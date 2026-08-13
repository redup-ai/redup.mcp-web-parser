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

_PDF_URL_RE = re.compile(r"\.pdf(\?|#|$)", re.IGNORECASE)


def url_looks_like_pdf(url: str) -> bool:
    """Heuristic: path ends with .pdf or common /pdf/ document hosts."""
    raw = (url or "").strip()
    if not raw:
        return False
    if _PDF_URL_RE.search(raw):
        return True
    path = urlparse(raw).path.lower()
    # arxiv.org/pdf/<id>, similar patterns
    return "/pdf/" in path or path.endswith("/pdf")


def is_pdf_content_type(content_type: str | None) -> bool:
    ctype = (content_type or "").split(";")[0].strip().lower()
    return ctype == "application/pdf" or ctype.endswith("/pdf")


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
        """HEAD (fallback GET) to learn Content-Type without downloading a body."""
        timeout_s = self._config.clamp_timeout(timeout_seconds)
        proxy = _httpx_proxy(self._config.default_proxy)
        headers = {"User-Agent": "redup-mcp-web-parser/0.2"}
        try:
            async with httpx.AsyncClient(
                timeout=_client_timeout(timeout_s),
                follow_redirects=True,
                proxy=proxy,
            ) as client:
                resp = await client.head(url, headers=headers)
                # Some hosts reject HEAD or omit content-type.
                if resp.status_code >= 400 or not resp.headers.get("content-type"):
                    resp = await client.get(
                        url,
                        headers={**headers, "Range": "bytes=0-0"},
                    )
        except httpx.HTTPError as exc:
            raise UpstreamError(f"content-type probe failed: {exc}") from exc

        final_url = str(resp.url)
        ctype = resp.headers.get("content-type")
        return {
            "url": final_url,
            "status_code": resp.status_code,
            "content_type": ctype,
            "is_pdf": is_pdf_content_type(ctype) or url_looks_like_pdf(final_url),
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
            async with httpx.AsyncClient(
                timeout=_client_timeout(timeout_s),
                follow_redirects=True,
                proxy=proxy,
            ) as client:
                async with client.stream("GET", url, headers=headers) as resp:
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

                    # Early reject obvious non-PDF when type is present.
                    if ctype and not is_pdf_content_type(ctype) and not url_looks_like_pdf(
                        final_url
                    ):
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
                            # keep only up to max_bytes
                            remain = max_bytes - (total - len(chunk))
                            if remain > 0:
                                chunks.append(chunk[:remain])
                            break
                        chunks.append(chunk)
        except httpx.HTTPError as exc:
            logger.warning("pdf download failed url=%s err=%s", url, exc)
            raise UpstreamError(f"pdf download failed: {exc}") from exc

        data = b"".join(chunks)
        # Magic-byte check when content-type was missing/wrong.
        if not data.startswith(b"%PDF") and not is_pdf_content_type(ctype):
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
            content_base64=b64,
            truncated=truncated,
            error_message=err,
            used_proxy=used_proxy,
        )
