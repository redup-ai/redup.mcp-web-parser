ARG BASE_IMAGE=python:3.13-slim

FROM ${BASE_IMAGE} AS builder

RUN --mount=type=secret,id=PIP_INDEX_URL,required=false \
    export PIP_INDEX_URL=$(cat /run/secrets/PIP_INDEX_URL 2>/dev/null || true) \
    && python3 -m pip install --no-cache -U uv hatchling

WORKDIR /build
COPY pyproject.toml VERSION README.md LICENSE NOTICE ./
COPY src ./src
RUN --mount=type=secret,id=PIP_INDEX_URL,required=false \
    export PIP_INDEX_URL=$(cat /run/secrets/PIP_INDEX_URL 2>/dev/null || true) \
    && if [ -n "${PIP_INDEX_URL}" ]; then export UV_INDEX_URL="${PIP_INDEX_URL}"; fi \
    && uv pip install --no-cache --system --python python --target=/app/libs .

FROM ${BASE_IMAGE}

ENV PYTHONPATH=/app/libs

COPY --from=builder /app/libs /app/libs
COPY VERSION /app/VERSION
COPY config/ /config/

WORKDIR /app

RUN find / -xdev \( -perm -4000 -o -perm -2000 \) -type f -exec chmod a-s {} \; || true

EXPOSE 8000 9999

CMD ["python", "-m", "redup_mcp_web_parser.service", "/config/config.yaml"]
