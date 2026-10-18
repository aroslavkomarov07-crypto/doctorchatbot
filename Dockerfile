FROM ghcr.io/astral-sh/uv:python3.12-alpine AS builder
WORKDIR /opt/doctorshop
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --no-cache --compile-bytecode

FROM python:3.12-alpine
WORKDIR /opt/doctorshop
COPY --from=builder /opt/doctorshop/.venv /opt/doctorshop/.venv
ENV PATH="/opt/doctorshop/.venv/bin:$PATH" PYTHONPATH=/opt/doctorshop PYTHONUNBUFFERED=1
COPY src ./src
COPY assets ./assets
COPY docker-entrypoint.sh ./docker-entrypoint.sh
RUN chmod +x ./docker-entrypoint.sh
CMD ["./docker-entrypoint.sh"]
