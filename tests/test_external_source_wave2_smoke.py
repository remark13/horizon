from saia import external_source_wave2_smoke


def test_wave2_smoke_keeps_all_channels_out_of_score(monkeypatch):
    monkeypatch.setattr(external_source_wave2_smoke, "fetch_funding", lambda query: {
        "source": "nih_reporter", "status": "complete",
        "scientific_score_modified": False,
    })
    monkeypatch.setattr(external_source_wave2_smoke, "fetch_software", lambda query: {
        "source": "deps_dev", "status": "complete",
        "scientific_score_modified": False,
    })
    monkeypatch.setattr(external_source_wave2_smoke, "fetch_scholar", lambda query: {
        "source": "semantic_scholar", "status": "rate_limited",
        "scientific_score_modified": False,
    })
    report = external_source_wave2_smoke.run(
        "agents", "agentic artificial intelligence", "pypi", "transformers",
        "2025-01-01", "2026-09-21",
    )
    assert report["funding"]["status"] == "complete"
    assert report["software"]["status"] == "complete"
    assert report["scholar"]["status"] == "rate_limited"
    assert report["scientific_score_modified"] is False
    assert len(report["report_payload_sha256"]) == 64
