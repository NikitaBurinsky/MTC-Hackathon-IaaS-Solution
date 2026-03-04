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

Catalog (read-only, seeded by `init_db`):
- `GET /flavors`
- `GET /images`
- `GET /plans`

Tenant-scoped resources:
- `GET/POST/DELETE /instances*`
- `POST/GET/DELETE /deployments*`
- `POST /tasks/execute`, `GET /tasks`, `GET /tasks/{id}`
- `GET/POST/PUT/DELETE /scripts*`
- `GET/POST/PUT/DELETE /networks*`

AI deployment entrypoint rules live in `app/config/entrypoint_rules.json`
(`exact_filenames` and `regex_patterns`).

## Hosted Deployments

Deployed apps are exposed via Nginx at:
`https://<DOMAIN>/<DEPLOYMENT_PUBLIC_PATH_PREFIX>/<deployment_id>/`.
Deleting a deployment removes the container, image, and Nginx route.

## VPS Deployment (Ubuntu)

Workflows:
- `.github/workflows/db-bootstrap.yml` (manual trigger)
- `.github/workflows/deploy-app.yml` (auto on push to `main`)

DB bootstrap recreates PostgreSQL from scratch each run (container + volume).

Required GitHub Secrets:
- `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY`, `VPS_PORT`
- `POSTGRES_PASSWORD`, `DATABASE_URL`, `JWT_SECRET`, `DOMAIN`, `DEEPSEEK_API_KEY`
- `DEEPSEEK_PROXY_URL` (optional, when calling DeepSeek through a proxy URL)
- `DEEPSEEK_PROXY_USERNAME`, `DEEPSEEK_PROXY_PASSWORD` (optional, when assembling proxy URL from parts)
- `DEEPSEEK_PROXY_SCHEME`, `DEEPSEEK_PROXY_HOST`, `DEEPSEEK_PROXY_PORT` (optional, proxy URL parts)
  - `DEEPSEEK_PROXY_URL` has priority over split proxy settings.

Optional GitHub Variables:
- `VPS_APP_DIR` (default: `/opt/iaas-hackathon`)
- `POSTGRES_USER` (default: `postgres`)
- `POSTGRES_DB` (default: `iaas`)
- `POSTGRES_IMAGE` (default: `postgres:16-alpine`)
- `POSTGRES_CONTAINER_NAME` (default: `iaas-postgres`)
- `NGINX_RUNTIME_CONF` (default: `/tmp/iaas-nginx.conf`)
- `DEPLOYMENT_PUBLIC_PATH_PREFIX` (default: `hosted`)
- `DEPLOYMENT_PUBLIC_SCHEME` (default: `https`)
- `DEPLOYMENT_NETWORK_NAME` (default: `iaas-backbone`)
- `NGINX_CONTAINER_NAME` (default: `iaas-nginx`)
- `DEEPSEEK_API_BASE_URL` (default: `https://api.deepseek.com`)
- `DEEPSEEK_MODEL` (default: `deepseek-chat`)
- `DEEPSEEK_TIMEOUT_SEC` (default: `120`)
