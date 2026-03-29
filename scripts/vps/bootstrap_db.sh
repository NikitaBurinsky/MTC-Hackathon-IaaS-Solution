#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/iaas-hackathon}"
DB_COMPOSE_FILE="${APP_DIR}/infra/docker/docker-compose.db.yml"

if [ ! -f "${DB_COMPOSE_FILE}" ]; then
  echo "Missing ${DB_COMPOSE_FILE}. Ensure repository is cloned on VPS."
  exit 1
fi

cd "${APP_DIR}"

docker network inspect iaas-backbone >/dev/null 2>&1 || docker network create iaas-backbone
if docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_CMD="docker-compose"
else
  echo "Docker Compose is required but was not found."
  exit 1
fi

${COMPOSE_CMD} -f "${DB_COMPOSE_FILE}" up -d

echo "Waiting for PostgreSQL health..."
for i in {1..30}; do
  if ${COMPOSE_CMD} -f "${DB_COMPOSE_FILE}" exec -T postgres pg_isready -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-iaas}" >/dev/null 2>&1; then
    echo "PostgreSQL is healthy."
    exit 0
  fi
  sleep 2
done

echo "PostgreSQL health check timeout."
exit 1
