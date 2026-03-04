# Manual QA Checklist (Swagger)

Use `/docs` to validate the MVP acceptance scenarios:

1. Register/login accepts JSON and returns JWT with tenant context; `access_token` cookie is set.
2. Tenant A cannot read Tenant B resources.
3. New tenant has `100` credits and plan `1 vCPU / 1 GB`.
4. First `POST /instances` succeeds, second exceeds quota.
5. `POST /instances` returns `202` + `provisioning_operation_id`.
6. `GET /instances/operations/{id}` reaches `SUCCESS`.
7. `POST /instances/{id}/action` with `start` on RUNNING returns `200` no-op.
8. `POST /tasks/execute` on RUNNING + STOPPED instances yields `PARTIAL_SUCCESS`.
9. `DELETE /instances/{id}` with running task returns `409`.
10. `GET /billing/usage` returns per-slice CPU/RAM + base charges for RUNNING instances.
11. API startup fails when Docker daemon is unavailable.
12. DB workflow bootstraps PostgreSQL; app workflow deploys API+Nginx on push to `main`.
13. HTTPS is reachable with Let's Encrypt certificate.
14. Catalog endpoints are available: `GET /flavors`, `GET /images`, `GET /plans`.
15. `GET /images` does not return any internal `docker_image_ref`.
16. Validation errors return 422 with a concise error message (no details array).
17. Tenant balance decreases every 60 seconds while instances are RUNNING.
18. When balance <= 0, all tenant RUNNING instances stop automatically.
