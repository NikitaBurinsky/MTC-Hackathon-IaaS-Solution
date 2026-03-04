# IaaS Cloud Platform MVP (Hackathon)

FastAPI-based IaaS MVP with:
- Multi-tenant isolation via JWT `tenant_id`
- Resource quota checks and usage billing
- Docker-backed instance lifecycle
- Task execution on instances with per-instance logs
- Logical network CRUD
- Swagger demo at `/docs`

## Quick Start (Local)

1. Create `.env` from `.env.example`.
2. Start PostgreSQL.
3. Install dependencies:
   - `pip install -r requirements.txt`
   - OR `uv sync`
4. Run API:
   - `uvicorn app.main:app --reload`
   - OR `uv run uvicorn app.main:app --reload`

## API Base

- `/api/v1`

Key routes:
- `POST /auth/register`
- `POST /auth/login`
- `GET /tenant/profile`
- `GET /billing/quotas`
- `GET /billing/usage`
- `GET /flavors`
- `GET /images`
- `GET/POST/DELETE /instances*`
- `POST /tasks/execute`
- `GET /tasks`
- `GET /tasks/{id}`
- `GET/POST/PUT/DELETE /scripts*`
- `GET/POST/PUT/DELETE /networks*`

## VPS Deployment (Ubuntu)

Workflows:
- `.github/workflows/db-bootstrap.yml` (manual trigger)
- `.github/workflows/deploy-app.yml` (auto on push to `main`)

Notes:
- Workflow logic is fully inline (no `.sh` script execution).
- DB bootstrap recreates PostgreSQL from scratch each run:
  - removes old container
  - removes old named volume
  - starts a brand new Postgres container

Required GitHub Secrets:
- `VPS_HOST`
- `VPS_USER`
- `VPS_SSH_KEY`
- `VPS_PORT`
- `POSTGRES_PASSWORD`
- `DOMAIN`
- `JWT_SECRET`
- `DATABASE_URL`

Optional GitHub Variables:
- `VPS_APP_DIR` (default: `/opt/iaas-hackathon`)
- `POSTGRES_USER` (default: `postgres`)
- `POSTGRES_DB` (default: `iaas`)
- `POSTGRES_IMAGE` (default: `postgres:16-alpine`)
- `POSTGRES_CONTAINER_NAME` (default: `iaas-postgres`)
- `NGINX_RUNTIME_CONF` (default: `/tmp/iaas-nginx.conf`)

## Architecture Diagrams

See `docs/architecture/*.mmd`.

## Manual Validation

Use `docs/manual-test-checklist.md` with Swagger `/docs`.
