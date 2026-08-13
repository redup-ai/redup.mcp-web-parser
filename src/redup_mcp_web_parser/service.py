"""HTTP service bootstrap: YAML config, MonitorServer, FastMCP Streamable HTTP."""

from __future__ import annotations

import sys

from redup_servicekit.config import ConfigSingleton
from redup_servicekit.logging import init_console_log
from redup_servicekit.monitoring import ErrorParser, MonitorServer

from redup_mcp_web_parser.config import ServerConfig
from redup_mcp_web_parser.server import create_server


def main(config_path: str | None = None) -> None:
    path = config_path or (sys.argv[1] if len(sys.argv) > 1 else None)
    if not path:
        raise SystemExit(
            "Usage: python -m redup_mcp_web_parser.service /path/to/config.yaml"
        )

    ConfigSingleton.load(path)
    ConfigSingleton.inject_os_envs()
    raw = ConfigSingleton.get()

    service = raw.get("service") or {}
    init_console_log(service.get("console_log_level", "INFO"))

    MonitorServer().run(
        raw.get("MonitorServer", {}),
        max_workers=int(service.get("max_workers", 4)),
        hpa_max_workers=int(service.get("hpa_max_workers", 2)),
    )
    ErrorParser.init()

    config = ServerConfig.from_servicekit(raw)
    server = create_server(config)
    server.run(
        transport="streamable-http",
        host=config.host,
        port=config.port,
        path=config.path,
        json_response=config.json_response,
        stateless_http=config.stateless_http,
    )


if __name__ == "__main__":
    main()
