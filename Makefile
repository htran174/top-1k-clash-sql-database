# Makefile

export PYTHONPATH := $(PWD)/src

ifneq (,$(wildcard .env))
include .env
export
endif

TOPN ?= 1000

ETL_MODULE ?= scripts.etl_snapshot_topn
VALIDATE_MODULE ?= scripts.validate_snapshot

.PHONY: db-up db-down db-reset wait-db schema etl validate refresh refresh-hard test

db-up:
	docker compose up -d

# SAFE: stop containers, keep database volume
db-down:
	docker compose down

# DANGEROUS: deletes volumes (pgdata) => wipes database
db-reset:
	docker compose down -v

wait-db:
	@echo "Waiting for Postgres..."
	@until docker compose exec -T db pg_isready -U clash -d clash > /dev/null 2>&1; do \
		sleep 1; \
	done
	@echo "Postgres is ready ✅"

schema: wait-db
	docker compose exec -T db psql -U clash -d clash -f /app/db/schema.sql
	@echo "Schema applied ✅"

etl: wait-db
	python -m $(ETL_MODULE) --top-n $(TOPN)
	@echo "ETL complete ✅ (top-n=$(TOPN))"

validate: wait-db
	python -m $(VALIDATE_MODULE) --top-n $(TOPN)
	@echo "Validation passed ✅ (top-n=$(TOPN))"

refresh: db-up schema etl validate
	@echo "REFRESH DONE ✅ (top-n=$(TOPN))"

# Full wipe + rebuild + reload + validate
refresh-hard: db-reset db-up schema etl validate
	@echo "REFRESH HARD DONE 🔥 (top-n=$(TOPN))"

test:
	python scripts/test_sql.py
