## Project TODOs
- Enforce per-tenant uniqueness for instance and script names; return a 409 when creating/updating duplicates to avoid ambiguous targeting in the UI.
- Reject task execution when any requested instance is not RUNNING (409 with a clear message) so tasks fail fast instead of creating doomed runs.
- Add warning-level logging for swallowed exceptions (task execution, instance cleanup, billing stop failures) to avoid silent failures during operations.

## Confirmed Decisions
- Hide `docker_image_ref` from public `GET /images` responses.
- Do not add an `is_active` flag for flavors (keep all visible).
- Add a read-only `/plans` endpoint.
- For `init_db` defaults: implement upsert if simple; otherwise keep insert-only behavior.
- Networks must have unique names per tenant and disallow CIDR overlaps.
- Add a global error handler with a standard error schema.
- Add script/task validation without imposing length limits.

## Agent-Added TODOs
- (none at the moment)
