"""HTTP helpers: content-type probe and binary download (config default_proxy)."""

from __future__ import annotations

import base64
import logging
import re
from typing import Any
from urllib.parse import unquote, urlparse

import httpx

from redup_mcp_web_parser.binary_types import (
    decide_binary,
    is_clearly_textual_content_type,
    url_suggests_binary,
)
from redup_mcp_web_parser.config import ServerConfig
from redup_mcp_web_parser.errors import UpstreamError
from redup_mcp_web_parser.output import FetchBinaryResult

logger = logging.getLogger(__name__)


def filename_from_response(
    url: str,
    headers: httpx.Headers,
    *,
    kind: str = "",
) -> str:
    cd = headers.get("content-disposition") or ""
    match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', cd, flags=re.I)
    if match:
        return unquote(match.group(1).strip())
    path = urlparse(url).path
    name = unquote(path.rsplit("/", 1)[-1]) if path else ""
    if name and "." in name:
        return name
    ext = {
        "gzip": "gz",
        "bzip2": "bz2",
        "jpeg": "jpg",
    }.get(kind, kind or "bin")
    return f"download.{ext}"


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
        """HEAD / ranged GET for Content-Type and magic-byte peek."""
        timeout_s = self._config.clamp_timeout(timeout_seconds)
        proxy = _httpx_proxy(self._config.default_proxy)
        headers = {"User-Agent": "redup-mcp-web-parser/0.3"}
        body_prefix = b""
        try:
            async with httpx.AsyncClient(
                timeout=_client_timeout(timeout_s),
                follow_redirects=True,
                proxy=proxy,
            ) as client:
                resp = await client.head(url, headers=headers)
                ctype = resp.headers.get("content-type")
                final_guess = str(resp.url) or url
                need_peek = (
                    resp.status_code >= 400
                    or not ctype
                    or url_suggests_binary(final_guess)
                )
                if need_peek:
                    resp = await client.get(
                        url,
                        headers={**headers, "Range": "bytes=0-511"},
                    )
                    body_prefix = resp.content or b""
        except httpx.HTTPError as exc:
            raise UpstreamError(f"content-type probe failed: {exc}") from exc

        final_url = str(resp.url)
        ctype = resp.headers.get("content-type")
        is_bin, kind = decide_binary(
            url=final_url, content_type=ctype, body_prefix=body_prefix
        )
        return {
            "url": final_url,
            "status_code": resp.status_code,
            "content_type": ctype,
            "is_binary": is_bin,
            "binary_kind": kind,
            "url_suggests_binary": url_suggests_binary(final_url),
            "used_proxy": bool(proxy),
        }

    async def fetch_binary(
        self,
        url: str,
        *,
        timeout_seconds: float | None = None,
    ) -> FetchBinaryResult:
        """Download a binary file (or fail clearly if body looks like HTML/text)."""
        timeout_s = self._config.clamp_timeout(timeout_seconds)
        proxy = _httpx_proxy(self._config.default_proxy)
        max_bytes = int(self._config.max_binary_bytes)
        headers = {"User-Agent": "redup-mcp-web-parser/0.3"}
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
                resp_headers = resp.headers
                filename = filename_from_response(final_url, resp_headers)

                if status >= 400:
                    return FetchBinaryResult(
                        success=False,
                        url=final_url,
                        status_code=status,
                        media_type=ctype,
                        filename=filename,
                        error_message=f"HTTP {status} while downloading",
                        used_proxy=used_proxy,
                    )

                if ctype and is_clearly_textual_content_type(ctype):
                    return FetchBinaryResult(
                        success=False,
                        url=final_url,
                        status_code=status,
                        media_type=ctype,
                        filename=filename,
                        error_message=(
                            f"URL did not return a binary file (Content-Type: {ctype}). "
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
            logger.warning("binary download failed url=%s err=%s", url, exc)
            raise UpstreamError(f"binary download failed: {exc}") from exc

        data = b"".join(chunks)
        is_bin, kind = decide_binary(
            url=final_url, content_type=ctype, body_prefix=data[:512]
        )
        if not is_bin:
            return FetchBinaryResult(
                success=False,
                url=final_url,
                status_code=status,
                media_type=ctype,
                size=len(data),
                filename=filename,
                error_message=(
                    "Downloaded body does not look like a supported binary. "
                    "Use parse_page for HTML pages."
                ),
                used_proxy=used_proxy,
            )

        filename = filename_from_response(final_url, resp_headers, kind=kind)
        b64 = base64.b64encode(data).decode("ascii")
        err = ""
        if truncated:
            err = (
                f"File truncated to max_binary_bytes={max_bytes}. "
                "Increase McpWebParser.max_binary_bytes or fetch a smaller file."
            )
        return FetchBinaryResult(
            success=True,
            url=final_url,
            status_code=status,
            media_type=ctype or "application/octet-stream",
            kind=kind,
            size=len(data),
            filename=filename,
            truncated=truncated,
            error_message=err,
            used_proxy=used_proxy,
            content_base64=b64,
        )
