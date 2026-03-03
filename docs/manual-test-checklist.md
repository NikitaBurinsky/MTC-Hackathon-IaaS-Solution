# Manual QA Checklist (Swagger)

Use `/docs` to validate the MVP acceptance scenarios:

1. Register/login returns JWT with tenant context.
2. Tenant A cannot read Tenant B resources.
3. New tenant has `100` credits and plan `1 vCPU / 1 GB`.
4. First `POST /instances` succeeds, second exceeds quota.
5. `POST /instances` returns `202` + `provisioning_operation_id`.
6. `GET /instances/operations/{id}` reaches `SUCCESS`.
7. `POST /instances/{id}/action` with `start` on RUNNING returns `200` no-op.
8. `POST /tasks/execute` on RUNNING + STOPPED instances yields `PARTIAL_SUCCESS`.
9. `DELETE /instances/{id}` with running task returns `409`.
10. `GET /billing/usage` charges only RUNNING time windows.
11. API startup fails when Docker daemon is unavailable.
12. DB workflow bootstraps PostgreSQL; app workflow deploys API+Nginx on push to `main`.
13. HTTPS is reachable with Let's Encrypt certificate.

