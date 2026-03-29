# IaaS Cloud Platform MVP (Hackathon)

FastAPI-based IaaS concept with multi-tenant isolation, Docker-backed instances,
task execution, logical networks, and basic billing/quotas. Swagger UI is
available at `/docs`.

## Documentation

- `docs/technical.md`: hackathon technical task and evaluation criteria.
- `docs/architecture/README.md`: diagram index and rendering tips.
- `docs/manual-test-checklist.md`: manual QA scenarios for the API.

## Quick Start (Local)

1. `cp .env.example .env` and fill required values.
2. Start PostgreSQL (use your local compose file or a local Postgres install).
3. Install dependencies: `pip install -r requirements.txt` or `uv sync`.
4. Run the API: `uvicorn app.main:app --reload` (or `uv run uvicorn app.main:app --reload`).

Python >= 3.13 is required.

## API Overview

Base path: `/api/v1`.

Auth:
- `POST /auth/register` and `POST /auth/login` accept JSON and return a JWT.
- The JWT is also set as the `access_token` cookie for browser clients.
- Auth responses include `role` (`USER`, `ADMIN`, `SUPERUSER`).

Catalog (read-only, seeded by `init_db`):
- `GET /flavors`
- `GET /images`
- `GET /plans`

Tenant-scoped resources:
- `GET/POST/DELETE /instances*`
- `POST/GET/DELETE /deployments*`
- `GET /deployments` (history list, newest first)
- `POST /tasks/execute`, `GET /tasks`, `GET /tasks/{id}`
- `GET/POST/PUT/DELETE /scripts*`
- `GET/POST/PUT/DELETE /networks*`

Admin resources (`ADMIN` and `SUPERUSER`):
- `GET /admin/overview`
- `GET /admin/tenants`
- `GET /admin/users`
- `POST /admin/users/{id}/promote` (`SUPERUSER` only)
- `POST /admin/users/{id}/demote` (`SUPERUSER` only)
- `GET /admin/instances`
- `POST /admin/instances/{id}/action`
- `DELETE /admin/instances/{id}`
- `GET /admin/deployments`
- `GET /admin/billing/usage`

RBAC:
- `USER` can access only its tenant context.
- `ADMIN` has provider-level access through `/admin/*`.
- `SUPERUSER` is global (`tenant_id = NULL`) and can promote/demote admins.
- tenant-only dependencies return `403` for users without tenant context (e.g. `SUPERUSER`).

AI deployment entrypoint rules live in `app/config/entrypoint_rules.json`
(`exact_filenames` and `regex_patterns`).

`GET /deployments/{id}` includes retry metadata:
- `current_attempt`, `max_attempts`
- `attempts[]` with per-attempt Dockerfile, build error, detected technology, and timing.
- deployment records are persisted in DB (`deployments`, `deployment_attempts`) and survive API restarts.

## Hosted Deployments

Hosted routing mode: `subdomain`.

Deployed apps are exposed via Nginx at:
`https://<deployment_id>.<DEPLOYMENT_HOST_DOMAIN or DOMAIN>/`.
Deleting a deployment removes the container, image, and Nginx route.

Infrastructure requirement:
- wildcard DNS record `*.DOMAIN` must point to the Nginx host (A/CNAME).
- optional separate hosted zone: set `DEPLOYMENT_HOST_DOMAIN` (e.g. `hosters.formatis.online`)
  and configure wildcard DNS `*.DEPLOYMENT_HOST_DOMAIN` to the same Nginx host.

HTTPS support for hosted subdomains:
- set `DEPLOYMENT_PUBLIC_SCHEME=https`
- provide wildcard certificate for `*.DOMAIN` (Let’s Encrypt DNS-01 or equivalent)
- optionally set:
  - `DEPLOYMENT_TLS_CERT_PATH`
  - `DEPLOYMENT_TLS_KEY_PATH`
- if paths are not set, API uses `/etc/letsencrypt/live/<DOMAIN>/fullchain.pem` and `privkey.pem`.

Referer-based routing is intentionally not used as a primary production mechanism.

AI deployment retries are enabled by default:
- up to 5 attempts per deployment (`AI_DEPLOY_MAX_ATTEMPTS`, hard-capped at 5)
- retry occurs only after Docker build failures
- attempts 2-3 include enriched repository context + previous build feedback.

## VPS Deployment (Ubuntu)

Workflows:
- `.github/workflows/db-bootstrap.yml` (manual trigger)
- `.github/workflows/deploy-app.yml` (auto on push to `main`)
- `.github/workflows/demo-reset.yml` (auto daily + manual trigger)

DB bootstrap recreates PostgreSQL from scratch each run (container + volume).

Required GitHub Secrets:
- `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY`, `VPS_PORT`
- `POSTGRES_PASSWORD`, `DATABASE_URL`, `JWT_SECRET`, `DOMAIN`, `PROXYAPI_API_KEY`
- `SUPERUSER_EMAIL`, `SUPERUSER_PASSWORD`

Optional GitHub Variables:
- `VPS_APP_DIR` (default: `/opt/iaas-hackathon`)
- `POSTGRES_USER` (default: `postgres`)
- `POSTGRES_DB` (default: `iaas`)
- `POSTGRES_IMAGE` (default: `postgres:16-alpine`)
- `POSTGRES_CONTAINER_NAME` (default: `iaas-postgres`)
- `NGINX_RUNTIME_CONF` (default: `/tmp/iaas-nginx.conf/nginx.conf`)
- `DEPLOYMENT_PUBLIC_SCHEME` (default: `https`)
- `DEPLOYMENT_HOST_DOMAIN` (optional; fallback to `DOMAIN`)
- `DEPLOYMENT_TLS_CERT_PATH` (optional)
- `DEPLOYMENT_TLS_KEY_PATH` (optional)
- `DEPLOYMENT_NETWORK_NAME` (default: `iaas-backbone`)
- `NGINX_CONTAINER_NAME` (default: `iaas-nginx`)
- `PROXYAPI_BASE_URL` (default: `https://api.proxyapi.ru/openrouter/v1`)
- `PROXYAPI_MODEL` (default: `deepseek/deepseek-chat`)
- `PROXYAPI_TIMEOUT_SEC` (default: `120`)
- `AI_DEPLOY_MAX_ATTEMPTS` (default: `3`, hard cap `5`)
- `AI_DEPLOY_RETRY_CONTEXT_MAX_CHARS` (default: `120000`)
- `SUPERUSER_NAME` (default: `SuperUser`)

Runtime note:
- startup is fail-fast if `SUPERUSER_EMAIL` or `SUPERUSER_PASSWORD` is missing.
- this RBAC rollout assumes DB reset (`drop & recreate`) without Alembic migrations.

## Automated Demo Reset

The demo environment can be fully reset by `.github/workflows/demo-reset.yml`.

Current schedule:
- daily at `05:00 UTC` (`08:00` Minsk, `UTC+3`)
- manual trigger via `workflow_dispatch`

Reset scope:
- project runtime containers (`iaas-api`, `iaas-nginx`, and deployment containers on `DEPLOYMENT_NETWORK_NAME`)
- database reset via `DATABASE_URL` (`DROP SCHEMA public CASCADE` + recreate)
- project deployment images cleanup (from DB records; fallback `tenant-*-deploy-*`)
- full restart of `api + nginx` (database remains external or managed separately)
- `POSTGRES_VOLUME_NAME` is not used by this workflow.

To change the interval `N`, update the cron expression in
`.github/workflows/demo-reset.yml`.
