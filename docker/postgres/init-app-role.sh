#!/bin/sh
# Runs once, on first start of an empty data directory, as the owner (POSTGRES_USER).
# Creates the login role the API uses. Table grants are applied by the Alembic migration
# (audit_log gets INSERT and SELECT only, S12).
set -eu

: "${APP_DB_PASSWORD:?APP_DB_PASSWORD must be set}"
APP_DB_ROLE="${APP_DB_ROLE:-loanrag_app}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v role="$APP_DB_ROLE" -v pw="$APP_DB_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'role', :'pw')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'role') \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), :'role') \gexec
SQL
