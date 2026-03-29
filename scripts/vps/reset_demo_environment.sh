#!/usr/bin/env bash
set -euo pipefail

log() {
  echo "[demo-reset] $*"
}

add_unique() {
  local array_name="$1"
  local value="$2"
  local normalized="${value//$'\r'/}"
  normalized="${normalized//$'\n'/}"
  if [ -z "${normalized//[[:space:]]/}" ]; then
    return
  fi

  local -n target_array="${array_name}"
  local existing
  for existing in "${target_array[@]}"; do
    if [ "${existing}" = "${normalized}" ]; then
      return
    fi
  done
  target_array+=("${normalized}")
}

APP_DIR="${APP_DIR:-/opt/iaas-hackathon}"
APP_COMPOSE_FILE="${APP_DIR}/infra/docker/docker-compose.app.yml"
NGINX_TEMPLATE_FILE="${APP_DIR}/infra/nginx/nginx.https.template.conf"
NGINX_RUNTIME_CONF="${NGINX_RUNTIME_CONF:-/tmp/iaas-nginx.conf/nginx.conf}"

DOMAIN="${DOMAIN:?DOMAIN is required}"
DATABASE_URL="${DATABASE_URL:?DATABASE_URL is required}"
JWT_SECRET="${JWT_SECRET:?JWT_SECRET is required}"
PROXYAPI_API_KEY="${PROXYAPI_API_KEY:?PROXYAPI_API_KEY is required}"
SUPERUSER_EMAIL="${SUPERUSER_EMAIL:?SUPERUSER_EMAIL is required}"
SUPERUSER_PASSWORD="${SUPERUSER_PASSWORD:?SUPERUSER_PASSWORD is required}"
GHCR_USER="${GHCR_USER:?GHCR_USER is required}"
GHCR_TOKEN="${GHCR_TOKEN:?GHCR_TOKEN is required}"
APP_IMAGE="${APP_IMAGE:?APP_IMAGE is required}"

DEPLOYMENT_NETWORK_NAME="${DEPLOYMENT_NETWORK_NAME:-iaas-backbone}"
NGINX_CONTAINER_NAME="${NGINX_CONTAINER_NAME:-iaas-nginx}"
POSTGRES_CONTAINER_NAME="${POSTGRES_CONTAINER_NAME:-iaas-postgres}"
DEPLOYMENT_HOST_DOMAIN="${DEPLOYMENT_HOST_DOMAIN:-}"
DEPLOYMENT_PUBLIC_SCHEME="${DEPLOYMENT_PUBLIC_SCHEME:-https}"
DEPLOYMENT_TLS_CERT_PATH="${DEPLOYMENT_TLS_CERT_PATH:-}"
DEPLOYMENT_TLS_KEY_PATH="${DEPLOYMENT_TLS_KEY_PATH:-}"
COOKIE_SAMESITE="${COOKIE_SAMESITE:-lax}"
COOKIE_SECURE="${COOKIE_SECURE:-true}"
PROXYAPI_BASE_URL="${PROXYAPI_BASE_URL:-https://api.proxyapi.ru/openrouter/v1}"
PROXYAPI_MODEL="${PROXYAPI_MODEL:-deepseek/deepseek-chat}"
PROXYAPI_TIMEOUT_SEC="${PROXYAPI_TIMEOUT_SEC:-120}"
AI_DEPLOY_MAX_ATTEMPTS="${AI_DEPLOY_MAX_ATTEMPTS:-3}"
AI_DEPLOY_RETRY_CONTEXT_MAX_CHARS="${AI_DEPLOY_RETRY_CONTEXT_MAX_CHARS:-120000}"
CPU_PRICE_PER_VCPU_MIN="${CPU_PRICE_PER_VCPU_MIN:-1}"
RAM_PRICE_PER_GB_MIN="${RAM_PRICE_PER_GB_MIN:-5}"
SUPERUSER_NAME="${SUPERUSER_NAME:-SuperUser}"
PSQL_CLIENT_IMAGE="${PSQL_CLIENT_IMAGE:-postgres:16-alpine}"

if [ ! -d "${APP_DIR}" ]; then
  echo "Missing APP_DIR=${APP_DIR}"
  exit 1
fi
if [ ! -f "${APP_COMPOSE_FILE}" ]; then
  echo "Missing ${APP_COMPOSE_FILE}"
  exit 1
