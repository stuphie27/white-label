## 1.6.4 — 2026-08-07

- Unified the favourites extension with the normal customer basket/checkout flow.
- Removed favourites-stage payment selection and added removable £15 extension handling.
- Preserved free 5-day favourites when the extension is removed or not purchased.

# Changelog

## 1.8.0 - Offline Sync API Foundation

- Added authenticated, versioned sync API endpoints for Pirouette Event.
- Added idempotent event and gallery upserts using stable local source references.
- Added transfer creation, file-manifest registration and resume-state reporting.
- Added per-file progress metadata without storing photograph bytes on the web service.
- Enforced the existing five-day transfer expiry policy.
- Added migration `006_sync_api_foundation`.
- Added optional `PIROUETTE_SYNC_API_KEY`; the sync API remains disabled until configured.


## 1.5.0

- Added a seven-step event production checklist.
- Added one-click checklist completion controls to event detail pages.
- Added checklist progress percentage and completion bar.
- Added dashboard counts for production-ready events and upcoming events needing attention.
- Added migration `003_event_production_checklist`.
- Kept duplicated events as clean draft records with an empty checklist.

# Changelog

## 1.3.0 - Pirouette Experience and Event Operations

- Aligns the staff dashboard closely with the offline Pirouette Live Event Control design.
- Adds live-style event queue cards, event metrics, quick actions, event status summaries and system health.
- Adds event search by name or venue and filtering by status.
- Improves event detail, edit and archive workflows for desktop and iPad.
- Keeps all scanner, printer, SSD, kiosk and SumUp Solo controls local-only.
- Does not change the database schema or DigitalOcean environment configuration.

## 1.2.2 — Visual Alignment and Event Shell

- Introduces a shared Pirouette staff design system based on the Event application.
- Adds the familiar dark sidebar, Sophie’s Photography branding, operational cards, buttons, forms, badges and tablet layout.
- Restyles Dashboard, Events, Event Detail, Event Form and System Status.
- Shows recent events on the Dashboard when PostgreSQL is available.
- Keeps Event-only hardware controls out of Cloud.

## 1.2.1

- Prevent database configuration from blocking application startup.
- Keep `/readyz` healthy while reporting database state separately.
- Add protected database-status screen.
- Disable Events gracefully until PostgreSQL is verified.
- Stop automatic production migrations during web-service startup.

## 1.2.0

- Initial event-management and PostgreSQL foundation.

## 1.2.3 - Stable Staff Dashboard

- Rebuilt from the current GitHub repository supplied by the product owner.
- Prevents the staff dashboard from querying an uninitialised database schema.
- Separates database connection health from Pirouette table readiness.
- Adds a protected one-time database initialisation control.
- Adds a branded 500 error page and server-side error logging.
- Removes Python cache files and adds a complete `.gitignore`.
- Keeps local Event hardware and workflows excluded from Cloud.

## 1.4.0 - Event Operations
- Added client/school, principal/contact, lead photographer and event type fields.
- Added arrival, photography start and photography finish times.
- Added migration `002_event_operations_fields` for existing PostgreSQL databases.
- Expanded event search to client and photographer names.
- Preserved resilient database setup: the app remains available until staff initialise the new schema.

## 1.6.2 — 2026-08-07
- Combined basket and checkout into the Pirouette customer flow.
- Added in-gallery basket drawer and return-to-gallery basket behavior.
- Added v1.6.2 Cloud customer visual polish.

## 1.6.3 — 2026-08-07
- Added a dedicated customer favourites page with gallery/basket parity.
- Added saved-state hearts and a proper Favourites destination in customer navigation.
- Added professional HTML email styling for secure digital delivery and reminder emails, retaining plain-text fallbacks.

## 1.6.9 — 2026-08-07
- Customer Video navigation now requires at least one ready video asset; empty mirrored video folders are not advertised.
