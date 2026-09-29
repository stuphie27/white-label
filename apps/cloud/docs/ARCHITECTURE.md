# Architecture

Pirouette Cloud is the online companion to the offline-first Pirouette Event application.

## v1.2 data layer

- FastAPI web service on DigitalOcean App Platform
- SQLAlchemy 2 data access
- Psycopg 3 PostgreSQL driver
- Managed PostgreSQL in London
- Numbered schema migrations tracked in `schema_migrations`

Event records are cloud-owned. Original photographs, local processing, kiosks, printers and event-critical workflows remain on Pirouette Event.
