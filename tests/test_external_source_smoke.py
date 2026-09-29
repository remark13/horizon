from saia import external_source_smoke


def test_smoke_report_keeps_connector_statuses_and_does_not_change_score(monkeypatch):
    monkeypatch.setattr(external_source_smoke, 'fetch_news', lambda query: {
        'source': 'gdelt_doc_2_0', 'status': 'rate_limited',
        'scientific_score_modified': False,
    })
    monkeypatch.setattr(external_source_smoke, 'fetch_patents', lambda query: {
        'source': 'epo_ops', 'status': 'credentials_required',
        'scientific_score_modified': False,
    })
    report = external_source_smoke.run(
        'agents', 'agentic AI', 'agentic artificial intelligence',
        '2026-09-14', '2025-01-01', '2026-09-21',
    )
    assert report['news']['status'] == 'rate_limited'
    assert report['patents']['status'] == 'credentials_required'
    assert report['scientific_score_modified'] is False
    assert len(report['report_payload_sha256']) == 64
