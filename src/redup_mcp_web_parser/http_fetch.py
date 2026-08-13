"""HTTP helpers: content-type probe and PDF download (uses config default_proxy)."""

from __future__ import annotations

import base64
import logging
import re
from typing import Any
from urllib.parse import unquote, urlparse

import httpx

from redup_mcp_web_parser.config import ServerConfig
from redup_mcp_web_parser.errors import UpstreamError
from redup_mcp_web_parser.output import FetchPdfResult

logger = logging.getLogger(__name__)

_PDF_EXT_RE = re.compile(r"\.pdf(\?|#|$)", re.IGNORECASE)
_PDF_PATH_RE = re.compile(r"/pdf(/|$)", re.IGNORECASE)


def url_has_pdf_extension(url: str) -> bool:
    """True when the URL path ends with ``.pdf``."""
    return bool(_PDF_EXT_RE.search((url or "").strip()))


def url_suggests_pdf(url: str) -> bool:
    """Soft URL heuristic: ``.pdf`` extension or a ``/pdf/`` path segment.

    Soft only — not sufficient alone when Content-Type is a non-PDF type.
    """
    raw = (url or "").strip()
    if not raw:
        return False
    if url_has_pdf_extension(raw):
        return True
    return bool(_PDF_PATH_RE.search(urlparse(raw).path))


# Back-compat alias.
url_looks_like_pdf = url_suggests_pdf


def is_pdf_content_type(content_type: str | None) -> bool:
    ctype = (content_type or "").split(";")[0].strip().lower()
    return ctype == "application/pdf" or ctype.endswith("/pdf")


def is_clearly_non_pdf_content_type(content_type: str | None) -> bool:
    """True when Content-Type clearly is not a PDF body."""
    ctype = (content_type or "").split(";")[0].strip().lower()
    if not ctype or is_pdf_content_type(ctype):
        return False
    if ctype.startswith(("text/", "image/", "audio/", "video/", "font/")):
        return True
    if ctype in {
        "application/json",
        "application/javascript",
        "application/xml",
        "application/xhtml+xml",
        "application/atom+xml",
        "application/rss+xml",
    }:
        return True
    # application/octet-stream is ambiguous — not clearly non-PDF.
    return "html" in ctype or "xml" in ctype or "json" in ctype


def decide_is_pdf(
    *,
    url: str,
    content_type: str | None,
    body_prefix: bytes = b"",
) -> bool:
    """Hard PDF decision for parse_page reject / probe.

    Priority: ``%PDF`` magic → Content-Type → ``.pdf`` extension when type is
    missing/ambiguous. A bare ``/pdf/`` path is never enough if type is non-PDF.
    """
    if body_prefix.lstrip().startswith(b"%PDF"):
        return True
    if is_pdf_content_type(content_type):
        return True
    if is_clearly_non_pdf_content_type(content_type):
        return False
    # No decisive type: trust .pdf extension only (not /pdf/ alone).
    return url_has_pdf_extension(url)


def filename_from_response(url: str, headers: httpx.Headers) -> str:
    cd = headers.get("content-disposition") or ""
    match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', cd, flags=re.I)
    if match:
        return unquote(match.group(1).strip())
    path = urlparse(url).path
    name = unquote(path.rsplit("/", 1)[-1]) if path else ""
    if name and "." in name:
        return name
    return "document.pdf"


def _httpx_proxy(proxy: str) -> str | None:
    clean = (proxy or "").strip()
    return clean or None


def _client_timeout(timeout_s: float) -> httpx.Timeout:
    read_s = max(60.0, float(timeout_s))
    return httpx.Timeout(connect=30.0, read=read_s, write=60.0, pool=30.0)


