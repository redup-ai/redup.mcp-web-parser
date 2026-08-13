"""Config and output unit tests."""

from __future__ import annotations

import pytest

from redup_mcp_web_parser.binary_types import (
    decide_binary,
    url_has_binary_extension,
    url_suggests_binary,
)
from redup_mcp_web_parser.config import ServerConfig
from redup_mcp_web_parser.errors import ConfigError
from redup_mcp_web_parser.output import (
    FetchBinaryResult,
    markdown_from_api_item,
    parse_crawl_api_response,
    truncate_markdown,
)


def test_config_requires_upstream():
    with pytest.raises(ConfigError, match="upstream_base_url"):
        ServerConfig(upstream_base_url="", require_upstream=True)


def test_config_from_servicekit_and_proxy():
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
    assert cfg.clamp_timeout(999) == cfg.max_timeout_seconds
    assert cfg.clamp_timeout(None) == cfg.request_timeout_seconds


def test_config_max_pdf_bytes_alias():
    cfg = ServerConfig.from_servicekit(
        {
            "service": {"host": "0.0.0.0", "port": 8000, "path": "/mcp"},
            "McpWebParser": {
                "upstream_base_url": "https://example.test/c4ai",
                "max_pdf_bytes": 2048,
            },
        }
    )
    assert cfg.max_binary_bytes == 2048


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


def test_binary_heuristics_and_decide():
    assert url_has_binary_extension("https://x.example/a.pdf")
    assert url_has_binary_extension("https://x.example/a.docx")
    assert not url_has_binary_extension("https://docs.example/pdf/2510.16927")
    assert url_suggests_binary("https://docs.example/pdf/2510.16927")

    ok, kind = decide_binary(
        url="https://docs.example/pdf/2510.16927",
        content_type="application/pdf",
    )
    assert ok and kind == "pdf"

    ok, kind = decide_binary(
        url="https://docs.example/pdf/img/table-word.pdf",
        content_type="text/html",
    )
    assert not ok

    ok, kind = decide_binary(
        url="https://example.com/file",
        content_type="text/html",
        body_prefix=b"%PDF-1.4",
    )
    assert ok and kind == "pdf"

    ok, kind = decide_binary(
        url="https://example.com/archive.zip",
        content_type=None,
        body_prefix=b"PK\x03\x04....",
    )
    assert ok and kind == "zip"

    ok, kind = decide_binary(
        url="https://example.com/paper.docx",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        body_prefix=b"PK\x03\x04....",
    )
    assert ok and kind == "docx"


def test_fetch_binary_result_json_puts_base64_last():
    raw = FetchBinaryResult(
        success=True,
        url="https://example.com/a.pdf",
        status_code=200,
        media_type="application/pdf",
        kind="pdf",
        size=3,
        filename="a.pdf",
        truncated=False,
        error_message="",
        used_proxy=True,
        content_base64="QUJD",
    ).to_json()
    assert raw.index('"used_proxy"') < raw.index('"content_base64"')
    assert raw.index('"kind"') < raw.index('"content_base64"')
    assert '"content_base64": "QUJD"' in raw
