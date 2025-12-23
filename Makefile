# Makefile

export PYTHONPATH := $(PWD)/src

ifneq (,$(wildcard .env))
include .env
export
endif

TOPN ?= 20

ETL_MODULE ?= scripts.etl_snapshot_topn
VALIDATE_MODULE ?= scripts.validate_snapshot

.PHONY: db-up db-down db-reset wait-db schema etl validate refresh refresh-hard test db-query

db-up:
	docker compose up -d

db-query: db-up
	docker compose exec db psql -U clash -d clash

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

# -------------------------
# Cloud SQL (no Docker)
# -------------------------

.PHONY: cloud-schema cloud-etl cloud-psql cloud-reset cloud-load-from-local

# Helper: require env var
require-%:
	@test -n "$($*)" || (echo "Missing $* in .env" && exit 1)

# Build a connection string that matches working manual psql command
CLOUD_CONNINFO = host=$(CLOUD_DB_HOST) port=5432 dbname=$(CLOUD_DB_NAME) user=$(CLOUD_DB_USER) sslmode=require

cloud-psql: require-CLOUD_DB_HOST require-CLOUD_DB_NAME require-CLOUD_DB_USER require-CLOUD_PGPASSWORD
	PGPASSWORD="$(CLOUD_PGPASSWORD)" psql "$(CLOUD_CONNINFO)"

cloud-reset: require-DATABASE_URL_CLOUD require-CLOUD_PGPASSWORD
	@echo "Resetting Cloud DB (dropping tables in public)..."
	PGPASSWORD="$(CLOUD_PGPASSWORD)" psql "$(DATABASE_URL_CLOUD)" -v ON_ERROR_STOP=1 -f db/cloud_reset.sql
	@echo "Cloud reset ✅"

cloud-schema: require-CLOUD_DB_HOST require-CLOUD_DB_NAME require-CLOUD_DB_USER require-CLOUD_PGPASSWORD
	PGPASSWORD="$(CLOUD_PGPASSWORD)" psql "$(CLOUD_CONNINFO)" -f db/schema.sql
	@echo "Cloud schema applied ✅"

# -------------------------
# Push LOCAL docker data -> CLOUD (dump + restore)
# -------------------------
cloud-load-from-local: wait-db require-CLOUD_DB_HOST require-CLOUD_DB_NAME require-CLOUD_DB_USER require-CLOUD_PGPASSWORD
	@echo "Dumping local docker DB data and loading into Cloud SQL..."
	docker compose exec -T db pg_dump -U clash -d clash \
		--data-only --no-owner --no-privileges \
	| PGPASSWORD="$(CLOUD_PGPASSWORD)" psql "$(CLOUD_CONNINFO)" -v ON_ERROR_STOP=1
	@echo "Cloud load from local ✅"

# ----------------------------------
# Full LOCAL → CLOUD refresh pipeline
# ----------------------------------

.PHONY: cloud-refresh cloud-validate cloud-publish

cloud-refresh: cloud-reset cloud-schema cloud-load-from-local
	@echo "CLOUD REFRESH COMPLETE 🚀"

cloud-validate: require-CLOUD_DB_HOST require-CLOUD_DB_NAME require-CLOUD_DB_USER require-CLOUD_PGPASSWORD
	@echo "Running validation against CLOUD..."
	DATABASE_URL="postgresql+psycopg2://$(CLOUD_DB_USER):$(CLOUD_PGPASSWORD)@$(CLOUD_DB_HOST):5432/$(CLOUD_DB_NAME)" \
	python -m $(VALIDATE_MODULE) --top-n $(TOPN)
	@echo "Cloud validation passed ✅ (top-n=$(TOPN))"

cloud-publish: cloud-refresh cloud-validate
	@echo "CLOUD PUBLISH COMPLETE ✅"
