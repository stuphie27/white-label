# Operations

- `/healthz` confirms the process is running.
- `/readyz` confirms both the process and database are ready.
- If `/readyz` returns 503, inspect Runtime Logs before changing credentials.
- Never paste `DATABASE_URL`, admin hashes or secret keys into tickets or chat.
- Managed PostgreSQL backups and App Platform automatic rollback protect the initial production release.
