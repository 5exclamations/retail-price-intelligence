# Security

This is a portfolio project that runs on synthetic data. Still:

* **No secrets in the repository.** Configuration is by environment variable; `.env.example` holds placeholders only. CI scans the full history with gitleaks and audits dependencies with pip-audit on every push, pull request and weekly.
* **Write operations fail closed.** The API's only write endpoint returns 503 until `RPI_API_KEY` (at least 16 characters) is set and 401 without the exact key. The dashboard's review buttons are disabled unless `RPI_DASHBOARD_ALLOW_REVIEW=1`.
* **Development credentials** in `docker-compose.yml` (`rpi` / `rpi`) are for local use only; the Postgres port is published for convenience and must not be exposed on a shared host.
* **No authentication, TLS or rate limiting in the app.** Put a gateway in front of anything that is reachable beyond localhost.

Report a vulnerability by opening a private security advisory on the repository.
