from pathlib import Path

import pytest

from saia import arxiv_parent_corpus, arxiv_parent_index_audit, full_analysis


def test_parent_audit_routes_only_opt_in_and_falls_back_only_if_ineligible(monkeypatch):
    calls = []

    def full(_mirror, _mission, _manifest):
        calls.append("full")
        return {"version": "full"}

    def fast(**_kwargs):
        calls.append("fast")
        return {"version": "fast"}

    monkeypatch.setattr(arxiv_parent_corpus, "audit", full)
    monkeypatch.setattr(arxiv_parent_index_audit, "audit", fast)
    monkeypatch.delenv("SAIA_ARXIV_PARENT_INDEX_ENABLED", raising=False)
    assert full_analysis._parent_audit(Path("mirror"), Path("package"))["version"] == "full"
    monkeypatch.setenv("SAIA_ARXIV_PARENT_INDEX_ENABLED", "1")
    monkeypatch.setenv("SAIA_ARXIV_TRIGRAM_INDEX_DIR", "index")
    assert full_analysis._parent_audit(Path("mirror"), Path("package"))["version"] == "fast"

    def ineligible(**_kwargs):
        calls.append("ineligible")
        raise arxiv_parent_index_audit.IndexAuditIneligible("old scope")

    monkeypatch.setattr(arxiv_parent_index_audit, "audit", ineligible)
    assert full_analysis._parent_audit(Path("mirror"), Path("package"))["version"] == "full"
    assert calls == ["full", "fast", "ineligible", "full"]


def test_parent_audit_does_not_hide_corrupt_index(monkeypatch):
    monkeypatch.setenv("SAIA_ARXIV_PARENT_INDEX_ENABLED", "1")
    monkeypatch.setenv("SAIA_ARXIV_TRIGRAM_INDEX_DIR", "index")

    def corrupt(**_kwargs):
        raise ValueError("checksum mismatch")

    monkeypatch.setattr(arxiv_parent_index_audit, "audit", corrupt)
    monkeypatch.setattr(arxiv_parent_corpus, "audit", lambda *_args: pytest.fail(
        "Corrupt index must not silently fall back to the old path"))
    with pytest.raises(ValueError, match="checksum mismatch"):
        full_analysis._parent_audit(Path("mirror"), Path("package"))
