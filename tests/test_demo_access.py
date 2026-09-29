import base64
import sys
from types import ModuleType

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pytest

from saia.demo_access import DemoAccess, DemoCredentials
from saia import demo_server


USER = "preview-reader"
PASSWORD = "test-preview-secret-2026"


def authorization(user=USER, password=PASSWORD):
    return "Basic " + base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")


@pytest.fixture
def preview():
    app = FastAPI()
    calls = []

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE"])
    async def record(request: Request):
        calls.append((request.method, request.url.path, dict(request.headers)))
        return JSONResponse({"path": request.url.path}, headers={"Cache-Control": "public, max-age=1000"})

    credentials = DemoCredentials.from_environment({"HORIZON_DEMO_USER": USER, "HORIZON_DEMO_PASSWORD": PASSWORD})
    return app, credentials, calls


def assert_private(response):
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert "noindex" in response.headers["x-robots-tag"]
    assert response.headers["referrer-policy"] == "no-referrer"


@pytest.mark.parametrize("path", ["/", "/help", "/scout", "/public-signals", "/api/public-signals", "/health",
                                   "/docs", "/redoc", "/openapi.json", "/unknown", "/scout-results/demo"])
def test_every_path_authenticates_before_routing(preview, path):
    app, credentials, calls = preview
    with TestClient(DemoAccess(app, credentials)) as client:
        response = client.get(path)
    assert response.status_code == 401
    assert response.headers["www-authenticate"].endswith('charset="UTF-8"')
    assert_private(response)
    assert calls == []


@pytest.mark.parametrize("value", [
    "", "Bearer abc", "Basic", "Basic !!!!", "Basic YQ==", "Basic Og==",
    "Basic " + base64.b64encode(b"\xff:bad-utf8").decode(),
    "Basic " + base64.b64encode(b"reader:\xc0\xaf").decode(),
    "Basic " + "a" * 8193,
    authorization("wrong", PASSWORD), authorization(USER, "wrong-password-2026"),
])
def test_malformed_and_wrong_credentials_fail_without_exposing_values(preview, value):
    app, credentials, calls = preview
    with TestClient(DemoAccess(app, credentials)) as client:
        response = client.get("/health", headers={"Authorization": value})
    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required."}
    assert_private(response)
    assert calls == []


def test_duplicate_authorization_is_rejected(preview):
    app, credentials, calls = preview
    with TestClient(DemoAccess(app, credentials)) as client:
        response = client.get("/health", headers=[("Authorization", authorization()), ("Authorization", authorization())])
    assert response.status_code == 401
    assert calls == []


def test_unicode_credentials_and_colons_in_password_work_and_are_not_forwarded(preview):
    app, _, calls = preview
    user, password = "читатель", "длинный:пароль:для:демо"
    credentials = DemoCredentials.from_environment({"HORIZON_DEMO_USER": user, "HORIZON_DEMO_PASSWORD": password})
    with TestClient(DemoAccess(app, credentials)) as client:
        response = client.get("/help", headers={"Authorization": authorization(user, password)})
    assert response.status_code == 200
    assert_private(response)
    assert "authorization" not in calls[0][2]
    assert user not in repr(credentials) and password not in repr(credentials)


def test_both_constant_time_digest_comparisons_run_for_wrong_username(preview, monkeypatch):
    _, credentials, _ = preview
    from saia import demo_access
    original = demo_access.hmac.compare_digest
    comparisons = []

    def compare(left, right):
        comparisons.append((len(left), len(right)))
        return original(left, right)

    monkeypatch.setattr(demo_access.hmac, "compare_digest", compare)
    assert credentials.accepts([(b"authorization", authorization("wrong", PASSWORD).encode())]) is False
    assert comparisons == [(32, 32), (32, 32)]


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT"])
def test_every_non_read_method_is_denied_before_application(preview, method):
    app, credentials, calls = preview
    with TestClient(DemoAccess(app, credentials)) as client:
        response = client.request(method, "/api/public-signals", headers={"Authorization": authorization()})
    assert response.status_code == 405
    assert response.headers["allow"] == "GET, HEAD"
    assert_private(response)
    assert calls == []


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect", "/source-catalog",
                                   "/dangerous-get", "/scout/extra", "/health/", "/%2Fhealth",
                                   "/scout-results/demo/enrich", "/signals/demo/7/brief"])
def test_unknown_gets_and_full_api_schema_stay_closed(preview, path):
    app, credentials, calls = preview
    with TestClient(DemoAccess(app, credentials)) as client:
        response = client.get(path, headers={"Authorization": authorization()})
    assert response.status_code == 404
    assert_private(response)
    assert calls == []


@pytest.mark.parametrize("path", ["/help", "/scout", "/public-signals", "/api/public-signals?query=robot&limit=5",
                                   "/health", "/public-signals/jrc-2024-p113-041/brief?lang=ru"])
def test_reviewed_preview_reads_reach_application(preview, path):
    app, credentials, calls = preview
    with TestClient(DemoAccess(app, credentials)) as client:
        response = client.get(path, headers={"Authorization": authorization()})
    assert response.status_code == 200
    assert_private(response)
    assert len(calls) == 1 and calls[0][0] == "GET"


def test_head_reuses_reviewed_get_and_suppresses_body(preview):
    app, credentials, calls = preview
    with TestClient(DemoAccess(app, credentials)) as client:
        response = client.head("/health", headers={"Authorization": authorization()})
    assert response.status_code == 200 and response.content == b""
    assert_private(response)
    assert calls[0][0] == "GET"


