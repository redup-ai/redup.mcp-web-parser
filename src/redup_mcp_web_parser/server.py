"""FastMCP server with web-parser tools."""

from __future__ import annotations

from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from redup_mcp_web_parser.binary_types import (
    url_extension,
    url_has_binary_extension,
    url_suggests_binary,
)
from redup_mcp_web_parser.config import ServerConfig
from redup_mcp_web_parser.crawl4ai_client import Crawl4AIClient
from redup_mcp_web_parser.errors import UpstreamError
from redup_mcp_web_parser.http_fetch import DirectHttpClient
from redup_mcp_web_parser.metrics import tracked_work
from redup_mcp_web_parser.output import (
    BINARY_PARSE_HINT,
    FetchBinaryResult,
    ParseResult,
)

_PageUrlArg = Annotated[
    str,
    Field(description="http(s) URL of an HTML page (not a PDF/ZIP/DOCX file)."),
]
_FileUrlArg = Annotated[
    str,
    Field(
        description=(
            "http(s) URL of a binary file to download "
            "(pdf, docx, zip, png, … — not an HTML page)."
        )
    ),
]
_TimeoutArg = Annotated[
    float,
    Field(description="Request timeout in seconds (server may clamp)."),
]


def create_server(config: ServerConfig) -> FastMCP:
    """Create and configure the MCP server with all tools."""
    mcp = FastMCP("redup-mcp-web-parser")
    crawl_client = Crawl4AIClient(config)
    http_client = DirectHttpClient(config)

    async def _reject_if_binary(page_url: str, timeout: float) -> ParseResult | None:
        try:
            probe = await http_client.probe_content_type(
                page_url, timeout_seconds=min(timeout, 30.0)
            )
        except UpstreamError:
            if url_has_binary_extension(page_url):
                kind = url_extension(page_url) or "bin"
                return ParseResult(
                    success=False,
                    url=page_url,
                    is_binary=True,
                    binary_kind=kind,
                    content_type="application/octet-stream",
                    error_message=BINARY_PARSE_HINT,
                    hint="Call fetch_binary on this URL.",
                    used_proxy=bool(config.default_proxy),
                )
            return None

        if not probe.get("is_binary"):
            return None
        return ParseResult(
            success=False,
            url=str(probe.get("url") or page_url),
            status_code=probe.get("status_code"),
            content_type=probe.get("content_type"),
            is_binary=True,
            binary_kind=str(probe.get("binary_kind") or ""),
            error_message=BINARY_PARSE_HINT,
            hint="Call fetch_binary on this URL.",
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
        url: _PageUrlArg,
        timeout: _TimeoutArg = config.request_timeout_seconds,
    ) -> str:
        """Read an HTML web page and return cleaned markdown as JSON.

        WHEN TO USE: HTML articles, docs, wiki, blog posts, /abs pages.
        WHEN NOT TO USE: PDF, DOCX, XLSX, ZIP, images, or other file downloads
        — call fetch_binary instead. If this tool returns is_binary=true, switch
        to fetch_binary (do not retry parse_page in a loop).

        Returns JSON fields: success, url, status_code, markdown, error_message,
        links_internal, links_external, truncated, used_proxy, content_type,
        is_binary, binary_kind, hint.
        """
        async with tracked_work("parse_page"):
            page_url = (url or "").strip()
            if not page_url.startswith(("http://", "https://")):
                return ParseResult(
                    success=False,
                    url=page_url,
                    error_message="url must be an absolute http(s) URL",
                ).to_json()

            binary_reject = await _reject_if_binary(page_url, timeout)
            if binary_reject is not None:
                return binary_reject.to_json()

            try:
                result = await crawl_client.parse_page(
                    page_url,
                    timeout_seconds=timeout,
                )
            except UpstreamError as exc:
                msg = str(exc)
                if url_suggests_binary(page_url) or "minimal_text" in msg.lower():
                    maybe = await _reject_if_binary(page_url, min(timeout, 30.0))
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
    async def fetch_binary(
        url: _FileUrlArg,
        timeout: _TimeoutArg = config.request_timeout_seconds,
    ) -> str:
        """Download a binary file and return metadata + base64 bytes as JSON.

        WHEN TO USE: the URL itself is a file download — pdf, docx/xlsx/pptx,
        odt/epub, zip/tar/gz/7z/rar, png/jpeg/gif/webp, or similar. Also use when
        parse_page returned is_binary=true / hint to call fetch_binary.
        WHEN NOT TO USE: normal HTML pages (example.com, wiki, /abs, blogs) —
        use parse_page. Never switch to this tool only because parse_page failed
        on an HTML URL (anti-bot, timeout, empty markdown).

        Download only: no text extraction, no OCR, no unzip. Bytes live only in
        JSON ``content_base64`` (last; may be large) — this server does not write
        a shared filesystem path. Downstream tools that need the bytes must use
        their own input contract. Prefer metadata fields kind, size, filename.

        Returns JSON: success, url, status_code, media_type, kind, size, filename,
        truncated, error_message, used_proxy, content_base64.
        """
        async with tracked_work("fetch_binary"):
            page_url = (url or "").strip()
            if not page_url.startswith(("http://", "https://")):
                return FetchBinaryResult(
                    success=False,
                    url=page_url,
                    error_message="url must be an absolute http(s) URL",
                ).to_json()
            try:
                result = await http_client.fetch_binary(
                    page_url, timeout_seconds=timeout
                )
            except UpstreamError as exc:
                return FetchBinaryResult(
                    success=False,
                    url=page_url,
                    status_code=exc.status_code,
                    error_message=str(exc)[:2000],
                    used_proxy=bool(config.default_proxy),
                ).to_json()
            return result.to_json()

    return mcp