fi
if [ ! -f "${NGINX_TEMPLATE_FILE}" ]; then
  echo "Missing ${NGINX_TEMPLATE_FILE}"
  exit 1
fi

cd "${APP_DIR}"

DATABASE_URL_PSQL="$(printf '%s' "${DATABASE_URL}" | sed -E 's#^postgresql\+[^:]+://#postgresql://#')"
DB_HOST="$(printf '%s' "${DATABASE_URL_PSQL}" | sed -E 's#^[^:]+://##; s#^[^@]*@##; s#^\[?([^]/:?#]+)\]?.*#\1#')"
PSQL_NETWORK_MODE="${DEPLOYMENT_NETWORK_NAME}"
if [ "${DB_HOST}" = "localhost" ] || [ "${DB_HOST}" = "127.0.0.1" ]; then
  PSQL_NETWORK_MODE="host"
fi

if [ "${PSQL_NETWORK_MODE}" != "host" ]; then
  docker network inspect "${DEPLOYMENT_NETWORK_NAME}" >/dev/null 2>&1 || docker network create "${DEPLOYMENT_NETWORK_NAME}" >/dev/null
fi

declare -a CONTAINERS_TO_REMOVE=()
declare -a IMAGES_TO_REMOVE=()

run_psql_query() {
  local sql="$1"
  printf '%s\n' "${sql}" | docker run --rm -i \
    --network "${PSQL_NETWORK_MODE}" \
    -e DATABASE_URL_PSQL="${DATABASE_URL_PSQL}" \
    "${PSQL_CLIENT_IMAGE}" \
    sh -lc 'psql "$DATABASE_URL_PSQL" -v ON_ERROR_STOP=1 -At' 2>/dev/null
}

run_psql_exec() {
  local sql="$1"
  printf '%s\n' "${sql}" | docker run --rm -i \
    --network "${PSQL_NETWORK_MODE}" \
    -e DATABASE_URL_PSQL="${DATABASE_URL_PSQL}" \
    "${PSQL_CLIENT_IMAGE}" \
    sh -lc 'psql "$DATABASE_URL_PSQL" -v ON_ERROR_STOP=1 >/dev/null'
}

collect_from_db() {
  if ! run_psql_exec "SELECT 1;"; then
    log "[cleanup] DATABASE_URL is unreachable; DB-derived cleanup will be skipped."
    return
  fi

  local instance_container_ids
  local deployment_container_ids
  local deployment_images
  instance_container_ids="$(run_psql_query "SELECT docker_container_id FROM instances WHERE docker_container_id IS NOT NULL;" || true)"
  deployment_container_ids="$(run_psql_query "SELECT container_id FROM deployments WHERE container_id IS NOT NULL;" || true)"
  deployment_images="$(run_psql_query "SELECT docker_image FROM deployments WHERE docker_image IS NOT NULL;" || true)"

  while IFS= read -r value; do
    add_unique CONTAINERS_TO_REMOVE "${value}"
  done <<< "${instance_container_ids}"
  while IFS= read -r value; do
    add_unique CONTAINERS_TO_REMOVE "${value}"
  done <<< "${deployment_container_ids}"
  while IFS= read -r value; do
    add_unique IMAGES_TO_REMOVE "${value}"
  done <<< "${deployment_images}"
}

collect_from_network() {
  if ! docker network inspect "${DEPLOYMENT_NETWORK_NAME}" >/dev/null 2>&1; then
    return
  fi

  local network_containers
  local value
  network_containers="$(docker network inspect "${DEPLOYMENT_NETWORK_NAME}" --format '{{range $id, $cfg := .Containers}}{{println $cfg.Name}}{{end}}' 2>/dev/null || true)"
  while IFS= read -r value; do
    if [ -z "${value}" ]; then
      continue
    fi
    if [ "${value}" = "iaas-api" ] || [ "${value}" = "${NGINX_CONTAINER_NAME}" ] || [ "${value}" = "${POSTGRES_CONTAINER_NAME}" ]; then
      continue
    fi
    add_unique CONTAINERS_TO_REMOVE "${value}"
  done <<< "${network_containers}"
}

cleanup_containers() {
  local container_ref
  for container_ref in "${CONTAINERS_TO_REMOVE[@]}"; do
    if docker container inspect "${container_ref}" >/dev/null 2>&1; then
      log "[cleanup] Removing container ${container_ref}"
      docker rm -f "${container_ref}" >/dev/null 2>&1 || true
    fi
  done
}

