"""Server configuration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from redup_mcp_web_parser.errors import ConfigError


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class ServerConfig:
    """Runtime configuration for the MCP web parser."""

    upstream_base_url: str = ""
    upstream_token: str = ""
    default_proxy: str = ""
    request_timeout_seconds: float = 120.0
    max_timeout_seconds: float = 300.0
    max_markdown_chars: int = 100_000
    max_pdf_bytes: int = 15 * 1024 * 1024
    delay_before_return_html: float = 2.5
    transport: str = "streamable-http"
    host: str = "0.0.0.0"
    port: int = 8000
    path: str = "/mcp"
    json_response: bool = True
    stateless_http: bool = True
    require_upstream: bool = True

    def __post_init__(self) -> None:
        self.upstream_base_url = (self.upstream_base_url or "").strip().rstrip("/")
        self.upstream_token = (self.upstream_token or "").strip()
        self.default_proxy = (self.default_proxy or "").strip()
        if self.require_upstream and not self.upstream_base_url:
            raise ConfigError(
                "McpWebParser.upstream_base_url is required "
                "(set via config or McpWebParser___upstream_base_url)"
            )
        if self.upstream_base_url and not self.upstream_base_url.startswith(
            ("http://", "https://")
        ):
            raise ConfigError("upstream_base_url must be an absolute http(s) URL")
        if self.request_timeout_seconds < 1:
            raise ConfigError("request_timeout_seconds must be >= 1")
        if self.max_timeout_seconds < 1:
            raise ConfigError("max_timeout_seconds must be >= 1")
        if self.request_timeout_seconds > self.max_timeout_seconds:
            raise ConfigError("request_timeout_seconds must be <= max_timeout_seconds")
        if self.max_markdown_chars < 1024:
            raise ConfigError("max_markdown_chars must be >= 1024")
        if self.max_pdf_bytes < 1024:
            raise ConfigError("max_pdf_bytes must be >= 1024")
        if self.delay_before_return_html < 0:
            raise ConfigError("delay_before_return_html must be >= 0")
        if self.port < 1 or self.port > 65535:
            raise ConfigError("port must be between 1 and 65535")
        if not self.path.startswith("/"):
            raise ConfigError("path must start with '/'")

    def clamp_timeout(self, timeout: float | None) -> float:
        """Clamp a tool timeout into [1, max_timeout_seconds]."""
        if timeout is None:
            return float(self.request_timeout_seconds)
        return max(1.0, min(float(timeout), float(self.max_timeout_seconds)))

    @classmethod
    def from_servicekit(cls, config: Mapping[str, Any]) -> ServerConfig:
        """Build from a servicekit YAML dict (`service` + `McpWebParser`)."""
        service = config.get("service") or {}
        section = config.get("McpWebParser") or {}
        return cls(
            upstream_base_url=str(section.get("upstream_base_url", "") or ""),
            upstream_token=str(section.get("upstream_token", "") or ""),
            default_proxy=str(section.get("default_proxy", "") or ""),
            request_timeout_seconds=float(section.get("request_timeout_seconds", 120)),
            max_timeout_seconds=float(section.get("max_timeout_seconds", 300)),
            max_markdown_chars=int(section.get("max_markdown_chars", 100_000)),
            max_pdf_bytes=int(section.get("max_pdf_bytes", 15 * 1024 * 1024)),
            delay_before_return_html=float(section.get("delay_before_return_html", 2.5)),
            transport="streamable-http",
            host=str(service.get("host", "0.0.0.0")),
            port=int(service.get("port", 8000)),
            path=str(service.get("path", "/mcp")),
            json_response=_as_bool(section.get("json_response"), True),
            stateless_http=_as_bool(section.get("stateless_http"), True),
            require_upstream=True,
        )
