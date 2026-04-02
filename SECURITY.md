# Security

## Reporting issues

If you discover a security vulnerability, please open a **private** advisory or contact the maintainers with enough detail to reproduce the issue. Do not post exploit details in public issues before a fix is available.

## Hub exposure

The FastAPI hub is intended for **trusted local networks**. By default it listens on all interfaces (`0.0.0.0:8000`). Anyone who can reach the hub can:

- Use BLE provisioning and bot APIs.
- **Read or set API keys** via `GET`/`POST /api/hub/settings` unless you protect the deployment.

For production or shared networks:

- Bind to **localhost** only, or
- Put the hub behind a **reverse proxy** with authentication, or
- Inject secrets with **environment variables** (they override file-based secrets) and restrict network access with firewalls.

Secrets on disk live under the hub data directory as `hub_secrets.json` (see `OMNIBOT_DATA_DIR` in the README). Clock and Maps location live in `hub_app_settings.json` (not secret, but sensitive). Restrict file permissions on the host.

## Maps and third-party keys

The Google Maps JavaScript API key is exposed to the browser by design for the Maps widget. **Gemini** and other server keys must never be shipped in frontend bundles; configure them via environment variables or Hub settings (stored server-side).
