"""Repository startup must be isolated and keep its bundled catalogs visible."""
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def configuration():
    return yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))


def test_starter_has_no_host_data_or_preexisting_container_dependency():
    compose = configuration()
    assert set(compose["services"]) == {"db", "app", "worker"}
    assert "ports" not in compose["services"]["db"]
    for service in compose["services"].values():
        assert "container_name" not in service
        for volume in service.get("volumes", []):
            assert volume.split(":", 1)[0] in compose["volumes"]
        for port in service.get("ports", []):
            assert port.startswith("127.0.0.1:")
    for filename in ("compose.yaml", "infra/Dockerfile.repository", ".env.repository.example"):
        assert "/Users/" not in (ROOT / filename).read_text(encoding="utf-8")


def test_empty_runtime_volumes_do_not_hide_bundled_reference_catalogs():
    compose = configuration()
    app, worker = (compose["services"][name] for name in ("app", "worker"))
    assert app["volumes"] == worker["volumes"]
    targets = {value.split(":")[1] for value in app["volumes"]}
    assert targets == {"/opt/horizon/data/raw", "/opt/horizon/data/processed",
                       "/opt/horizon/reports/generated", "/models"}
    assert app["working_dir"] == worker["working_dir"] == "/opt/horizon"
    assert app["command"] == ["python", "-m", "saia.server"]
    assert worker["depends_on"]["app"]["condition"] == "service_healthy"


def test_new_worker_allows_model_download_and_explicit_live_retrieval():
    environment = configuration()["services"]["worker"]["environment"]
    assert environment["SAIA_ALLOW_LIVE_ARXIV"] == "1"
    assert environment["HF_HUB_OFFLINE"] == "0"
    assert environment["TRANSFORMERS_OFFLINE"] == "0"
    assert environment["SAIA_TORCH_DEVICE"] == "cpu"
    assert not {"SAIA_ARXIV_MIRROR_DIR", "SAIA_ARXIV_TRIGRAM_INDEX_DIR",
                "SAIA_THEMATIC_ARXIV_CACHE_DIR", "SAIA_PREPARED_OPENALEX_DIR"} & environment.keys()


def test_bundled_public_catalog_is_readable_without_database_or_network(monkeypatch):
    monkeypatch.delenv("SAIA_DATABASE_URL", raising=False)
    from saia.public_signals import search
    from saia.query_planning import THEMES_PATH, BRIDGES_PATH

    result = search(limit=2)
    assert result["records"]
    assert result["scientific_score_modified"] is False
    assert result["not_gold_labels"] is True
    assert THEMES_PATH.is_file() and BRIDGES_PATH.is_file()