cleanup_images() {
  local image_ref
  for image_ref in "${IMAGES_TO_REMOVE[@]}"; do
    log "[cleanup] Removing image ${image_ref}"
    docker image rm -f "${image_ref}" >/dev/null 2>&1 || true
  done

  if [ "${#IMAGES_TO_REMOVE[@]}" -gt 0 ]; then
    return
  fi

  local fallback_images
  fallback_images="$(docker image ls --format '{{.Repository}}:{{.Tag}}' | grep -E '^tenant-.*-deploy-.*:' || true)"
  while IFS= read -r image_ref; do
    if [ -n "${image_ref}" ]; then
      log "[cleanup] Removing fallback deployment image ${image_ref}"
      docker image rm -f "${image_ref}" >/dev/null 2>&1 || true
    fi
  done <<< "${fallback_images}"
}

log "[cleanup] Collecting project resources for deletion."
collect_from_db
add_unique CONTAINERS_TO_REMOVE "iaas-api"
add_unique CONTAINERS_TO_REMOVE "${NGINX_CONTAINER_NAME}"
collect_from_network

log "[cleanup] Stopping and removing project containers."
cleanup_containers
docker compose -f "${APP_COMPOSE_FILE}" down --remove-orphans || true

log "[cleanup] Removing project deployment images."
cleanup_images

log "[db-reset] Resetting database via DATABASE_URL."
run_psql_exec "DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public; GRANT ALL ON SCHEMA public TO CURRENT_USER; GRANT ALL ON SCHEMA public TO PUBLIC;"

log "[app-up] Preparing runtime nginx config."
mkdir -p "$(dirname "${NGINX_RUNTIME_CONF}")"
sed "s/\${DOMAIN}/${DOMAIN}/g" "${NGINX_TEMPLATE_FILE}" > "${NGINX_RUNTIME_CONF}"
sed -i 's/listen 443 ssl http2;/listen 443 ssl;\n    http2 on;/g' "${NGINX_RUNTIME_CONF}"
grep -q "^server_names_hash_bucket_size" "${NGINX_RUNTIME_CONF}" || sed -i '1iserver_names_hash_bucket_size 128;' "${NGINX_RUNTIME_CONF}"
grep -q "^server_names_hash_max_size" "${NGINX_RUNTIME_CONF}" || sed -i '2iserver_names_hash_max_size 8192;' "${NGINX_RUNTIME_CONF}"
sed -i '1s/^\xEF\xBB\xBF//' "${NGINX_RUNTIME_CONF}"

export DATABASE_URL
export JWT_SECRET
export SUPERUSER_EMAIL
export SUPERUSER_PASSWORD
export SUPERUSER_NAME
export PROXYAPI_API_KEY
export PROXYAPI_BASE_URL
export PROXYAPI_MODEL
export PROXYAPI_TIMEOUT_SEC
export AI_DEPLOY_MAX_ATTEMPTS
export AI_DEPLOY_RETRY_CONTEXT_MAX_CHARS
export DOMAIN
export DEPLOYMENT_HOST_DOMAIN
export COOKIE_SAMESITE
export COOKIE_SECURE
export CPU_PRICE_PER_VCPU_MIN
export RAM_PRICE_PER_GB_MIN
export DEPLOYMENT_PUBLIC_SCHEME
export DEPLOYMENT_TLS_CERT_PATH
export DEPLOYMENT_TLS_KEY_PATH
export NGINX_CONTAINER_NAME
export DEPLOYMENT_NETWORK_NAME
export NGINX_CONF_PATH="${NGINX_RUNTIME_CONF}"
export APP_IMAGE

log "[app-up] Logging in to GHCR."
echo "${GHCR_TOKEN}" | docker login ghcr.io -u "${GHCR_USER}" --password-stdin

log "[app-up] Pulling and recreating api/nginx containers."
docker compose -f "${APP_COMPOSE_FILE}" pull api
docker compose -f "${APP_COMPOSE_FILE}" up -d --force-recreate api nginx

log "[nginx-check] Validating nginx config."
docker compose -f "${APP_COMPOSE_FILE}" exec -T nginx nginx -t
docker compose -f "${APP_COMPOSE_FILE}" exec -T nginx nginx -s reload

log "Demo environment reset completed successfully."
