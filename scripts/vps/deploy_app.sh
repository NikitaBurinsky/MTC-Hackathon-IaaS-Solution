#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/iaas-hackathon}"
APP_COMPOSE_FILE="${APP_DIR}/infra/docker/docker-compose.app.yml"
DOMAIN="${DOMAIN:?DOMAIN is required}"
NGINX_RUNTIME_CONF="${NGINX_RUNTIME_CONF:-/tmp/iaas-nginx.conf}"

if [ ! -f "${APP_COMPOSE_FILE}" ]; then
  echo "Missing ${APP_COMPOSE_FILE}. Ensure repository is cloned on VPS."
  exit 1
fi

cd "${APP_DIR}"

docker network inspect iaas-backbone >/dev/null 2>&1 || docker network create iaas-backbone
git pull --ff-only

cp infra/nginx/nginx.conf "${NGINX_RUNTIME_CONF}"
export NGINX_CONF_PATH="${NGINX_RUNTIME_CONF}"

docker-compose -f "${APP_COMPOSE_FILE}" up -d --build api nginx

sed "s/\${DOMAIN}/${DOMAIN}/g" infra/nginx/nginx.https.template.conf > "${NGINX_RUNTIME_CONF}"
docker-compose -f "${APP_COMPOSE_FILE}" up -d nginx
docker-compose -f "${APP_COMPOSE_FILE}" exec -T nginx nginx -s reload || true

echo "Deploy finished."
