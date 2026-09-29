# Deployment

1. Deploy v1.2.1 with the existing authentication variables only.
2. Do not add `DATABASE_URL` during this deployment.
3. Confirm `/healthz`, `/readyz`, `/staff`, and `/staff/database`.
4. Database configuration and migrations are a separate controlled step.

This release intentionally avoids database work during production startup.
