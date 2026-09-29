from saia import external_source_wave3_smoke


def test_wave3_smoke_keeps_all_channels_out_of_score(monkeypatch):
    monkeypatch.setattr(external_source_wave3_smoke, "fetch_ukri", lambda query: {
        "source": "ukri_gtr", "status": "complete", "scientific_score_modified": False,
    })
    monkeypatch.setattr(external_source_wave3_smoke, "fetch_eu", lambda query: {
        "source": "eu_funding_tenders", "status": "complete", "scientific_score_modified": False,
    })
    monkeypatch.setattr(external_source_wave3_smoke, "fetch_huggingface", lambda query: {
        "source": "huggingface_hub", "status": "complete", "scientific_score_modified": False,
    })
    report = external_source_wave3_smoke.run(
        "agents", "agentic artificial intelligence", "2025-01-01", "2026-09-21",
    )
    assert report["ukri"]["status"] == "complete"
    assert report["eu_programmes"]["status"] == "complete"
    assert report["huggingface"]["status"] == "complete"
    assert report["scientific_score_modified"] is False
    assert len(report["report_payload_sha256"]) == 64
