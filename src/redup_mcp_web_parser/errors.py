"""Domain errors for the web-parser MCP service."""

from __future__ import annotations


class WebParserError(Exception):
    """Base error for this service."""


class ConfigError(WebParserError):
    """Invalid or incomplete server configuration."""


class UpstreamError(WebParserError):
    """Crawl4AI upstream call failed."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code