class DirectHttpClient:
    """Direct fetches (optional egress proxy from server config)."""

    def __init__(self, config: ServerConfig):
        self._config = config

    async def probe_content_type(
        self,
        url: str,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        """HEAD / ranged GET to learn Content-Type and optional ``%PDF`` magic."""
        timeout_s = self._config.clamp_timeout(timeout_seconds)
        proxy = _httpx_proxy(self._config.default_proxy)
        headers = {"User-Agent": "redup-mcp-web-parser/0.2"}
        body_prefix = b""
        try:
            async with httpx.AsyncClient(
                timeout=_client_timeout(timeout_s),
                follow_redirects=True,
                proxy=proxy,
            ) as client:
                resp = await client.head(url, headers=headers)
                ctype = resp.headers.get("content-type")
                # Need body peek when HEAD lacks type, or URL soft-suggests PDF.
                need_peek = (
                    resp.status_code >= 400
                    or not ctype
                    or (
                        url_suggests_pdf(str(resp.url) or url)
                        and not is_pdf_content_type(ctype)
                    )
                )
                if need_peek:
                    resp = await client.get(
                        url,
                        headers={**headers, "Range": "bytes=0-15"},
                    )
                    body_prefix = resp.content or b""
        except httpx.HTTPError as exc:
            raise UpstreamError(f"content-type probe failed: {exc}") from exc

        final_url = str(resp.url)
        ctype = resp.headers.get("content-type")
        is_pdf = decide_is_pdf(
            url=final_url, content_type=ctype, body_prefix=body_prefix
        )
        return {
            "url": final_url,
            "status_code": resp.status_code,
            "content_type": ctype,
            "is_pdf": is_pdf,
            "url_suggests_pdf": url_suggests_pdf(final_url),
            "used_proxy": bool(proxy),
        }

    async def fetch_pdf(
        self,
        url: str,
        *,
        timeout_seconds: float | None = None,
    ) -> FetchPdfResult:
        """Download a PDF (or fail clearly if response is not a PDF)."""
        timeout_s = self._config.clamp_timeout(timeout_seconds)
        proxy = _httpx_proxy(self._config.default_proxy)
        max_bytes = int(self._config.max_pdf_bytes)
        headers = {"User-Agent": "redup-mcp-web-parser/0.2"}
        used_proxy = bool(proxy)

        try:
            async with (
                httpx.AsyncClient(
                    timeout=_client_timeout(timeout_s),
                    follow_redirects=True,
                    proxy=proxy,
                ) as client,
                client.stream("GET", url, headers=headers) as resp,
            ):
                status = resp.status_code
                final_url = str(resp.url)
                ctype = resp.headers.get("content-type")
                filename = filename_from_response(final_url, resp.headers)

                if status >= 400:
                    return FetchPdfResult(
                        success=False,
                        url=final_url,
                        status_code=status,
                        media_type=ctype,
                        filename=filename,
                        error_message=f"HTTP {status} while downloading",
                        used_proxy=used_proxy,
                    )

                # Prefer Content-Type over URL path heuristics.
                if ctype and is_clearly_non_pdf_content_type(ctype):
                    return FetchPdfResult(
                        success=False,
                        url=final_url,
                        status_code=status,
                        media_type=ctype,
                        filename=filename,
                        error_message=(
                            f"URL did not return a PDF (Content-Type: {ctype}). "
                            "Use parse_page for HTML pages."
                        ),
                        used_proxy=used_proxy,
                    )

                chunks: list[bytes] = []
                total = 0
                truncated = False
                async for chunk in resp.aiter_bytes():
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > max_bytes:
                        truncated = True
                        remain = max_bytes - (total - len(chunk))
                        if remain > 0:
                            chunks.append(chunk[:remain])
                        break
                    chunks.append(chunk)
        except httpx.HTTPError as exc:
            logger.warning("pdf download failed url=%s err=%s", url, exc)
            raise UpstreamError(f"pdf download failed: {exc}") from exc

        data = b"".join(chunks)
        if not data.lstrip().startswith(b"%PDF"):
            return FetchPdfResult(
                success=False,
                url=final_url,
                status_code=status,
                media_type=ctype,
                size=len(data),
                filename=filename,
                error_message=(
                    "Downloaded body is not a PDF (missing %PDF header). "
                    "Use parse_page for HTML pages."
                ),
                used_proxy=used_proxy,
            )

        b64 = base64.b64encode(data).decode("ascii")
        err = ""
        if truncated:
            err = (
                f"PDF truncated to max_pdf_bytes={max_bytes}. "
                "Increase McpWebParser.max_pdf_bytes or fetch a smaller file."
            )
        return FetchPdfResult(
            success=True,
            url=final_url,
            status_code=status,
            media_type=ctype or "application/pdf",
            size=len(data),
            filename=filename,
            truncated=truncated,
            error_message=err,
            used_proxy=used_proxy,
            content_base64=b64,
        )
