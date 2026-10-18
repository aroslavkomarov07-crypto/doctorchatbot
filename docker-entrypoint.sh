#!/bin/sh
set -eu

alembic -c src/infrastructure/database/alembic.ini upgrade head
exec uvicorn src.__main__:application \
  --factory \
  --host "${APP_HOST:-0.0.0.0}" \
  --port "${APP_PORT:-5000}" \
  ${UVICORN_RELOAD_ENABLED:+--reload}
