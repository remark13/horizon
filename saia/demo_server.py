"""Optional local preview: ``python -m saia.demo_server``.

Required: HORIZON_DEMO_USER and HORIZON_DEMO_PASSWORD (at least 16 characters).
HORIZON_DEMO_PORT defaults to 8083; the listener is always 127.0.0.1. There is
no tunnel, migration, worker, or write-enabled mode. HORIZON_DEMO_READ_ONLY may
be omitted or true; false is rejected rather than silently enabling writes.

By default only static pages and the pinned public catalog/briefs are exposed.
The scout HTML is a preview: new searches, reviews, enrichment, export routes,
and internal API documentation are unavailable.

Saved-result reading is separately opt-in: HORIZON_DEMO_ALLOW_SAVED_RESULTS=true
requires HORIZON_DEMO_DATABASE_URL. Use a prepared, separate demonstration
database with a SELECT-only database account and no production/personal data.
This gate does not anonymize data, and a different URL does not prove database
isolation. The supplied URL never falls back to the working SAIA_DATABASE_URL;
the demo connection additionally requests read-only PostgreSQL transactions.
Any later remote exposure requires HTTPS and separately authorized deployment.
"""
from __future__ import annotations

import os
from typing import Mapping

from saia.demo_access import DemoAccess, DemoCredentials


def _flag(environment: Mapping[str, str], name: str, default: bool) -> bool:
    value = environment.get(name, str(default)).strip().lower()
    if value not in {"true", "false", "1", "0", "yes", "no"}:
        raise ValueError(f"{name} must be true or false.")
    return value in {"true", "1", "yes"}


def create_app() -> DemoAccess:
    """Uvicorn factory; validate configuration before importing the application."""
    credentials = DemoCredentials.from_environment(os.environ)
    if not _flag(os.environ, "HORIZON_DEMO_READ_ONLY", True):
        raise ValueError("The demo entrypoint supports read-only previews only.")
    saved_results = _flag(os.environ, "HORIZON_DEMO_ALLOW_SAVED_RESULTS", False)
    if saved_results:
        demo_url = os.environ.get("HORIZON_DEMO_DATABASE_URL", "")
        if not demo_url.strip():
            raise ValueError("Saved-result previews require a separate HORIZON_DEMO_DATABASE_URL.")
        if demo_url == os.environ.get("SAIA_DATABASE_URL"):
            raise ValueError("The demo database must not reuse the working SAIA_DATABASE_URL.")
        from psycopg.conninfo import make_conninfo
        try:
            readonly_url = make_conninfo(demo_url, options="-c default_transaction_read_only=on")
        except Exception:
            # Driver diagnostics may echo the connection string, including its
            # password. Never propagate those diagnostics from this entrypoint.
            raise ValueError("HORIZON_DEMO_DATABASE_URL is invalid.") from None
        os.environ["SAIA_DATABASE_URL"] = readonly_url
    else:
        # In this separate process even an accidentally introduced database read
        # cannot silently connect to an inherited working database.
        os.environ.pop("SAIA_DATABASE_URL", None)
    from saia.api import app
    return DemoAccess(app, credentials, allow_saved_results=saved_results)


def main() -> None:
    try:
        port = int(os.environ.get("HORIZON_DEMO_PORT", "8083"))
    except ValueError:
        raise ValueError("HORIZON_DEMO_PORT must be an integer from 1 to 65535.") from None
    if not 1 <= port <= 65535:
        raise ValueError("HORIZON_DEMO_PORT must be an integer from 1 to 65535.")
    app = create_app()
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=port, reload=False,
                proxy_headers=False, access_log=False)


if __name__ == "__main__":
    main()
