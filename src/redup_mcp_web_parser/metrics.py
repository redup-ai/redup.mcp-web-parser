"""Mirror ``aio_grpc_method_wrapper`` task accounting for MCP tool calls."""

from __future__ import annotations

import logging
import time
import traceback
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from redup_servicekit.metrics import PROMETHEUS_METRICS_REGISTRY
from redup_servicekit.monitoring import ErrorParser, MonitorServer, StatusParser


def _server() -> MonitorServer | None:
    return MonitorServer.get_instance()


@asynccontextmanager
async def tracked_work(method: str) -> AsyncIterator[None]:
    """One in-flight MCP tool call."""
    server = _server()
    trace_id = str(uuid.uuid4())
    started = time.time()
    failed = False
    error_type = None

    if server:
        await server.inc_stats(f"request___method__{method}")
        await server.add_key_value("tasks", (trace_id, started))

    try:
        if server:
            with (
                PROMETHEUS_METRICS_REGISTRY["stats_tasks_time_spent_quantile"]
                .labels(*MonitorServer._get_labels())
                .time()
            ):
                yield
        else:
            yield
    except Exception as exc:
        failed = True
        error_type = ErrorParser.parse(exc)
        logging.error(traceback.format_exc())
        if server:
            await server.inc_stats(f"errors___method__{method}___type__{error_type}")
        raise
    finally:
        if server:
            await server.del_key_value("tasks", trace_id)
            await server.inc_stats(
                f"processed___method__{method}___status__{StatusParser.parse(failed)}"
            )
            await server.append_stats(
                {f"time full___method__{method}": time.time() - started}
            )
        await ErrorParser.set_status(error_type)
