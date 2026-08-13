"""Normalize and truncate Crawl4AI parse results for MCP tools."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ParseResult:
    """Agent-facing parse payload."""

    success: bool
    url: str
    status_code: int | None = None
    markdown: str = ""
    error_message: str = ""
    links_internal: list[Any] = field(default_factory=list)
    links_external: list[Any] = field(default_factory=list)
    truncated: bool = False
    used_proxy: bool = False
    content_type: str | None = None
    is_pdf: bool = False
    hint: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


@dataclass
class FetchPdfResult:
    """Agent-facing PDF download payload."""

    success: bool
    url: str
    status_code: int | None = None
    media_type: str | None = None
    size: int = 0
    filename: str = ""
    content_base64: str = ""
    truncated: bool = False
    error_message: str = ""
    used_proxy: bool = False

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


PDF_PARSE_HINT = (
    "This URL is a PDF. parse_page only extracts HTML pages via Crawl4AI. "
    "Call fetch_pdf to download the file (base64), or open an HTML version "
    "of the document if available (e.g. arXiv /abs or /html)."
)


def markdown_from_api_item(item: dict[str, Any]) -> str:
    """Extract markdown from a Crawl4AI result item (Airflow parity)."""
    md = item.get("markdown")
    if isinstance(md, dict):
        return str(
            md.get("raw_markdown")
            or md.get("markdown_with_citations")
            or md.get("fit_markdown")
            or ""
        )
    if isinstance(md, str):
        return md
    return ""


def truncate_markdown(text: str, max_chars: int) -> tuple[str, bool]:
    """Truncate markdown; return (text, truncated)."""
    if max_chars < 1 or len(text) <= max_chars:
        return text, False
    return text[:max_chars] + "\n\n…[truncated]", True


def parse_crawl_api_response(
    data: dict[str, Any],
    *,
    request_url: str,
    max_markdown_chars: int,
    used_proxy: bool,
) -> ParseResult:
    """Normalize a Crawl4AI ``/crawl`` JSON body into ``ParseResult``."""
    if not data.get("success", True):
        detail = data.get("detail")
        if isinstance(detail, str):
            err = detail
        elif detail is not None:
            err = json.dumps(detail, ensure_ascii=False)
        else:
            err = "api_success_false"
        return ParseResult(
            success=False,
            url=request_url,
            error_message=err[:2000],
            used_proxy=used_proxy,
        )

    results = data.get("results") or []
    if not results:
        return ParseResult(
            success=False,
            url=request_url,
            error_message="empty_results",
            used_proxy=used_proxy,
        )

    item = results[0] if isinstance(results[0], dict) else {}
    links = item.get("links") or {}
    if not isinstance(links, dict):
        links = {}
    markdown, truncated = truncate_markdown(
        markdown_from_api_item(item),
        max_markdown_chars,
    )
    status = item.get("status_code")
    status_code = int(status) if status is not None else None
    return ParseResult(
        success=bool(item.get("success")),
        url=str(item.get("url") or request_url),
        status_code=status_code,
        markdown=markdown,
        error_message=(item.get("error_message") or "").strip()[:2000],
        links_internal=list(links.get("internal") or []),
        links_external=list(links.get("external") or []),
        truncated=truncated,
        used_proxy=used_proxy,
    )