def test_root_redirects_to_public_catalog_without_dispatching_internal_home(preview):
    app, credentials, calls = preview
    with TestClient(DemoAccess(app, credentials)) as client:
        response = client.get("/", headers={"Authorization": authorization()}, follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/public-signals"
    assert_private(response)
    assert calls == []


@pytest.mark.parametrize("path", ["/signals/demo-mission", "/scout-results/demo-mission?score_run_id=12"])
def test_saved_results_are_explicitly_opt_in(preview, path):
    app, credentials, calls = preview
    with TestClient(DemoAccess(app, credentials)) as client:
        assert client.get(path, headers={"Authorization": authorization()}).status_code == 404
    assert calls == []
    with TestClient(DemoAccess(app, credentials, allow_saved_results=True)) as client:
        response = client.get(path, headers={"Authorization": authorization()})
    assert response.status_code == 200


def test_websocket_cannot_bypass_read_only_gate(preview):
    from starlette.websockets import WebSocketDisconnect
    app, credentials, calls = preview
    with TestClient(DemoAccess(app, credentials)) as client:
        with pytest.raises(WebSocketDisconnect) as error:
            with client.websocket_connect("/health", headers={"Authorization": authorization()}):
                pass
    assert error.value.code == 1008
    assert calls == []


@pytest.mark.parametrize("user,password", [("", PASSWORD), ("reader:name", PASSWORD), ("reader\n", PASSWORD),
                                           (USER, "short"), (USER, " " * 16), (USER, "")])
def test_missing_or_weak_configuration_fails_closed(user, password):
    with pytest.raises(ValueError):
        DemoCredentials.from_environment({"HORIZON_DEMO_USER": user, "HORIZON_DEMO_PASSWORD": password})


@pytest.fixture
def configured_environment(monkeypatch, preview):
    monkeypatch.setenv("HORIZON_DEMO_USER", USER)
    monkeypatch.setenv("HORIZON_DEMO_PASSWORD", PASSWORD)
    for name in ("HORIZON_DEMO_READ_ONLY", "HORIZON_DEMO_ALLOW_SAVED_RESULTS", "HORIZON_DEMO_DATABASE_URL", "HORIZON_DEMO_PORT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SAIA_DATABASE_URL", "postgresql://working.invalid/working")
    module = ModuleType("saia.api")
    module.app = preview[0]
    monkeypatch.setitem(sys.modules, "saia.api", module)


def test_factory_removes_working_database_and_never_migrates(configured_environment, monkeypatch):
    from saia import db
    def no_migrations(*args, **kwargs):
        pytest.fail("Preview startup must not migrate or connect to a database")
    monkeypatch.setattr(db, "migrate", no_migrations)
    monkeypatch.setattr(db, "connect", no_migrations)
    guarded = demo_server.create_app()
    assert guarded.allow_saved_results is False
    assert "SAIA_DATABASE_URL" not in demo_server.os.environ


@pytest.mark.parametrize("setting", ["false", "0", "no", "maybe"])
def test_write_enabled_or_invalid_mode_cannot_start(configured_environment, monkeypatch, setting):
    monkeypatch.setenv("HORIZON_DEMO_READ_ONLY", setting)
    with pytest.raises(ValueError):
        demo_server.create_app()


@pytest.mark.parametrize("url", [None, "postgresql://working.invalid/working"])
def test_saved_mode_requires_separate_explicit_database(configured_environment, monkeypatch, url):
    monkeypatch.setenv("HORIZON_DEMO_ALLOW_SAVED_RESULTS", "true")
    if url:
        monkeypatch.setenv("HORIZON_DEMO_DATABASE_URL", url)
    with pytest.raises(ValueError):
        demo_server.create_app()


def test_saved_mode_requests_read_only_transactions_without_connecting(configured_environment, monkeypatch):
    from psycopg.conninfo import conninfo_to_dict
    from saia import db
    def no_connection(*args, **kwargs):
        pytest.fail("Creating the preview must not contact the database")
    monkeypatch.setattr(db, "connect", no_connection)
    monkeypatch.setenv("HORIZON_DEMO_ALLOW_SAVED_RESULTS", "true")
    monkeypatch.setenv("HORIZON_DEMO_DATABASE_URL", "postgresql://demo.invalid/demo?options=-c%20default_transaction_read_only%3Doff")
    guarded = demo_server.create_app()
    assert guarded.allow_saved_results is True
    settings = conninfo_to_dict(demo_server.os.environ["SAIA_DATABASE_URL"])
    assert settings["host"] == "demo.invalid"
    assert settings["options"] == "-c default_transaction_read_only=on"


def test_invalid_database_diagnostics_do_not_expose_secret(configured_environment, monkeypatch):
    monkeypatch.setenv("HORIZON_DEMO_ALLOW_SAVED_RESULTS", "true")
    monkeypatch.setenv("HORIZON_DEMO_DATABASE_URL", "invalid-dsn-with-sensitive-password")
    with pytest.raises(ValueError, match="HORIZON_DEMO_DATABASE_URL is invalid") as error:
        demo_server.create_app()
    assert "sensitive-password" not in str(error.value)


def test_entrypoint_uses_own_local_port_without_access_logging(configured_environment, monkeypatch):
    import uvicorn
    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.append((app, kwargs)))
    monkeypatch.setenv("HORIZON_DEMO_PORT", "8093")
    demo_server.main()
    assert len(calls) == 1
    assert isinstance(calls[0][0], DemoAccess)
    assert calls[0][1] == {"host": "127.0.0.1", "port": 8093, "reload": False,
                           "proxy_headers": False, "access_log": False}
