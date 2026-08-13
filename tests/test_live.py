"""Optional live smoke against a real Crawl4AI (not run in default CI)."""

from __future__ import annotations

import os

import pytest

from redup_mcp_web_parser.config import ServerConfig
from redup_mcp_web_parser.crawl4ai_client import Crawl4AIClient

# servicekit-style keys (Section___key); noqa: SIM112 — intentional casing
_UPSTREAM = (
    os.environ.get("McpWebParser___upstream_base_url")  # noqa: SIM112
    or os.environ.get("CRAWL4AI_BASE_URL")
    or ""
).strip()


@pytest.mark.live
@pytest.mark.skipif(not _UPSTREAM, reason="McpWebParser___upstream_base_url not set")
@pytest.mark.asyncio
async def test_live_parse():
    config = ServerConfig(
        upstream_base_url=_UPSTREAM,
        default_proxy=(
            os.environ.get("McpWebParser___default_proxy") or ""  # noqa: SIM112
        ).strip(),
        request_timeout_seconds=60,
        max_timeout_seconds=120,
        max_markdown_chars=50_000,
        delay_before_return_html=1.0,
    )
    client = Crawl4AIClient(config)
    result = await client.parse_page("https://example.com/")
    assert result.success is True
    assert "Example" in result.markdown or len(result.markdown) > 0
