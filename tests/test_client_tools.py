"""Crawl4AI client and MCP tool tests with mocked HTTP."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from redup_mcp_web_parser.config import ServerConfig
from redup_mcp_web_parser.crawl4ai_client import (
    Crawl4AIClient,
    build_crawl_payload,
    crawl_endpoint,
)
from redup_mcp_web_parser.server import create_server


def _tool_text(out) -> str:
    if hasattr(out, "content"):
        blobs = []
        for c in out.content or []:
            blobs.append(getattr(c, "text", None) or str(c))
        return "\n".join(blobs)
    if isinstance(out, str):
        return out
    return str(out)


def test_build_crawl_payload_proxy_on_off():
    bare = build_crawl_payload("https://example.com/")
    assert "proxy_config" not in bare["crawler_config"]
    assert bare["urls"] == ["https://example.com/"]

    with_proxy = build_crawl_payload(
        "https://example.com/",
        proxy="http://user:pass@proxy.example:3128",
    )
    assert with_proxy["crawler_config"]["proxy_config"] == {
        "server": "http://user:pass@proxy.example:3128"
    }
    assert crawl_endpoint("http://host:11235/") == "http://host:11235/crawl"
    assert crawl_endpoint("http://host:11235/crawl") == "http://host:11235/crawl"


class _Transport(httpx.AsyncBaseTransport):
    def __init__(self, handler):
        self._handler = handler

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return self._handler(request)


@pytest.mark.asyncio
async def test_client_parse_page_posts_proxy(
    monkeypatch: pytest.MonkeyPatch, config: ServerConfig
):
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={
                "success": True,
                "results": [
                    {
                        "success": True,
                        "url": "https://example.com/",
                        "status_code": 200,
                        "markdown": {"raw_markdown": "# Hi"},
                        "links": {"internal": [], "external": []},
                        "error_message": "",
                    }
                ],
            },
        )

    transport = _Transport(handler)
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        kwargs.setdefault("base_url", "http://crawl4ai.test:11235")
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)

    client = Crawl4AIClient(config)
    result = await client.parse_page(
        "https://example.com/",
        proxy="http://proxy.example:3128",
        timeout_seconds=10,
    )
    assert result.success is True
    assert result.markdown == "# Hi"
    assert result.used_proxy is True
    assert captured["url"].endswith("/crawl")
    assert captured["body"]["crawler_config"]["proxy_config"]["server"] == (
        "http://proxy.example:3128"
    )


@pytest.mark.asyncio
async def test_parse_page_tool_uses_default_proxy(
    monkeypatch: pytest.MonkeyPatch, config: ServerConfig
):
    config.default_proxy = "http://default-proxy:3128"
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/crawl"):
            captured["body"] = json.loads(request.content.decode())
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "results": [
                        {
                            "success": True,
                            "url": "https://example.com/",
                            "status_code": 200,
                            "markdown": {"raw_markdown": "ok"},
                            "links": {},
                            "error_message": "",
                        }
                    ],
                },
            )
        return httpx.Response(404, json={"detail": "not found"})

    transport = _Transport(handler)
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)

    server = create_server(config)
    tools = await server.get_tools()
    assert "parse_page" in tools
    assert "check_upstream" in tools
    out = await tools["parse_page"].run({"url": "https://example.com/"})
    text = _tool_text(out)
    payload = json.loads(text)
    assert captured["body"]["crawler_config"]["proxy_config"]["server"] == (
        "http://default-proxy:3128"
    )
    assert payload.get("success") is True
    assert payload.get("used_proxy") is True
