"""Config and output unit tests."""

from __future__ import annotations

import pytest

from redup_mcp_web_parser.config import ServerConfig
from redup_mcp_web_parser.errors import ConfigError
from redup_mcp_web_parser.output import (
    markdown_from_api_item,
    parse_crawl_api_response,
    truncate_markdown,
)


def test_config_requires_upstream():
    with pytest.raises(ConfigError, match="upstream_base_url"):
        ServerConfig(upstream_base_url="", require_upstream=True)


def test_config_from_servicekit_and_proxy_override():
    cfg = ServerConfig.from_servicekit(
        {
            "service": {"host": "0.0.0.0", "port": 8000, "path": "/mcp"},
            "McpWebParser": {
                "upstream_base_url": "https://example.test/c4ai/",
                "default_proxy": "http://proxy.example:3128",
                "max_markdown_chars": 5000,
            },
        }
    )
    assert cfg.upstream_base_url == "https://example.test/c4ai"
    assert cfg.default_proxy == "http://proxy.example:3128"
    assert cfg.effective_proxy("") == "http://proxy.example:3128"
    assert cfg.effective_proxy("  http://other:1  ") == "http://other:1"
    assert cfg.clamp_timeout(999) == cfg.max_timeout_seconds
    assert cfg.clamp_timeout(None) == cfg.request_timeout_seconds


def test_markdown_and_truncate():
    assert (
        markdown_from_api_item({"markdown": {"raw_markdown": "A", "fit_markdown": "B"}})
        == "A"
    )
    assert markdown_from_api_item({"markdown": "plain"}) == "plain"
    text, truncated = truncate_markdown("hello world", 5)
    assert truncated is True
    assert text.startswith("hello")
    assert "truncated" in text


def test_parse_crawl_api_response_success():
    data = {
        "success": True,
        "results": [
            {
                "success": True,
                "url": "https://example.com/",
                "status_code": 200,
                "markdown": {"raw_markdown": "Hello"},
                "links": {"internal": [{"href": "/a"}], "external": []},
                "error_message": "",
            }
        ],
    }
    result = parse_crawl_api_response(
        data,
        request_url="https://example.com/",
        max_markdown_chars=100,
        used_proxy=True,
    )
    assert result.success is True
    assert result.markdown == "Hello"
    assert result.used_proxy is True
    assert result.links_internal == [{"href": "/a"}]


def test_parse_crawl_api_response_empty():
    result = parse_crawl_api_response(
        {"success": True, "results": []},
        request_url="https://example.com/",
        max_markdown_chars=100,
        used_proxy=False,
    )
    assert result.success is False
    assert result.error_message == "empty_results"
