# Repository Guidelines

## Project Structure & Module Organization
- `app/` is the FastAPI application: routers in `app/api/v1/routers`, business logic in `app/services`, data contracts in `app/schemas`, models in `app/models`, DB wiring in `app/db`, and infrastructure providers in `app/providers`.
- `main.py` re-exports the ASGI app for convenience entrypoints.
- `infra/` holds deployment assets (Docker and Nginx configs); root `docker-compose.yml` is for local Postgres.
- `docs/` contains architecture diagrams (`docs/architecture/`) and manual QA steps (`docs/manual-test-checklist.md`).
- `scripts/` contains VPS bootstrap/deploy helpers.

## Build, Test, and Development Commands
- `cp .env.example .env` then fill required values for local runs.
- `docker-compose up -d` starts the local Postgres container.
- `pip install -r requirements.txt` or `uv sync` installs dependencies (Python >= 3.13).
- `uvicorn app.main:app --reload` runs the API locally (or `uv run uvicorn app.main:app --reload`).

## Coding Style & Naming Conventions
- Follow existing Python style: 4-space indentation, PEP 8 conventions, and grouped imports (stdlib, third-party, local).
- Naming: `snake_case` for functions/vars, `PascalCase` for classes, `UPPER_CASE` for constants.
- No formatter/linter is configured; keep changes consistent with surrounding files.

## Task Intake & Clarifications
- Check `TODO.md` for pending changes and confirm which items to implement before starting.
- Review `docs/technical.md` for the hackathon technical task and follow it when possible; ask if requirements conflict or are unclear.
- If multiple TODOs exist, ask which items are in scope and their priority.
- Ask focused questions about implementation details (inputs/outputs, edge cases, constraints) when TODOs are ambiguous.
- Be skeptical of scope creep; prefer minimal changes that meet the requirement and avoid overengineering.
- When architectural choices are unclear, ask about expectations (e.g., DB schema impact, service boundaries, provider usage) and offer 2-3 options with tradeoffs.

## Branching & Commit Workflow
- Work on the long-lived `dev` branch; do not develop directly on `main`.
- Create short-lived feature branches off `dev` (e.g., `feature/auth-email`, `fix/billing-quota`).
- DO NOT create short-lived branches if the changes are small enough (can be done in a single small, concise, commit)
- Rebase feature branches onto `dev` before integration, then merge into `dev` with `--ff-only`.
- Keep `dev` current by rebasing onto `origin/main` (`git fetch origin` then `git rebase origin/main`); when releasing, fast-forward `main` to `dev`.
- Git history favors short, descriptive messages (e.g., `fix .envrc`, `Split workflow: build/push GHCR image & deploy`).
- Avoid merge commits, pull requests, and force pushes.

## Security & Configuration Tips
- Keep secrets in `.env` only; do not commit real credentials.
- Deployment secrets for GitHub Actions are listed in `README.md`; confirm they are set before pushing to `main`.
