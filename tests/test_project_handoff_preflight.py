from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

def load():
    spec = spec_from_file_location("project_handoff_preflight", Path("scripts/project_handoff_preflight.py"))
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_preflight_requires_full_runtime_and_does_not_claim_final_delivery():
    report = load().inspect()
    names = {row["path"] for row in report["members"]}
    assert not report["missing_required_source_files"]
    assert not report["missing_required_package_members"]
    assert {"infra/docker-compose.yml", "infra/Dockerfile", "saia/api.py", "migrations/065_large_aggregators.sql"} <= names
    assert ".env" not in names and ".env.example" in names
    assert not report["complete_tz_compliance_proven"] and not report["final_package_created"]
    assert not report["database_exported"] and not report["clean_machine_restore_verified"]


def test_known_secrets_block_inclusion_without_disclosing_values(tmp_path, monkeypatch):
    module = load()
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "test"\n')
    (tmp_path / "README.md").write_text("private-test-token")
    (tmp_path / ".env").write_text("LENS_API_TOKEN=private-test-token\n")
    report = module.inspect(tmp_path)
    assert not report["known_secret_check_passed"]
    assert "private-test-token" not in str(report)
    assert "README.md" not in {row["path"] for row in report["members"]}
