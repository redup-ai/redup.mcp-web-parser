"""Async HTTP client for Crawl4AI 0.8.x (Airflow-parity /crawl + proxy_config)."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from redup_mcp_web_parser.config import ServerConfig
from redup_mcp_web_parser.errors import UpstreamError
from redup_mcp_web_parser.output import ParseResult, parse_crawl_api_response

logger = logging.getLogger(__name__)


def build_crawl_payload(
    page_url: str,
    *,
    proxy: str = "",
    delay_before_return_html: float = 2.5,
    page_timeout_ms: int = 120_000,
) -> dict[str, Any]:
    """Build Crawl4AI ``POST /crawl`` body (single URL)."""
    crawler_config: dict[str, Any] = {
        "cache_mode": "bypass",
        "delay_before_return_html": float(delay_before_return_html),
        "wait_until": "domcontentloaded",
        "verbose": False,
        "log_console": False,
        "page_timeout": int(page_timeout_ms),
        "exclude_all_images": True,
        "screenshot": False,
        "pdf": False,
    }
    clean_proxy = (proxy or "").strip()
    if clean_proxy:
        crawler_config["proxy_config"] = {"server": clean_proxy}

    return {
        "urls": [page_url],
        "browser_config": {
            "browser_type": "chromium",
            "headless": True,
            "ignore_https_errors": True,
            "verbose": False,
        },
        "crawler_config": crawler_config,
    }


def crawl_endpoint(base_url: str) -> str:
    api = (base_url or "").strip().rstrip("/")
    if api.endswith("/crawl"):
        return api
    return f"{api}/crawl"


def _auth_headers(token: str) -> dict[str, str]:
    auth = (token or "").strip()
    if not auth:
        return {}
    if auth.lower().startswith(("bearer ", "basic ")):
        return {"Authorization": auth}
    return {"Authorization": f"Bearer {auth}"}


class Crawl4AIClient:
    """Thin async client for Crawl4AI HTTP API."""

    def __init__(self, config: ServerConfig):
        self._config = config

    async def parse_page(
        self,
        page_url: str,
        *,
        timeout_seconds: float | None = None,
    ) -> ParseResult:
        timeout_s = self._config.clamp_timeout(timeout_seconds)
        page_timeout_ms = int(timeout_s * 1000)
        # HTTP read timeout slightly above page_timeout (Airflow-style headroom).
        read_s = max(120.0, timeout_s + 30.0)
        http_timeout = httpx.Timeout(connect=60.0, read=read_s, write=60.0, pool=60.0)
        proxy = self._config.default_proxy
        payload = build_crawl_payload(
            page_url,
            proxy=proxy,
            delay_before_return_html=self._config.delay_before_return_html,
            page_timeout_ms=page_timeout_ms,
        )
        url = crawl_endpoint(self._config.upstream_base_url)
        headers = {
            "Content-Type": "application/json",
            **_auth_headers(self._config.upstream_token),
        }
        used_proxy = bool(proxy)

        try:
            async with httpx.AsyncClient(timeout=http_timeout, follow_redirects=True) as client:
                resp = await client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            logger.warning("crawl4ai request failed url=%s err=%s", page_url, exc)
            raise UpstreamError(f"upstream crawl request failed: {exc}") from exc

        if resp.status_code >= 400:
            detail = (resp.text or "")[:800]
            raise UpstreamError(
                f"upstream crawl HTTP {resp.status_code}: {detail}",
                status_code=resp.status_code,
            )

        try:
            data = resp.json()
        except ValueError as exc:
            raise UpstreamError("upstream crawl returned non-JSON body") from exc

        if not isinstance(data, dict):
            raise UpstreamError("upstream crawl returned unexpected JSON type")

        return parse_crawl_api_response(
            data,
            request_url=page_url,
            max_markdown_chars=self._config.max_markdown_chars,
            used_proxy=used_proxy,
        )
