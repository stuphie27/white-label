# Pirouette Cloud

Pirouette Cloud is the online companion to Pirouette Event. It is offline-first: the event Mac remains authoritative and Cloud receives metadata and temporary transfer instructions when connectivity is available.

## v1.8.0

This release adds the first authenticated Desktop Sync API. It synchronises events, galleries, transfer jobs and resumable file manifests. It does **not** upload or permanently store photographs.

### Required production setting

Add an encrypted DigitalOcean environment variable before using the sync endpoints:

```text
PIROUETTE_SYNC_API_KEY=<long random secret>
```

Generate one on macOS with:

```bash
openssl rand -hex 48
```

The API is available under `/api/sync/v1`.
