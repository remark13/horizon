from fastapi.testclient import TestClient

from saia import candidate_brief as brief
from saia.api import app
from saia.scout_web import SCOUT_WEB_HTML


def packet():
    return {"mission_id": "m", "score_run_id": 12, "exported_at": "2026-09-28",
            "card": {"candidate_id": 7, "label": '<script>alert("x")</script>', "composition_sha256": "a" * 64,
                     "evidence_confidence": None, "metrics": {"publication_series": {"share_slope_per_window": 0.1,
                        "coverage_comparable": False, "window_scale_comparable": True, "points": [
                        {"start": "2026-01-01", "end": "2026-04-01", "topic_works": 0, "corpus_works": 10, "share": 0, "complete": True}]}},
                     "evidence": [{"title": "Test article", "published_at": "2024-01-01", "sources": [{"url": "javascript:alert(1)", "type": "arxiv"}]}]},
            "screening": {"label": "Данных недостаточно", "explanation": "Недостаточно данных."}, "presentation": None,
            "context": {"binding": {"retrieval_date": "2026-09-28", "query": "graph"}, "reports": [
                {"name": "EPO", "status": "credentials_required", "materials": []}]},
            "analysis_note": None, "reviews": {"reviews": []}}


def test_display_filter_cannot_remove_scoring_sources_from_brief(monkeypatch):
    from saia import candidate_assessment
    c = {"candidate_id": 7}
    full = {"reports": [{"source": "google_news_rss"}, {"source": "epo_ops"}]}
    seen = []
    monkeypatch.setattr(brief.candidate_external_links, '_candidate', lambda *args: c)
    monkeypatch.setattr(brief.candidate_external_links, 'for_candidate', lambda *args: {})
    monkeypatch.setattr(brief.triage, 'build_queue', lambda *args: {"queue": []})
    monkeypatch.setattr(brief.source_context, 'read', lambda *args: {"reports": []})
    monkeypatch.setattr(brief.source_context, 'read_saved_all', lambda *args: full)
    monkeypatch.setattr(candidate_assessment, 'calculate', lambda card, context, links: seen.append(context) or {})
    monkeypatch.setattr(brief.card_presentation, 'read', lambda *args: {"presentation": None})
    monkeypatch.setattr(brief.candidate_analysis, 'read', lambda *args: {"note": None})
    monkeypatch.setattr(brief.score_expert, 'history', lambda *args: {})
    monkeypatch.setattr(brief, 'render', lambda p: p)
    value = brief.build('m', 12, 7, query='filtered', sources=['epo_ops'])
    assert value['context'] is full and seen == [full]


def test_readable_offline_brief_escapes_sources_and_does_not_invent_absent_data():
    html = brief.render(packet())
    assert '<script>' not in html and 'javascript:' not in html
    assert '&lt;script&gt;' in html
    assert 'Изменение доли: Не установлено' in html
    assert 'Не установлено%' not in html
    assert '0,00%' in html
    assert 'Нужен ключ доступа' in html and 'Не проработано.' in html
    assert 'не вероятность' in html and 'не входят в научный рейтинг' in html
    assert 'Гранты и госконтракты не являются частными инвестициями' in html
    assert html.count('Не проработано.') >= 6


def test_note_evidence_is_shown_next_to_hypothesis_without_implied_validation():
    value = packet()
    value['analysis_note'] = {'revision': 1, 'created_at': '2026-09-28', 'content': {
        'author': 'Скаут', 'summary': 'Нужно проверить условия.', 'pestle': [{'dimension': 'technological',
        'text': 'Метод может повлиять на планирование.', 'dependencies': 'Независимая проверка.',
        'effect': 'opportunity', 'horizon': 'not_assessed', 'evidence_ids': ['pub']}], 'industry_impacts': [],
        'references': [{'id': 'pub', 'title': 'Original source', 'url': 'https://arxiv.org/abs/1609.02907'}]}}
    html = brief.render(value)
    assert 'https://arxiv.org/abs/1609.02907' in html
    assert 'Гипотезы скаута, не установленные последствия' in html
    assert 'Автор: Скаут' in html and 'Версия 1' in html
    assert 'Срок не оценён' in html


