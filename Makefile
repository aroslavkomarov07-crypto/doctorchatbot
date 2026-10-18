ALEMBIC_INI=src/infrastructure/database/alembic.ini

.PHONY: run run-prod migrate migration test lint typecheck check

run:
	docker compose -f docker-compose.local.yml up --build

run-prod:
	docker compose -f docker-compose.prod.external.yml up -d --build

migrate:
	alembic -c $(ALEMBIC_INI) upgrade head

migration:
	alembic -c $(ALEMBIC_INI) revision --autogenerate -m "$(name)"

test:
	pytest

lint:
	ruff check src tests

typecheck:
	mypy src

check: lint typecheck test
