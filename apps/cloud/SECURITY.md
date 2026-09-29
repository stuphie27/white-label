# Security policy

- Never commit `.env`, passwords, API keys, customer data, databases, photographs, or order exports.
- Production requires HTTPS and a strong random `PIROUETTE_SECRET_KEY`.
- Local Pirouette Event routes and hardware controls are intentionally absent.
- Report suspected exposure immediately and rotate affected secrets before redeployment.
- Customer-facing modules remain disabled until their authentication, storage and data models have been reviewed.
