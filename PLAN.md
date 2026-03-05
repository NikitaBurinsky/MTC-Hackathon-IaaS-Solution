# SSH Access Feature Plan

## Goals
- Provide tenants with SSH access to their instances.
- Return a username/password on initial enablement or on reset.
- Keep the implementation simple, Docker-native, and easy to debug.

## Non-Goals (for now)
- SSH key management, MFA, or fine-grained IAM.
- Multi-user access per instance.
- Long-term password retrieval (passwords are returned only on create/reset).

## Constraints & Assumptions
- Instances are Docker containers created via `DockerProvider`.
- No DB migrations framework (schema changes require recreate or manual SQL).
- Images are admin-seeded (users do not create their own images).

## Approaches Considered
1) **SSH-enabled base image (recommended)**
   - Use an image that already has `sshd` installed and configured.
   - Pass `SSH_USER`/`SSH_PASSWORD` env vars at container creation.
   - Pros: simplest runtime, fewer failure modes, easy to debug.
   - Cons: all instance images must be SSH-capable (admin-controlled).

2) **Install SSH on first boot via `docker exec`**
   - Run `apt-get`/`apk` inside container, configure sshd, create user.
   - Pros: works with arbitrary images.
   - Cons: slower, more fragile, distro-specific logic.

3) **Bastion/jump host or exec-proxy**
   - A separate SSH gateway container or API-level exec proxy.
   - Pros: centralized control.
   - Cons: more moving parts and complexity.

## Recommended Approach
Use **SSH-enabled base images** with environment-based user/password injection.
This minimizes runtime complexity and keeps the compute pipeline predictable.
If we later need arbitrary images, we can add option (2) as a fallback.

## Data Model Changes
Add fields to `Instance` (app/models/models.py):
- `ssh_port: int` (host port mapped to container `22/tcp`)
- `ssh_username: str`
- `postgres_username: str | None` (only for postgres image)

Note: passwords are **not stored at all**. Plaintext is returned only in API
responses when credentials are generated or reset.

## Configuration
Add envs in `app/core/config.py`:
- `SSH_PORT_RANGE_START` (default: `22000`)
- `SSH_PORT_RANGE_END` (default: `22999`)
- `SSH_USERNAME_PREFIX` (default: `user`)
- `SSH_DEFAULT_HOST` (default: `api.formatis.online`)

These control port allocation and the host returned to clients.

## Image Seeding
Update `app/db/init_db.py` to seed 4 SSH-capable images (MVP):
- `ubuntu`: custom image with `sshd` running (base `ubuntu:22.04`).
- `alpine`: custom image with `sshd` running (base `alpine:3.20`).
- `postgres`: custom image that runs `postgres` **and** `sshd`.
- `docker`: custom image that runs `dockerd` **and** `sshd`.

Implementation detail:
- Create Dockerfiles in `infra/docker/ssh-images/` (one per image).
- Use a small wrapper entrypoint to start `sshd` and then `exec` the original
  service entrypoint:
  - Postgres: start sshd, then `exec docker-entrypoint.sh postgres`.
  - Docker: start sshd, then `exec dockerd-entrypoint.sh` (may require `--privileged`).
- Keep admin-only control; users do not create images.

## Docker Provider Changes
Extend `DockerProvider.create_instance(...)`:
- Accept optional `ports` mapping (e.g., `{"22/tcp": host_port}`).
- Accept optional `environment` dict (e.g., `SSH_USER`, `SSH_PASSWORD`).
- Continue to set CPU/RAM limits as now.
- Allow optional `privileged` flag for the `docker` image (required for `dockerd`).

If using `linuxserver/openssh-server`, set envs:
- `USER_NAME`, `USER_PASSWORD`, `PASSWORD_ACCESS=true`.
- Ensure `sshd` is already started by the image entrypoint.

## Service Flow
### Credential Generation
Add helper in `ComputeService` or new `InstanceAccessService`:
- `generate_ssh_credentials(tenant_id, instance_id)`:
  - Ensure instance exists and is RUNNING.
  - Allocate a free host port in the configured range.
  - Generate username (e.g., `user{user_id}` or `tenant{tenant_id}`).
  - Generate strong random password (16+ chars).
  - Store `ssh_username`, `ssh_port` (no password stored).

Add postgres credential helper for postgres image:
- Generate `POSTGRES_USER` and `POSTGRES_PASSWORD` on create.
- Store `postgres_username` only; return plaintext password once.

### Container Provisioning
During instance creation:
- If SSH is enabled globally (default) or per-image:
  - Create container with `ports={"22/tcp": ssh_port}`.
  - Pass envs to image for user creation.
  - If image is `docker`, run container with `privileged=True` so `dockerd` starts.
- If SSH setup fails, mark instance `ERROR` and return a clear message.
 - If image is postgres, pass `POSTGRES_USER`/`POSTGRES_PASSWORD` envs.

### Reset Flow
Implement `reset_ssh_credentials(...)`:
- Regenerate password.
- Apply password inside container (either via env + restart, or `docker exec`
  `chpasswd` depending on image).
- Return plaintext password in response.

## API Changes
Add endpoints in `app/api/v1/routers/instances.py`:
- `GET /instances/{id}/ssh`:
  - Returns `{host, port, username}` (no password).
  - 404 if SSH not enabled or instance not RUNNING.
- `POST /instances/{id}/ssh/reset`:
  - Returns `{host, port, username, password}`.
  - 409 if instance not RUNNING.

Instance creation response should include initial credentials:
- Extend `InstanceCreateAccepted` to include `{ssh_host, ssh_port, ssh_username, ssh_password}`.
- If postgres image, include `{postgres_username, postgres_password}`.
- This is a response shape change; update frontend + docs.
- `GET /instances/{id}` response should include `ssh_host`, `ssh_port`,
  `ssh_username`, and `postgres_username` (no passwords).

## Security Notes
- Disable root login in the SSH image.
- Enable password auth but only for the generated user.
- Password is returned only on reset or first enable; not stored.

## Testing & Verification (Manual)
- Create instance; verify SSH port is mapped (`docker ps`).
- Call `POST /instances/{id}/ssh/reset` and connect with returned creds.
- Confirm `GET /instances/{id}/ssh` returns host/port/username only.
- Verify wrong creds fail; reset then succeed.

## Rollout Plan
1) Add schema changes, update seeding image refs.
2) Update Docker provider and compute flow.
3) Add SSH endpoints and responses.
4) Update README + manual test checklist.
5) Recreate DB (if needed) and redeploy.

## MVP Decisions (Confirmed)
- SSH enabled for the 4 MVP images: ubuntu, postgres, docker, alpine.
- Username pattern: `tenant{tenant_id}`.
- Initial credentials returned on instance creation; reset endpoint still supported.
- Postgres and Docker images must run their services alongside `sshd`.
