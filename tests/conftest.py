"""Shared fixtures."""

from __future__ import annotations

import pytest

from redup_mcp_web_parser.config import ServerConfig


@pytest.fixture
def config() -> ServerConfig:
    return ServerConfig(
        upstream_base_url="http://crawl4ai.test:11235",
        upstream_token="",
        default_proxy="",
        request_timeout_seconds=30,
        max_timeout_seconds=60,
        max_markdown_chars=10_000,
        delay_before_return_html=0.5,
        require_upstream=True,
    )