def test_grouped_brief_keeps_each_source_link_and_escapes_identity_proofs():
    from saia import external_material_groups as grouping
    rows = []
    for identifier, relations in [('10.1234/a', []), ('10.1234/a.v1', [{'doi': '10.1234/a', 'relation': 'IsVersionOf'}])]:
        rows.append({'source': 'datacite', 'source_name': 'DataCite', 'group': 'software', 'record_type': 'research_dataset',
            'title_original': '<script>source title</script>', 'url': 'https://doi.org/' + identifier,
            'date_kind': 'resource_publication', 'record_date': '2025', 'date_precision': 'year',
            'retrieved_at': '2026-09-28', 'author': 'Author <unsafe>', 'observation_id': 'source',
            'identity_metadata': grouping.identity_metadata('datacite', {'doi': identifier, 'related_dois': relations})})
    value = packet()
    value['context']['reports'] = [{'name': 'DataCite', 'status': 'complete', 'materials': rows}]
    value['context']['material_grouping'] = grouping.group_materials(rows)
    html = brief.render(value)
    assert '1 материалов из 2 исходных записей' in html
    assert 'Другие записи и основание объединения (1)' in html
    assert html.count('href="https://doi.org/10.1234/a"') == 1
    assert html.count('href="https://doi.org/10.1234/a.v1"') == 1
    assert '<script>' not in html and '&lt;script&gt;source title' in html
    assert 'Источник указал только год' in html
    assert 'Author &lt;unsafe&gt;' in html
    assert 'не число независимых подтверждений' in html
    assert '<details>' in html


def test_grouped_grant_export_retains_conflicting_sums_without_a_total():
    from saia import external_material_groups as grouping
    rows = []
    for source, amount in [('nsf_awards', 100), ('openaire_projects', 200)]:
        raw = {'award_id': '2554349', 'project_id': 'source-id', 'grant_reference': '2554349',
               'funders': [{'shortName': 'NSF', 'jurisdiction': 'US'}]}
        rows.append({'source': source, 'source_name': source, 'group': 'funding', 'title_original': 'Grant',
            'url': 'https://example.org/' + source, 'observation_id': source, 'record_type': 'funded_research_project',
            'record_date': '2026-09-01', 'date_kind': 'project_start', 'retrieved_at': '2026-09-28',
            'funding_amount': amount, 'funding_currency': 'USD',
            'funding_amount_basis': 'reported_project_funding_not_paid_expenditure',
            'identity_metadata': grouping.identity_metadata(source, raw)})
    value = packet()
    value['context']['reports'] = [{'name': source, 'status': 'complete', 'materials': [row]} for source, row in zip(['NSF', 'OpenAIRE'], rows)]
    value['context']['material_grouping'] = grouping.group_materials(rows)
    html = brief.render(value)
    assert '100,00 USD' in html and '200,00 USD' in html
    assert '300,00 USD' not in html and 'Даты и суммы не складываются' in html
    assert 'NSF 2554349' in html


def test_export_endpoint_has_attachment_and_safe_inline_preview(monkeypatch):
    calls = []
    monkeypatch.setattr(brief, 'build', lambda *a: calls.append(a) or '<html lang="ru">Карточка</html>')
    client = TestClient(app)
    reply = client.get('/signals/m/7/brief?score_run_id=12&sources=mit_news_rss,nasa_news_rss&query=graph')
    assert reply.status_code == 200
    assert reply.headers['content-disposition'] == 'attachment; filename="Horizon-card-7-score-12.html"'
    assert "default-src 'none'" in reply.headers['content-security-policy']
    assert reply.headers['cache-control'] == 'no-store'
    assert calls == [('m', 12, 7, 'graph', ['mit_news_rss', 'nasa_news_rss'])]
    preview = client.get('/signals/m/7/brief?score_run_id=12&download=false')
    assert 'content-disposition' not in preview.headers and preview.status_code == 200


def test_export_checkpoint_accepts_lowercase_http_header_names():
    from scripts.verify_candidate_brief import normalized_headers
    assert normalized_headers({'Content-Disposition': 'attachment; filename="x.html"'}) == {
        'content-disposition': 'attachment; filename="x.html"'}
    assert normalized_headers({'content-disposition': 'attachment'})['content-disposition'] == 'attachment'


def test_ui_analysis_stays_optional_separate_and_does_not_overwrite_parallel_edits():
    for text in ('PESTLE и влияние на отрасли', 'Скачать карточку', 'data-impact-field="dependencies"',
                 'expected_revision:revision', 'Не удалось сохранить заметку',
                 'История сохранений не удаляется.'):
        assert text in SCOUT_WEB_HTML
    assert 'Ссылка подтверждает наличие материала, но не доказывает гипотезу.' in SCOUT_WEB_HTML
    assert 'details.isConnected' in SCOUT_WEB_HTML
    assert 'id="open-card-analysis"' in SCOUT_WEB_HTML
    assert "details.open=true;details.scrollIntoView" in SCOUT_WEB_HTML
    assert "document.body.style.overflow='hidden'" in SCOUT_WEB_HTML
    assert "document.body.style.overflow=''" in SCOUT_WEB_HTML
    assert 'loadAnalysis(c)' in SCOUT_WEB_HTML
    assert 'Автоматическое подтверждение PESTLE' not in SCOUT_WEB_HTML
