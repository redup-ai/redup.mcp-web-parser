"""FastMCP server with web-parser tools."""

from __future__ import annotations

import json
from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from redup_mcp_web_parser.config import ServerConfig
from redup_mcp_web_parser.crawl4ai_client import Crawl4AIClient
from redup_mcp_web_parser.errors import UpstreamError
from redup_mcp_web_parser.http_fetch import (
    DirectHttpClient,
    url_has_pdf_extension,
    url_suggests_pdf,
)
from redup_mcp_web_parser.metrics import tracked_work
from redup_mcp_web_parser.output import PDF_PARSE_HINT, FetchPdfResult, ParseResult

_UrlArg = Annotated[
    str,
    Field(description="Absolute http(s) URL of the page or PDF."),
]
_TimeoutArg = Annotated[
    float,
    Field(description="Max request time in seconds (clamped to server max)."),
]


def create_server(config: ServerConfig) -> FastMCP:
    """Create and configure the MCP server with all tools."""
    mcp = FastMCP("redup-mcp-web-parser")
    crawl_client = Crawl4AIClient(config)
    http_client = DirectHttpClient(config)

    async def _reject_if_pdf(page_url: str, timeout: float) -> ParseResult | None:
        """Return a clear PDF rejection payload, or None if not a hard PDF."""
        try:
            probe = await http_client.probe_content_type(
                page_url, timeout_seconds=min(timeout, 30.0)
            )
        except UpstreamError:
            # Without a probe, only a ``.pdf`` extension is a hard signal.
            if url_has_pdf_extension(page_url):
                return ParseResult(
                    success=False,
                    url=page_url,
                    is_pdf=True,
                    content_type="application/pdf",
                    error_message=PDF_PARSE_HINT,
                    hint="Use fetch_pdf to download this PDF.",
                    used_proxy=bool(config.default_proxy),
                )
            return None

        if not probe.get("is_pdf"):
            return None
        return ParseResult(
            success=False,
            url=str(probe.get("url") or page_url),
            status_code=probe.get("status_code"),
            content_type=probe.get("content_type") or "application/pdf",
            is_pdf=True,
            error_message=PDF_PARSE_HINT,
            hint="Use fetch_pdf to download this PDF.",
            used_proxy=bool(probe.get("used_proxy")),
        )

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
        """Fetch an HTML page via Crawl4AI and return cleaned markdown JSON.

        Does **not** parse PDF bodies. If the URL is a confirmed PDF
        (Content-Type / ``%PDF`` / ``.pdf``), returns ``is_pdf=true`` and
        tells you to call ``fetch_pdf`` (or an HTML mirror of the document
        when one exists).

        On failure: return the error — do not retry the same URL in a loop
        and do not call ``fetch_pdf`` as a fallback for HTML pages.

        Egress proxy (if any) comes from server config ``default_proxy``.

        Returns JSON: success, url, status_code, markdown, error_message,
        links_internal, links_external, truncated, used_proxy, content_type,
        is_pdf, hint.
        """
        async with tracked_work("parse_page"):
            page_url = (url or "").strip()
            if not page_url.startswith(("http://", "https://")):
                return ParseResult(
                    success=False,
                    url=page_url,
                    error_message="url must be an absolute http(s) URL",
                ).to_json()

            pdf_reject = await _reject_if_pdf(page_url, timeout)
            if pdf_reject is not None:
                return pdf_reject.to_json()

            try:
                result = await crawl_client.parse_page(
                    page_url,
                    timeout_seconds=timeout,
                )
            except UpstreamError as exc:
                msg = str(exc)
                if url_suggests_pdf(page_url) or "minimal_text" in msg.lower():
                    maybe = await _reject_if_pdf(page_url, min(timeout, 30.0))
                    if maybe is not None:
                        return maybe.to_json()
                return ParseResult(
                    success=False,
                    url=page_url,
                    status_code=exc.status_code,
                    error_message=msg[:2000],
                    used_proxy=bool(config.default_proxy),
                ).to_json()
            return result.to_json()

    @mcp.tool(
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        }
    )
    async def fetch_pdf(
        url: _UrlArg,
        timeout: _TimeoutArg = config.request_timeout_seconds,
    ) -> str:
        """Download a PDF via HTTP (server ``default_proxy`` if configured).

        Use only for real PDF URLs. Not a fallback when ``parse_page`` fails on
        HTML. Does not extract text — returns metadata plus ``content_base64``
        (placed last in the JSON object).

        Returns JSON: success, url, status_code, media_type, size, filename,
        truncated, error_message, used_proxy, content_base64.
        """
        async with tracked_work("fetch_pdf"):
            page_url = (url or "").strip()
            if not page_url.startswith(("http://", "https://")):
                return FetchPdfResult(
                    success=False,
                    url=page_url,
                    error_message="url must be an absolute http(s) URL",
                ).to_json()
            try:
                result = await http_client.fetch_pdf(
                    page_url, timeout_seconds=timeout
                )
            except UpstreamError as exc:
                return FetchPdfResult(
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
                payload = await crawl_client.check_health()
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
