import io
from pathlib import Path
from urllib.error import HTTPError

import pytest

from saia.software_evidence import SoftwareQuery, fetch, parse_response, request_url


FIXTURES = Path(__file__).parent / "fixtures"


def query(**changes):
    values = dict(topic_id="agents", system="pypi", package="agentic-fixture",
                  start="2025-01-01", end="2026-09-21", as_of="2026-09-21",
                  max_versions=10)
    values.update(changes)
    return SoftwareQuery(**values)


def test_deps_url_uses_exact_encoded_identity():
    assert request_url(query(system="npm", package="@scope/agent")) == (
        "https://api.deps.dev/v3/systems/npm/packages/%40scope%2Fagent"
    )


def test_deps_rejects_unknown_ecosystem_and_future_window():
    with pytest.raises(ValueError, match="экосистема"):
        query(system="unknown").validate()
    with pytest.raises(ValueError, match="Некорректный"):
        query(end="2026-09-22").validate()


def test_deps_fixture_filters_post_as_of_and_does_not_claim_adoption():
    payload = (FIXTURES / "deps_dev_package.json").read_bytes()
    result = parse_response(query(), payload, "2026-09-21T10:00:00+00:00",
                            request_url(query()))
    assert result["status"] == "complete"
    assert result["source_version_count"] == 3
    assert result["versions_through_as_of_count"] == 2
    assert result["observed_version_count"] == 2
    assert result["first_release_date_through_as_of"] == "2025-01-10"
    assert result["current_inventory_contains_post_as_of_versions"] is True
    assert result["adoption_established"] is False
    assert result["retrospective_safe"] is False
    assert result["scientific_score_modified"] is False


def test_deps_404_is_exact_package_negative_not_technology_zero(monkeypatch):
    def missing(*_args, **_kwargs):
        raise HTTPError("https://example.test", 404, "missing", {}, io.BytesIO(b"missing"))
    monkeypatch.setattr("saia.software_evidence.urlopen", missing)
    result = fetch(query())
    assert result["status"] == "package_not_found"
    assert result["observations"] == []
    assert result["observed_version_count"] == 0
    assert result["missing_is_zero"] is False

