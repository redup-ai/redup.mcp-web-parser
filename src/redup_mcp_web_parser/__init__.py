"""MCP Streamable HTTP service that parses web pages via Crawl4AI."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

try:
    __version__ = version("redup-mcp-web-parser")
except PackageNotFoundError:
    __version__ = "0.0.0"
    for candidate in (
        Path(__file__).resolve().parents[2] / "VERSION",
        Path("/app/VERSION"),
    ):
        try:
            __version__ = candidate.read_text(encoding="utf-8").strip()
            break
        except OSError:
            continue
