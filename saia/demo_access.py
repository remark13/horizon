"""Optional, fail-closed ASGI gate for a read-only Horizon preview.

This gate does not anonymize records. Saved-result access must use a separate
demonstration database containing no production or personal data. Route changes
need a fresh side-effect review before being added to the explicit allowlist.
"""
from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import re
from typing import Any, Awaitable, Callable, Mapping


ASGIApp = Callable[[dict, Callable, Callable], Awaitable[None]]
SECURITY_HEADERS = (
    (b"cache-control", b"no-store, max-age=0"),
    (b"x-robots-tag", b"noindex, nofollow, noarchive"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-content-type-options", b"nosniff"),
)

# Audited: these handlers return static HTML or the pinned public JSON catalog;
# public brief rendering reads pinned local references and never fetches sources.
# Docs/schema are deliberately omitted: they describe the full internal API.
PREVIEW_PATHS = frozenset({"/help", "/scout", "/public-signals", "/api/public-signals", "/health"})
PUBLIC_BRIEF = re.compile(r"/public-signals/[a-z0-9]+(?:-[a-z0-9]+)+/brief\Z")
# Audited chain: candidates.export_cards/current_run_id use SELECT queries;
# scout_results.build combines those cards, read_saved_all, for_candidate, and
# pure calculations. collect/enrich/generate/write helpers are never invoked.
SAVED_RESULTS = re.compile(r"/(?:signals|scout-results)/[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}\Z")


@dataclass(frozen=True)
class DemoCredentials:
    user_digest: bytes = field(repr=False)
    password_digest: bytes = field(repr=False)

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> DemoCredentials:
        user = environment.get("HORIZON_DEMO_USER", "")
        password = environment.get("HORIZON_DEMO_PASSWORD", "")
        if not user.strip() or len(user) > 128 or ":" in user or any(ord(c) < 32 or ord(c) == 127 for c in user):
            raise ValueError("HORIZON_DEMO_USER must be a nonempty username without colons or control characters.")
        if len(password) < 16 or len(password) > 1024 or not password.strip():
            raise ValueError("HORIZON_DEMO_PASSWORD must contain between 16 and 1024 characters.")
        try:
            return cls(hashlib.sha256(user.encode("utf-8")).digest(),
                       hashlib.sha256(password.encode("utf-8")).digest())
        except UnicodeError:
            raise ValueError("Demo credentials must be valid UTF-8.") from None

    def accepts(self, headers: list[tuple[bytes, bytes]]) -> bool:
        values = [value for name, value in headers if name.lower() == b"authorization"]
        if len(values) != 1 or len(values[0]) > 8192:
            return False
        scheme, separator, token = values[0].partition(b" ")
        if scheme.lower() != b"basic" or not separator or not token:
            return False
        try:
            decoded = base64.b64decode(token, validate=True).decode("utf-8")
        except (ValueError, UnicodeError, binascii.Error):
            return False
        user, separator, password = decoded.partition(":")
        if not separator:
            return False
        # Both comparisons always run over fixed-size digests; neither credential
        # nor an Authorization header is included in diagnostics or responses.
        user_ok = hmac.compare_digest(hashlib.sha256(user.encode("utf-8")).digest(), self.user_digest)
        password_ok = hmac.compare_digest(hashlib.sha256(password.encode("utf-8")).digest(), self.password_digest)
        return user_ok & password_ok


class DemoAccess:
    """Protect every HTTP path before routing; deny every modifying method.

    No write-enabled mode exists. Basic authentication is suitable only behind
    HTTPS for any future remote access. This class does not provide a tunnel,
    rate limiting, per-user permissions, or data anonymization.
    """

    def __init__(self, app: ASGIApp, credentials: DemoCredentials, *, allow_saved_results: bool = False):
        self.app = app
        self.credentials = credentials
        self.allow_saved_results = allow_saved_results

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] != "http":
            return

        is_head = scope.get("method") == "HEAD"

        async def protected_send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                replaced = {name for name, _ in SECURITY_HEADERS}
                headers = [(name, value) for name, value in message.get("headers", [])
                           if name.lower() not in replaced]
                message = {**message, "headers": headers + list(SECURITY_HEADERS)}
            elif message["type"] == "http.response.body" and is_head:
                message = {**message, "body": b""}
            await send(message)

        async def reject(status: int, detail: str, headers: list | None = None) -> None:
            content = json.dumps({"detail": detail}).encode("utf-8")
            await protected_send({"type": "http.response.start", "status": status,
                                  "headers": [(b"content-type", b"application/json"),
                                              (b"content-length", str(len(content)).encode())] + (headers or [])})
            await protected_send({"type": "http.response.body", "body": content})

        # Authenticate even unknown paths and forbidden methods, including
        # /health, /docs, and /openapi.json, before disclosing route availability.
        if not self.credentials.accepts(scope.get("headers", [])):
            await reject(401, "Authentication required.",
                         [(b"www-authenticate", b'Basic realm="Horizon preview", charset="UTF-8"')])
            return
        if scope.get("method") not in {"GET", "HEAD"}:
            await reject(405, "This preview is read-only; changes and new searches are disabled.",
                         [(b"allow", b"GET, HEAD")])
            return
        path = scope.get("path", "")
        if path == "/":
            await protected_send({"type": "http.response.start", "status": 307,
                                  "headers": [(b"location", b"/public-signals"), (b"content-length", b"0")]})
            await protected_send({"type": "http.response.body", "body": b""})
            return
        allowed = (path in PREVIEW_PATHS or (len(path) <= 130 and PUBLIC_BRIEF.fullmatch(path))
                   or (self.allow_saved_results and SAVED_RESULTS.fullmatch(path)))
        if not allowed:
            await reject(404, "This route is not available in the preview.")
            return
        if len(scope.get("query_string", b"")) > 4096:
            await reject(414, "Preview query is too long.")
            return
        # Upstream handlers have no reason to inspect the shared credentials.
        # HEAD reuses only audited GET handlers and suppresses response bodies.
        forwarded = {**scope, "method": "GET", "headers": [
            (name, value) for name, value in scope.get("headers", []) if name.lower() != b"authorization"
        ]}
        await self.app(forwarded, receive, protected_send)
