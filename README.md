# redup.mcp-web-parser

![Docker test](https://github.com/redup-ai/redup.mcp-web-parser/actions/workflows/docker-test.yml/badge.svg?branch=master)
![Python test](https://github.com/redup-ai/redup.mcp-web-parser/actions/workflows/python-test.yml/badge.svg?branch=master)

MCP Streamable HTTP service that parses web pages into cleaned markdown via
[Crawl4AI](https://github.com/unclecode/crawl4ai) `POST /crawl` (0.8.x).

## Model

- Thin MCP façade over Crawl4AI HTTP API — **no browser** in this image.
- **`upstream_base_url` is required** at runtime (config or
  `McpWebParser___upstream_base_url`). Defaults ship **empty** (OSS-safe: no
  cluster hostnames or internal proxies in the repo).
- Optional **egress proxy** for Crawl4AI IP substitution via server config
  `default_proxy` → `crawler_config.proxy_config.server`. Empty = direct fetch.
  Proxy is **not** a tool argument (deploy/runtime only).
- Tool results are **JSON** (`success` / `markdown` / `status_code` / …),
  not a concatenated text dump.
- Targeted at Crawl4AI **0.8.x** (per-request `proxy_config` works). On 0.9+
  Docker API may reject `proxy` / `proxy_config` in the request body.

Contract: MCP tools `parse_page`, `fetch_pdf`, `check_upstream`.
Endpoint: `POST http://<host>:8000/mcp` (stateless Streamable HTTP, JSON).
Metrics: `GET http://<host>:9999/metrics` (Prometheus via `redup-servicekit`).

**Tool args:**
- `parse_page` — **`url`**, optional **`timeout`**. HTML only via Crawl4AI.
  Confirmed PDFs (`Content-Type` / `%PDF` / `.pdf`) return `is_pdf=true` and a
  hint to call `fetch_pdf`. A bare `/pdf/` path is not enough when the response
  type is clearly non-PDF.
- `fetch_pdf` — **`url`**, optional **`timeout`**. Downloads a PDF over HTTP
  (same `default_proxy`). Rejects non-PDF Content-Types. JSON puts small fields
  before `content_base64`. Not a fallback for failed HTML parses.
- `check_upstream` — no args (`GET /health`).

**Agent registration example:** `{"id":"web-parser","url":"http://…:8000/mcp"}`
→ LLM names `mcp__web-parser__parse_page` / `mcp__web-parser__fetch_pdf`.

## Configuration

`config/config.yaml`:

```yaml
service:
  console_log_level: INFO
  host: "0.0.0.0"
  port: 8000
  path: /mcp
  max_workers: 4
  hpa_max_workers: 2

McpWebParser:
  upstream_base_url: ""
  upstream_token: ""
  default_proxy: ""
  request_timeout_seconds: 120
  max_timeout_seconds: 300
  max_markdown_chars: 100000
  max_pdf_bytes: 15728640
  delay_before_return_html: 2.5
  json_response: true
  stateless_http: true
```

Override via servicekit env substitution (`section___key`):

```bash
export McpWebParser___upstream_base_url=https://crawl4ai.example.com
export McpWebParser___default_proxy=http://user:pass@proxy.example:3128
export McpWebParser___upstream_token=
export service___port=8000
```

Startup fails fast if `upstream_base_url` is empty.

## Run with Docker

```bash
docker run --rm -p 8000:8000 -p 9999:9999 \
  -e McpWebParser___upstream_base_url=https://crawl4ai.example.com \
  -e McpWebParser___default_proxy=http://proxy.example:3128 \
  redup4ai/redup.mcp-web-parser:0.1.0-3.13-slim
```

MCP URL: `http://127.0.0.1:8000/mcp`. Metrics: `http://127.0.0.1:9999/metrics`.

GitHub Release publishes `{VERSION}-3.13-slim` to Docker Hub (`DOCKERHUB_USER` /
`DOCKERHUB_PASSWORD` secrets).

## Run locally without Docker

Requires Python 3.13+ and a reachable Crawl4AI base URL:

```bash
export McpWebParser___upstream_base_url=https://crawl4ai.example.com
uv sync
uv run python -m redup_mcp_web_parser.service config/config.yaml
```

Desktop MCP clients (stdio):

```bash
uv run redup-mcp-web-parser \
  --transport stdio \
  --upstream-base-url https://crawl4ai.example.com
```

## Tests

```bash
uv sync --dev
uv run pytest tests -q -m "not live"
```

Optional live smoke (needs a real Crawl4AI):

```bash
export McpWebParser___upstream_base_url=https://crawl4ai.example.com
uv run pytest tests -m live -q
```

## License

MIT — see `LICENSE` and `NOTICE`.
