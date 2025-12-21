export PYTHONPATH := $(PWD)/src

.PHONY: db-up db-down wait-db schema test

db-up:
	docker compose up -d

db-down:
	docker compose down -v

wait-db:
	@echo "Waiting for Postgres..."
	@until docker compose exec -T db pg_isready -U clash -d clash > /dev/null 2>&1; do \
		sleep 1; \
	done
	@echo "Postgres is ready ✅"

schema: wait-db
	docker compose exec -T db psql -U clash -d clash -f /app/db/schema.sql

test:
	python scripts/test_sql.py
