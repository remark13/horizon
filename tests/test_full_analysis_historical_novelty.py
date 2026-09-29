from pathlib import Path

from saia.full_analysis import _prepare_historical_novelty, job_payload


def profile():
    return {"controlled_search_plan": {
        "date_from": "2023-01-01", "as_of_date": "2026-01-01",
        "included_terms": ["tissue engineering"], "exclusions": [],
    }}


def audit(**changes):
    value = {
        "selection_engine": "verified_thematic_target_pack",
        "selected_target_packs": ["health--tissue"],
        "cache_manifest_sha256": "a" * 64,
        "target_pack_manifest_sha256": "b" * 64,
    }
    value.update(changes)
    return value


def test_full_job_places_historical_novelty_before_score():
    payload = job_payload("m", "m/v1", "m/v2", profile(), 1000, 15)
    assert payload["pipeline"].index("historical_novelty") < payload["pipeline"].index("score")


def test_auto_background_skips_without_proven_scope(monkeypatch, tmp_path):
    result = _prepare_historical_novelty(
        profile(), {"selection_engine": "full_pinned_mirror_scan"}, 7,
        "specter2/proximity@model", tmp_path,
    )
    assert result["status"] == "skipped"
    assert result["reason"] == "collection_not_built_from_verified_thematic_target_pack"

    result = _prepare_historical_novelty(
        profile(), audit(selected_target_packs=["a", "b"]),
        7, "specter2/proximity@model", tmp_path,
    )
    assert result["reason"] == "historical_background_requires_one_proven_target_pack"

    monkeypatch.delenv("SAIA_THEMATIC_ARXIV_CACHE_DIR", raising=False)
    monkeypatch.delenv("SAIA_THEMATIC_ARXIV_PACKS_DIR", raising=False)
    result = _prepare_historical_novelty(
        profile(), audit(), 7, "specter2/proximity@model", tmp_path,
    )
    assert result["reason"] == "thematic_cache_paths_not_configured"


def test_auto_background_skips_non_specter_model(tmp_path):
    result = _prepare_historical_novelty(
        profile(), audit(), 7, "hashing-ngram/v2-d384", tmp_path,
    )
    assert result == {"status": "skipped", "reason": "embedding_model_is_not_specter2"}


def test_auto_background_never_blocks_small_current_topic_set(monkeypatch, tmp_path):
    from saia import full_analysis

    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def execute(self, *_args): pass
        def fetchone(self): return (4,)

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def cursor(self): return Cursor()

    monkeypatch.setenv("SAIA_THEMATIC_ARXIV_CACHE_DIR", "/unused/cache")
    monkeypatch.setenv("SAIA_THEMATIC_ARXIV_PACKS_DIR", "/unused/packs")
    monkeypatch.setattr(full_analysis.db, "connect", lambda: Connection())
    result = _prepare_historical_novelty(
        profile(), audit(), 7, "specter2/proximity@model", tmp_path,
    )
    assert result["status"] == "skipped"
    assert result["reason"] == "fewer_than_5_current_peer_topics_for_novelty_percentile"
    assert result["current_topics"] == 4
