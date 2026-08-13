"""FastMCP server with web-parser tools."""

from __future__ import annotations

import json
from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from redup_mcp_web_parser.config import ServerConfig
from redup_mcp_web_parser.crawl4ai_client import Crawl4AIClient
from redup_mcp_web_parser.errors import UpstreamError
from redup_mcp_web_parser.metrics import tracked_work
from redup_mcp_web_parser.output import ParseResult

_UrlArg = Annotated[
    str,
    Field(description="Absolute http(s) URL of the page to parse."),
]
_TimeoutArg = Annotated[
    float,
    Field(description="Max crawl time in seconds (clamped to server max)."),
]


def create_server(config: ServerConfig) -> FastMCP:
    """Create and configure the MCP server with all tools."""
    mcp = FastMCP("redup-mcp-web-parser")
    client = Crawl4AIClient(config)

    @mcp.tool(
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        }
    )
    async def parse_page(
        url: _UrlArg,
        timeout: _TimeoutArg = config.request_timeout_seconds,
    ) -> str:
        """Fetch a web page via Crawl4AI and return cleaned markdown JSON.

        Uses Crawl4AI ``POST /crawl`` (0.8.x). Egress proxy (if any) comes from
        server config ``default_proxy``, not from tool arguments.

        Returns JSON: success, url, status_code, markdown, error_message,
        links_internal, links_external, truncated, used_proxy.
        """
        async with tracked_work("parse_page"):
            page_url = (url or "").strip()
            if not page_url.startswith(("http://", "https://")):
                return ParseResult(
                    success=False,
                    url=page_url,
                    error_message="url must be an absolute http(s) URL",
                ).to_json()

            try:
                result = await client.parse_page(
                    page_url,
                    timeout_seconds=timeout,
                )
            except UpstreamError as exc:
                return ParseResult(
                    success=False,
                    url=page_url,
                    status_code=exc.status_code,
                    error_message=str(exc)[:2000],
                    used_proxy=bool(config.default_proxy),
                ).to_json()
            return result.to_json()

    @mcp.tool(
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }
    )
    async def check_upstream() -> str:
        """Check Crawl4AI upstream ``GET /health`` (no secrets in the response)."""
        async with tracked_work("check_upstream"):
            try:
                payload = await client.check_health()
            except UpstreamError as exc:
                return json.dumps(
                    {
                        "ok": False,
                        "error": str(exc)[:500],
                        "status_code": exc.status_code,
                    },
                    ensure_ascii=False,
                )
            return json.dumps(payload, ensure_ascii=False)

    return mcp
