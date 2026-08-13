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
async def test_client_parse_page_uses_config_proxy(
    monkeypatch: pytest.MonkeyPatch, config: ServerConfig
):
    config.default_proxy = "http://proxy.example:3128"
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
    result = await client.parse_page("https://example.com/", timeout_seconds=10)
    assert result.success is True
    assert result.markdown == "# Hi"
    assert result.used_proxy is True
    assert captured["url"].endswith("/crawl")
    assert captured["body"]["crawler_config"]["proxy_config"]["server"] == (
        "http://proxy.example:3128"
    )


@pytest.mark.asyncio
async def test_parse_page_tool_schema_has_no_proxy_arg(
    monkeypatch: pytest.MonkeyPatch, config: ServerConfig
):
    config.default_proxy = "http://default-proxy:3128"
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        host = request.url.host
        if host == "example.com" and request.method in {"HEAD", "GET"}:
            return httpx.Response(
                200,
                headers={"content-type": "text/html; charset=utf-8"},
                content=b"<html></html>" if request.method == "GET" else b"",
            )
        if path.endswith("/crawl"):
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
    assert "fetch_pdf" in tools
    schema = tools["parse_page"].parameters
    props = (schema or {}).get("properties") or {}
    assert "proxy" not in props
    assert "url" in props
    assert "proxy" not in ((tools["fetch_pdf"].parameters or {}).get("properties") or {})

    out = await tools["parse_page"].run({"url": "https://example.com/"})
    text = _tool_text(out)
    payload = json.loads(text)
    assert captured["body"]["crawler_config"]["proxy_config"]["server"] == (
        "http://default-proxy:3128"
    )
    assert payload.get("success") is True
    assert payload.get("used_proxy") is True


@pytest.mark.asyncio
async def test_parse_page_rejects_pdf_clearly(
    monkeypatch: pytest.MonkeyPatch, config: ServerConfig
):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method in {"HEAD", "GET"}:
            return httpx.Response(
                200,
                headers={"content-type": "application/pdf"},
                content=b"%PDF-1.4" if request.method == "GET" else b"",
            )
        return httpx.Response(500, text="should not crawl")

    transport = _Transport(handler)
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)

    server = create_server(config)
    tools = await server.get_tools()
    out = await tools["parse_page"].run(
        {"url": "https://example.com/papers/2510.16927.pdf"}
    )
    payload = json.loads(_tool_text(out))
    assert payload["success"] is False
    assert payload["is_pdf"] is True
    assert "fetch_pdf" in payload["error_message"]
    assert payload["hint"]


@pytest.mark.asyncio
async def test_fetch_pdf_returns_base64(
    monkeypatch: pytest.MonkeyPatch, config: ServerConfig
):
    pdf_bytes = b"%PDF-1.4\n%fake\n"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(
            200,
            headers={
                "content-type": "application/pdf",
                "content-disposition": 'attachment; filename="paper.pdf"',
            },
            content=pdf_bytes,
        )

    transport = _Transport(handler)
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)

    server = create_server(config)
    tools = await server.get_tools()
    out = await tools["fetch_pdf"].run(
        {"url": "https://example.com/docs/paper.pdf"}
    )
    payload = json.loads(_tool_text(out))
    assert payload["success"] is True
    assert payload["filename"] == "paper.pdf"
    assert payload["size"] == len(pdf_bytes)
    assert payload["content_base64"]
    # Metadata fields should appear before content_base64 in the JSON object.
    keys = list(payload.keys())
    assert keys[-1] == "content_base64"
    assert keys.index("used_proxy") < keys.index("content_base64")
    assert keys.index("filename") < keys.index("content_base64")


@pytest.mark.asyncio
async def test_parse_page_does_not_hard_reject_pdf_path_with_html(
    monkeypatch: pytest.MonkeyPatch, config: ServerConfig
):
    """``/pdf/`` path + ``text/html`` must not set ``is_pdf``."""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method in {"HEAD", "GET"} and "example.com" in str(request.url):
            return httpx.Response(
                200,
                headers={"content-type": "text/html; charset=utf-8"},
                content=b"<html>choices</html>",
            )
        if request.url.path.endswith("/crawl"):
            captured["crawled"] = True
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "results": [
                        {
                            "success": True,
                            "url": str(request.url),
                            "status_code": 200,
                            "markdown": {"raw_markdown": "# choices"},
                            "links": {},
                            "error_message": "",
                        }
                    ],
                },
            )
        return httpx.Response(404)

    transport = _Transport(handler)
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)

    server = create_server(config)
    tools = await server.get_tools()
    out = await tools["parse_page"].run(
        {"url": "https://example.com/docs/pdf/img/table-word.pdf"}
    )
    payload = json.loads(_tool_text(out))
    assert payload.get("is_pdf") is not True
    assert captured.get("crawled") is True
    assert payload.get("success") is True


@pytest.mark.asyncio
async def test_fetch_pdf_rejects_html_content_type(
    monkeypatch: pytest.MonkeyPatch, config: ServerConfig
):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            300,
            headers={"content-type": "text/html"},
            content=b"<html>not a pdf</html>",
        )

    transport = _Transport(handler)
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)

    server = create_server(config)
    tools = await server.get_tools()
    out = await tools["fetch_pdf"].run(
        {"url": "https://example.com/docs/pdf/thing"}
    )
    payload = json.loads(_tool_text(out))
    assert payload["success"] is False
    assert "Content-Type" in payload["error_message"]
    assert not payload.get("content_base64")
