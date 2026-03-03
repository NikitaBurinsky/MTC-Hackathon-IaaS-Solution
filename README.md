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
4. Run API:
   - `uvicorn app.main:app --reload`

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

## VPS Deployment

Workflows:
- `.github/workflows/db-bootstrap.yml` (manual trigger)
- `.github/workflows/deploy-app.yml` (auto on push to `main`)

Scripts:
- `scripts/vps/bootstrap_db.sh`
- `scripts/vps/deploy_app.sh`

## Architecture Diagrams

See `docs/architecture/*.mmd`.

## Manual Validation

Use `docs/manual-test-checklist.md` with Swagger `/docs`.
