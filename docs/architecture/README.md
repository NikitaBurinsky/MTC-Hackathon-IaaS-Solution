# Architecture Diagrams

This folder contains Mermaid (`.mmd`) diagrams that document the IaaS MVP
architecture and key flows. Use them when reviewing or changing core behavior.

## Diagram Index

- `context.mmd`: system context (SPA, API, Docker, Postgres, Nginx, VPS).
- `container.mmd`: FastAPI monolith components and dependencies.
- `tenant-isolation.mmd`: JWT -> dependency -> tenant-scoped queries.
- `sequence-create-instance.mmd`: instance provisioning flow.
- `sequence-task-execute.mmd`: task execution flow.

## Rendering

Open `.mmd` files in a Mermaid-capable viewer (GitHub preview or VS Code Mermaid
extension). You can also render locally with Mermaid CLI if needed.

## Keeping In Sync

If you change endpoints, service boundaries, or data flows, update the matching
diagram(s) to keep architecture docs accurate.
