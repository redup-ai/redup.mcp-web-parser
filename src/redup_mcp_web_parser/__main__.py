"""CLI entry point for local / desktop MCP (stdio or ad-hoc HTTP).

Docker / Kubernetes use ``python -m redup_mcp_web_parser.service /config/config.yaml``.
"""

from __future__ import annotations

import argparse
import os


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="redup-mcp-web-parser",
        description=(
            "MCP web parser (local CLI). "
            "For production HTTP use: python -m redup_mcp_web_parser.service CONFIG.yaml"
        ),
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http", "streamable-http", "sse"],
        default="stdio",
        help="MCP transport (default: stdio for desktop clients)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address for HTTP transports (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Bind port for HTTP transports (default: 8000)",
    )
    parser.add_argument(
        "--path",
        default="/mcp",
        help="URL path for Streamable HTTP (default: /mcp)",
    )
    parser.add_argument(
        "--upstream-base-url",
        # servicekit injects Section___key with this casing
        default=os.environ.get("McpWebParser___upstream_base_url", ""),  # noqa: SIM112
        help="Crawl4AI base URL (or set McpWebParser___upstream_base_url)",
    )
    parser.add_argument(
        "--upstream-token",
        default=os.environ.get("McpWebParser___upstream_token", ""),  # noqa: SIM112
        help="Optional Bearer token for Crawl4AI",
    )
    parser.add_argument(
        "--default-proxy",
        default=os.environ.get("McpWebParser___default_proxy", ""),  # noqa: SIM112
        help="Default egress proxy URL for Crawl4AI",
    )
    parser.add_argument(
        "--request-timeout-seconds",
        type=float,
        default=120.0,
        help="Default upstream timeout in seconds (default: 120)",
    )
    parser.add_argument(
        "--max-timeout-seconds",
        type=float,
        default=300.0,
        help="Maximum allowed timeout in seconds (default: 300)",
    )
    parser.add_argument(
        "--max-markdown-chars",
        type=int,
        default=100_000,
        help="Truncate markdown to this many characters (default: 100000)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    from redup_mcp_web_parser.config import ServerConfig
    from redup_mcp_web_parser.server import create_server

    transport = args.transport
    if transport == "http":
        transport = "streamable-http"

    config = ServerConfig(
        upstream_base_url=args.upstream_base_url,
        upstream_token=args.upstream_token,
        default_proxy=args.default_proxy,
        request_timeout_seconds=args.request_timeout_seconds,
        max_timeout_seconds=args.max_timeout_seconds,
        max_markdown_chars=args.max_markdown_chars,
        transport=transport,
        host=args.host,
        port=args.port,
        path=args.path,
        require_upstream=True,
    )

    server = create_server(config)

    if transport == "stdio":
        server.run(transport="stdio")
        return

    server.run(
        transport=transport,
        host=config.host,
        port=config.port,
        path=config.path,
        json_response=config.json_response,
        stateless_http=config.stateless_http,
    )


if __name__ == "__main__":
    main()
