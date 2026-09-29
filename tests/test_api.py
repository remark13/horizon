from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError

from saia.api import BalancedDiscoveryJobRequest, app


client = TestClient(app)


def test_balanced_discovery_accepts_bounded_expanded_pilot():
    base = {"requested_by": "test", "date_from": "2021-09-01",
            "as_of_date": "2026-09-01", "max_results": 300}
    assert BalancedDiscoveryJobRequest(**base, limit_per_source=100).limit_per_source == 100
    with pytest.raises(ValidationError):
        BalancedDiscoveryJobRequest(**base, limit_per_source=101)


def test_web_app_is_available_in_russian():
    response = client.get("/")
    assert response.status_code == 200
    assert "Технология, научная тема или проблема" in response.text
    assert "/discover/preview" in response.text
    assert "/benchmarks/ml-area-2017" in response.text
    assert 'Открыть версию без нового поиска' in response.text
    assert '/queries/approved?query_version_id=' in response.text
    assert "m.windows_present??'неизвестно'" in response.text
    assert 'all.findIndex(x=>x.work_id===v.work_id)===i' in response.text
    assert 'Ретротест OpenAlex + arXiv: новый сохранённый результат' in response.text
    assert 'eae3a848-da19-4b9f-a272-a36cdfed498e' in response.text
    assert '/triage/ai-data-selection-current-p1-v047-openalex-enriched' in response.text
    assert 'score-review' in response.text
    assert 'Запустить сохраняемый предпросмотр' in response.text
    assert 'Запустить полный анализ до карточек' in response.text
    assert '/jobs/discovery' in response.text
    assert '/jobs/full-analysis' in response.text
    assert 'полнота: ${esc(j.result_completeness)}' in response.text
    assert 'href="/annotation-review"' in response.text
    assert 'href="/retrieval-review"' in response.text
    assert 'id="focus-area"' not in response.text
    assert "fetch('/focus-areas')" not in response.text
    assert "Введите любую технологию" in response.text
    assert 'id="plan-query"' in response.text
    assert "Ни одна предложенная ветвь не выбирается" in response.text
    assert "'/query-plan/approve'" in response.text
    assert "/jobs/discovery`" in response.text
    assert 'id="prepare-local-plan"' in response.text
    assert "/compile`" in response.text
    assert "Полный проход читает 3,16 млн записей" in response.text
    assert "candidate:'Кандидат слабого сигнала'" in response.text
    assert 'Сформировать до 15 кандидатов слабых сигналов' in response.text
    assert '/full-analysis`' in response.text
    assert 'Экспертная валидация для получения результата не требуется' in response.text


def test_scout_screen_is_real_data_first_and_expert_optional():
    response = client.get('/scout')
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store, max-age=0'
    assert 'Рабочее место технологического скаута' in response.text
    assert '/scout-results/' in response.text
    assert '/expert-requests' in response.text
    assert 'Передать на экспертизу' in response.text
    assert 'Новости и коммерческие публикации' in response.text
    assert 'Публикации и препринты ·' in response.text
    assert 'Публикации OpenAlex / arXiv' not in response.text
    assert 'Патенты' in response.text
    assert 'Инвестиционные сделки' in response.text
    assert 'class="block hidden" id="external-links"' in response.text
    assert 'id="patent-links"' in response.text
    assert "item.source==='epo_ops'" in response.text
    assert 'if(state.current?.candidate_id!==id||!packet.links?.length)return' in response.text
    assert '/compile`' in response.text
    assert 'compiled_query_plan_id:compiled.compilation_id' in response.text
    assert "phase:'collect',job_id:discovery.job_id" in response.text
    assert 'await continueSearch(pending)' in response.text
    assert "if(m)load(m,s?Number(s):null,discoveryJob).then(resumePending);else{render();resumePending()}" in response.text
    assert "const state={mission:null,score:null,packet:null" in response.text
    assert "Пример: прецизионная ферментация" not in response.text
    assert "'unmanned-aircraft-systems/uas-airframes':['UAV airframe'" in response.text
    assert "'unmanned-aircraft-systems/autonomous-flight-control':['autonomous flight'" in response.text
    assert "'unmanned-aircraft-systems/drone-swarms':['drone swarms'" in response.text
    assert 'data-branch="${esc(s.suggestion_id)}" checked' in response.text
    assert 'if(!foundWorks.length)' in response.text
    assert 'if(auditBranches.length&&localMatches===0)' not in response.text
    assert "$('results-title').textContent='Предыдущие результаты'" in response.text
    assert 'href="/scout"' in client.get('/').text


def test_scout_screen_keeps_diagnostics_out_of_primary_view():
    response = client.get('/scout')
    assert response.status_code == 200
    assert 'Пройдено проверок:' not in response.text
    assert 'Подробнее об оценке' not in response.text
    assert 'Загружаю сохранённый реальный прогон' not in response.text
    assert '<div class="eyebrow">Результаты анализа</div>' not in response.text
    assert '<div class="eyebrow">Научно-технологическая разведка</div>' not in response.text
    assert 'Связь с темой отметил аналитик' not in response.text
    assert 'Продолжить поиск' in response.text
    assert 'compiled_query_plan_id:compiled.compilation_id' in response.text
    assert 'Рост не подтверждён' in response.text
    assert 'Уверенность оценки' not in response.text
    assert 'Полнота оснований' in response.text
    assert 'не означает вероятность подтверждения сигнала' in response.text
    assert 'Неполный текущий период не участвует' in response.text
    assert 'new Date(today.getFullYear(),today.getMonth(),1)' in response.text


def test_expert_dispatch_api_is_separate_from_expert_opinion(monkeypatch):
    import saia.expert_requests as requests
    recorded = {}

    def fake_create(mission, score, ids, by, recipient, note, operation_id):
        recorded.update(mission=mission, score=score, ids=ids, by=by,
                        recipient=recipient, note=note, operation_id=operation_id)
        return {'status': 'awaiting_expert_review', 'delivery': 'internal_queue_only'}

    monkeypatch.setattr(requests, 'create', fake_create)
    response = client.post('/expert-requests', json={
        'mission_id': 'real-run', 'score_run_id': 9497, 'candidate_ids': [1, 2],
        'requested_by': 'скаут', 'recipient': 'группа', 'note': 'Проверьте источники',
    })
    assert response.status_code == 201
    assert recorded['ids'] == [1, 2]
    assert response.json()['delivery'] == 'internal_queue_only'
    monkeypatch.setattr(requests, 'history', lambda limit: {'requests': [], 'limit': limit})
    assert client.get('/expert-requests?limit=30').json()['limit'] == 30


def test_focus_area_catalog_is_public_and_not_presented_as_gold():
    response = client.get('/focus-areas')
    assert response.status_code == 200
    payload = response.json()
    assert payload['count'] == 9
    assert payload['policy']['profile_is_gold_label'] is False
    assert payload['policy']['manual_query_approval_required'] is True
    assert {item['id'] for item in payload['profiles']} >= {
        'artificial-intelligence', 'new-materials-and-chemistry',
        'multi-satellite-constellations',
    }
    assert client.get('/focus-areas/health-preservation').status_code == 200
    assert client.get('/focus-areas/unknown').status_code == 404


def test_transparent_query_plan_never_auto_replaces_free_query():
    response = client.post('/query-plan/preview', json={
        'query': 'новые материалы', 'max_suggestions': 12,
    })
    assert response.status_code == 200
    payload = response.json()
    assert payload['original_query'] == 'новые материалы'
    assert payload['original_query_preserved'] is True
    assert payload['automatic_execution'] is False
    assert payload['automatic_query_replacement'] is False
    assert payload['suggestions']
    assert all(item['selected'] is False for item in payload['suggestions'])


def test_query_plan_approval_endpoint_does_not_start_execution(monkeypatch):
    import saia.query_plan_store as store

    captured = {}
    def fake_approve(query, max_suggestions, preview_hash, branch_ids, approved_by,
                     operation_id):
        captured.update({
            'query': query, 'max_suggestions': max_suggestions,
            'preview_hash': preview_hash, 'branch_ids': branch_ids,
            'approved_by': approved_by, 'operation_id': operation_id,
        })
        return {'plan_id': operation_id, 'execution_started': False}

    monkeypatch.setattr(store, 'approve', fake_approve)
    operation_id = '284c103f-1fdc-4d4b-82d6-8556e3a7c459'
    response = client.post('/query-plan/approve', json={
        'query': 'новые материалы', 'max_suggestions': 12,
        'preview_payload_sha256': 'a' * 64,
        'selected_branch_ids': ['new-materials-and-chemistry/advanced-composites'],
        'approved_by': 'analyst', 'operation_id': operation_id,
    })
    assert response.status_code == 200
    assert response.json()['execution_started'] is False
    assert captured['query'] == 'новые материалы'
    assert captured['operation_id'] == operation_id


def test_query_plan_history_and_read_endpoints_translate_store_errors(monkeypatch):
    import saia.query_plan_store as store

    monkeypatch.setattr(store, 'history', lambda limit: {
        'total': 0, 'returned': 0, 'plans': [], 'execution_started': False,
    })
    response = client.get('/query-plans?limit=10')
    assert response.status_code == 200
    assert response.json()['execution_started'] is False

    def missing(_plan_id):
        raise ValueError('Подтверждённый поисковый план не найден.')
    monkeypatch.setattr(store, 'read', missing)
    response = client.get('/query-plans/not-a-uuid')
    assert response.status_code == 404


def test_balanced_discovery_job_api_uses_approved_plan_and_explicit_period(monkeypatch):
    from saia import jobs

    seen = []
    def enqueue(*args, **kwargs):
        seen.append((args, kwargs))
        return {
            'job_id': '00000000-0000-0000-0000-000000000091',
            'status': 'queued',
            'result_role': 'balanced_corpus_candidate_not_signals',
        }
    monkeypatch.setattr(jobs, 'enqueue_balanced_discovery', enqueue)
    plan_id = '00000000-0000-0000-0000-000000000090'
    response = client.post(f'/query-plans/{plan_id}/jobs/discovery', json={
        'requested_by': 'analyst', 'date_from': '2020-01-01',
        'as_of_date': '2026-01-01', 'limit_per_source': 7, 'max_results': 15,
        'operation_id': '00000000-0000-0000-0000-000000000092',
    })
    assert response.status_code == 202
    assert response.json()['result_role'] == 'balanced_corpus_candidate_not_signals'
    assert seen[0][0][0:2] == (plan_id, 'analyst')
    assert seen[0][1]['limit_per_source'] == 7
    assert seen[0][1]['compiled_query_plan_id'] is None


def test_query_plan_compilation_endpoint_keeps_execution_separate(monkeypatch):
    from saia import compiled_query_plan_store as store

    seen = []
    monkeypatch.setattr(store, 'compile_plan', lambda *args: seen.append(args) or {
        'compilation_id': args[3], 'execution_started': False,
    })
    plan_id = '00000000-0000-0000-0000-000000000093'
    operation_id = '00000000-0000-0000-0000-000000000094'
    response = client.post(f'/query-plans/{plan_id}/compile', json={
        'compiled_by': 'analyst', 'operation_id': operation_id,
        'branch_specs': [{
            'branch_id': 'original-query',
            'included_phrases': ['тканевая инженерия'],
            'excluded_phrases': [],
        }],
    })
    assert response.status_code == 200
    assert response.json()['execution_started'] is False
    assert seen[0][0] == plan_id and seen[0][3] == operation_id


def test_balanced_job_retrieval_packet_and_template_are_job_scoped(monkeypatch):
    from saia import balanced_retrieval_review as review

    job_id = '00000000-0000-0000-0000-000000000095'
    packet = {
        'version': 'balanced-job-retrieval-review-packet-0.4.38',
        'package_id': '00000000-0000-0000-0000-000000000096',
        'source_job_id': job_id, 'items': [],
    }
    template = {'package_id': packet['package_id'], 'annotations': []}
    monkeypatch.setattr(review, 'from_job_id', lambda value: (packet, template))
    response = client.get(f'/jobs/{job_id}/retrieval-review')
    assert response.status_code == 200
    assert response.json()['source_job_id'] == job_id
    response = client.get(f'/jobs/{job_id}/retrieval-review/template')
    assert response.status_code == 200
    assert response.json()['package_id'] == packet['package_id']


def test_balanced_job_retrieval_validation_uses_exact_packet(monkeypatch):
    from saia import balanced_retrieval_review as review
    from saia import retrieval_relevance_review as protocol

    job_id = '00000000-0000-0000-0000-000000000097'
    packet = {'package_id': '00000000-0000-0000-0000-000000000098'}
    monkeypatch.setattr(review, 'from_job_id', lambda value: (packet, {}))
    monkeypatch.setattr(protocol, 'validate_submission', lambda p, s: {
        'same_packet': p is packet, 'submission': s,
    })
    response = client.post(
        f'/jobs/{job_id}/retrieval-review/validate', json={'reviewer_id': 'r'}
    )
    assert response.status_code == 200
    assert response.json() == {
        'same_packet': True, 'submission': {'reviewer_id': 'r'},
    }


def test_balanced_job_retrieval_adjudication_is_explicit(monkeypatch):
    from saia import balanced_retrieval_adjudication as adjudication

    seen = []
    monkeypatch.setattr(adjudication, 'record', lambda *args: seen.append(args) or {
        'adjudication_id': args[-1], 'precision_available': True,
        'weak_signal_accuracy_measured': False,
    })
    job_id = '00000000-0000-0000-0000-000000000115'
    operation_id = '00000000-0000-0000-0000-000000000116'
    left_id = '00000000-0000-0000-0000-000000000117'
    right_id = '00000000-0000-0000-0000-000000000118'
    response = client.post(f'/jobs/{job_id}/retrieval-review/adjudications', json={
        'left_submission_id': left_id, 'right_submission_id': right_id,
        'adjudicator_id': 'adjudicator', 'operation_id': operation_id,
        'decisions': [{
            'item_id': 'item-1', 'topical_relevance': 'relevant',
            'rationale': 'This publication directly covers the requested topic.',
            'sources': [],
        }],
    })
    assert response.status_code == 200
    assert response.json()['weak_signal_accuracy_measured'] is False
    assert seen[0][0:4] == (job_id, left_id, right_id, 'adjudicator')
    assert seen[0][-1] == operation_id


def test_adjudicated_universal_query_can_explicitly_queue_full_analysis(monkeypatch):
    from saia import universal_materialization, jobs

    materialized = {
        'mission_id': 'universal-demo', 'query_version_id': 'universal-demo/v1',
    }
    seen = []
    monkeypatch.setattr(universal_materialization, 'materialize',
                        lambda *args, **kwargs: seen.append(('materialize', args, kwargs)) or materialized)
    monkeypatch.setattr(jobs, 'enqueue_full_analysis',
                        lambda *args, **kwargs: seen.append(('enqueue', args, kwargs)) or {
                            'job_id': '00000000-0000-0000-0000-000000000141',
                            'status': 'queued',
                        })
    source_job = '00000000-0000-0000-0000-000000000142'
    adjudication_id = '00000000-0000-0000-0000-000000000143'
    operation_id = '00000000-0000-0000-0000-000000000144'
    response = client.post(f'/jobs/{source_job}/retrieval-review/full-analysis', json={
        'adjudication_id': adjudication_id, 'accepted_by': 'analyst',
        'acknowledge_arxiv_only': True, 'acknowledge_phrase_union': True,
        'max_records': 5000, 'top_n': 15, 'operation_id': operation_id,
    })
    assert response.status_code == 202
    assert response.json()['analysis_job']['status'] == 'queued'
    assert response.json()['retrieval_precision_is_not_weak_signal_accuracy'] is True
    assert seen[0][0] == 'materialize' and seen[1][0] == 'enqueue'
    assert seen[1][1][0:3] == ('universal-demo', 'universal-demo/v1', 'analyst')
    assert seen[1][2]['acknowledge_source_scope'] is True


def test_balanced_job_can_queue_automatic_candidates_without_expert_gate(monkeypatch):
    from saia import universal_materialization, jobs

    materialized = {
        'mission_id': 'universal-auto', 'query_version_id': 'universal-auto/v1',
        'expert_validation_required': False,
    }
    seen = []
    monkeypatch.setattr(
        universal_materialization, 'materialize_candidate',
        lambda *args: seen.append(('materialize', args)) or materialized,
    )
    monkeypatch.setattr(
        jobs, 'enqueue_full_analysis',
        lambda *args, **kwargs: seen.append(('enqueue', args, kwargs)) or {
            'job_id': '00000000-0000-0000-0000-000000000151',
            'status': 'queued',
        },
    )
    source_job = '00000000-0000-0000-0000-000000000152'
    operation_id = '00000000-0000-0000-0000-000000000153'
    response = client.post(f'/jobs/{source_job}/full-analysis', json={
        'requested_by': 'user-interface', 'max_records': 5000, 'top_n': 15,
        'operation_id': operation_id,
    })
    assert response.status_code == 202
    payload = response.json()
    assert payload['candidate_generation'] == 'automatic'
    assert payload['expert_validation_required'] is False
    assert payload['expert_validation_status'] == 'not_requested'
    assert payload['retrieval_review_required'] is False
    assert seen[0] == ('materialize', (source_job, 'user-interface'))
    assert seen[1][1][0:3] == ('universal-auto', 'universal-auto/v1', 'user-interface')


def test_annotation_reviewer_page_is_blinded_and_uses_server_validation():
    response = client.get('/annotation-review')
    assert response.status_code == 200
    assert 'Независимая оценка публикационных кандидатов' in response.text
    assert "fetch('/annotation/current')" in response.text
    assert "'/annotation/current/validate'" in response.text
    assert "'/annotation/current/submissions'" in response.text
    assert 'неизменяемой локальной истории Horizon' in response.text
    assert 'private-key' not in response.text


def test_retrieval_reviewer_page_can_bind_to_one_balanced_job():
    response = client.get('/retrieval-review')
    assert response.status_code == 200
    assert "new URLSearchParams(location.search).get('job_id')" in response.text
    assert '/retrieval-review/current' in response.text
    assert '/jobs/${encodeURIComponent(jobId)}/retrieval-review' in response.text


def test_retrieval_adjudication_page_is_explicitly_optional_and_not_a_handoff_gate():
    response = client.get('/retrieval-adjudication?job_id=demo')
    assert response.status_code == 200
    assert 'Необязательный арбитраж релевантности' in response.text
    assert 'система формирует кандидатов слабых сигналов и без него' in response.text
    assert 'acknowledge_arxiv_only' not in response.text
    assert '/full-analysis' not in response.text
    assert 'не подтверждает слабый сигнал' in response.text


def test_main_page_explains_historical_novelty_and_parameter_sensitivity():
    response = client.get('/')
    assert response.status_code == 200
    assert 'Исторический фон:' in response.text
    assert 'historical_background_parameter_sensitivity_unresolved' in response.text
    assert 'Новизна пока не включена в оценку' in response.text


@pytest.mark.local_data
def test_current_annotation_packet_and_template_are_public_but_key_is_not():
    from saia import annotation_protocol

    required = (annotation_protocol.CURRENT_PACKET_PATH, annotation_protocol.CURRENT_TEMPLATE_PATH)
    missing = [path.name for path in required if not path.exists()]
    if missing:
        pytest.skip('Historical annotation fixtures are not bundled: ' + ', '.join(missing))
    packet = client.get('/annotation/current')
    template = client.get('/annotation/current/template')
    assert packet.status_code == 200 and template.status_code == 200
    assert packet.json()['package_id'] == template.json()['package_id']
    assert packet.json()['gold_standard'] is False
    assert 'mapping' not in packet.json()
    assert client.get('/annotation/current/private-key').status_code == 404


@pytest.mark.local_data
def test_retrieval_review_page_and_public_packet_keep_experiment_blinded():
    from saia import retrieval_relevance_review

    required = (retrieval_relevance_review.CURRENT_PACKET_PATH, retrieval_relevance_review.CURRENT_TEMPLATE_PATH)
    missing = [path.name for path in required if not path.exists()]
    if missing:
        pytest.skip('Historical retrieval fixtures are not bundled: ' + ', '.join(missing))
    page = client.get('/retrieval-review')
    packet = client.get('/retrieval-review/current')
    template = client.get('/retrieval-review/current/template')
    assert page.status_code == packet.status_code == template.status_code == 200
    assert 'Проверка расширенного поиска' in page.text
    assert "fetch('/retrieval-review/current')" in page.text
    assert "'/retrieval-review/current/validate'" in page.text
    assert "'/retrieval-review/current/submissions'" in page.text
    assert 'неизменяемой локальной истории Horizon' in page.text
    assert packet.json()['package_id'] == template.json()['package_id']
    assert len(packet.json()['items']) == 99
    assert packet.json()['selection_summary']['micro_precision_supported'] is False
    assert 'mapping' not in packet.json()
    assert 'source_number' not in str(packet.json()['items'])
    assert client.get('/retrieval-review/current/private-key').status_code == 404


@pytest.mark.parametrize('module_name,route', [
    ('annotation_protocol', '/annotation/current'),
    ('retrieval_relevance_review', '/retrieval-review/current'),
])
def test_missing_historical_review_packet_returns_service_unavailable(
        monkeypatch, tmp_path, module_name, route):
    from importlib import import_module

    protocol = import_module('saia.' + module_name)
    read_packet = protocol.current_packet
    missing_path = tmp_path / 'missing.packet.json'
    monkeypatch.setattr(protocol, 'current_packet', lambda: read_packet(missing_path))
    for suffix in ('', '/template'):
        response = client.get(route + suffix)
        assert response.status_code == 503
        assert missing_path.name in response.json()['detail']
    assert client.get(route + '/private-key').status_code == 404


def test_retrieval_review_validation_and_comparison_do_not_create_precision(monkeypatch):
    from saia import retrieval_relevance_review
    monkeypatch.setattr(retrieval_relevance_review, 'current_packet',
                        lambda: {'package_id': 'retrieval-packet'})
    monkeypatch.setattr(retrieval_relevance_review, 'validate_submission',
                        lambda packet, submission: {'complete': True,
                         'precision_available': False, 'seen': submission})
    monkeypatch.setattr(retrieval_relevance_review, 'compare_submissions',
                        lambda packet, left, right: {'consensus_created': False,
                         'precision_available': False, 'seen': [left, right]})
    checked = client.post('/retrieval-review/current/validate', json={'reviewer_id': 'A'})
    compared = client.post('/retrieval-review/current/compare', json={
        'left': {'reviewer_id': 'A'}, 'right': {'reviewer_id': 'B'}})
    assert checked.status_code == compared.status_code == 200
    assert checked.json()['precision_available'] is False
    assert compared.json()['precision_available'] is False
    assert compared.json()['consensus_created'] is False


def test_retrieval_review_append_only_store_api_is_explicit(monkeypatch):
    from saia import retrieval_review_store
    first = '00000000-0000-0000-0000-000000000061'
    second = '00000000-0000-0000-0000-000000000062'
    operation = '00000000-0000-0000-0000-000000000063'
    calls = []
    monkeypatch.setattr(retrieval_review_store, 'record',
                        lambda *args: calls.append(('record', *args)) or
                        {'submission_id': first, 'precision_available': False})
    monkeypatch.setattr(retrieval_review_store, 'history',
                        lambda limit: calls.append(('history', limit)) or {'submissions': []})
    monkeypatch.setattr(retrieval_review_store, 'read',
                        lambda identifier: calls.append(('read', identifier)) or
                        {'submission_id': identifier})
    monkeypatch.setattr(retrieval_review_store, 'compare',
                        lambda *args: calls.append(('compare', *args)) or
                        {'stored_submission_ids': list(args), 'precision_available': False})
    saved = client.post('/retrieval-review/current/submissions', json={
        'submission': {'reviewer_id': 'A'}, 'operation_id': operation})
    assert saved.status_code == 200 and saved.json()['precision_available'] is False
    assert client.get('/retrieval-review/current/submissions?limit=20').status_code == 200
    assert client.get(f'/retrieval-review/current/submissions/{first}').json()[
        'submission_id'] == first
    compared = client.post('/retrieval-review/current/compare-stored', json={
        'left_submission_id': first, 'right_submission_id': second})
    assert compared.status_code == 200 and compared.json()['precision_available'] is False
    assert calls == [('record', {'reviewer_id': 'A'}, operation), ('history', 20),
                     ('read', first), ('compare', first, second)]


def test_annotation_validation_endpoint_does_not_store_or_promote_opinion(monkeypatch):
    from saia import annotation_protocol
    monkeypatch.setattr(annotation_protocol, 'current_packet', lambda: {'package_id': 'packet'})
    monkeypatch.setattr(annotation_protocol, 'validate_submission',
                        lambda packet, submission: {'complete': True,
                         'individual_opinion_not_gold': True, 'seen': submission})
    response = client.post('/annotation/current/validate', json={'reviewer_id': 'A'})
    assert response.status_code == 200
    assert response.json()['individual_opinion_not_gold'] is True


def test_annotation_compare_requires_explicit_two_submissions(monkeypatch):
    from saia import annotation_protocol
    seen = []
    monkeypatch.setattr(annotation_protocol, 'current_packet', lambda: {'package_id': 'packet'})
    monkeypatch.setattr(annotation_protocol, 'compare_submissions',
                        lambda packet, left, right: seen.append((packet, left, right)) or
                        {'consensus_created': False})
    response = client.post('/annotation/current/compare', json={'left': {'a': 1}, 'right': {'b': 2}})
    assert response.status_code == 200 and response.json()['consensus_created'] is False
    assert seen == [({'package_id': 'packet'}, {'a': 1}, {'b': 2})]


def test_annotation_append_only_store_api_is_explicit_and_version_scoped(monkeypatch):
    from saia import annotation_store
    first = '00000000-0000-0000-0000-000000000041'
    second = '00000000-0000-0000-0000-000000000042'
    operation = '00000000-0000-0000-0000-000000000043'
    calls = []
    monkeypatch.setattr(annotation_store, 'record',
                        lambda *args: calls.append(('record', *args)) or
                        {'submission_id': first, 'individual_opinion_not_gold': True})
    monkeypatch.setattr(annotation_store, 'history',
                        lambda limit: calls.append(('history', limit)) or {'submissions': []})
    monkeypatch.setattr(annotation_store, 'read',
                        lambda identifier: calls.append(('read', identifier)) or
                        {'submission_id': identifier})
    monkeypatch.setattr(annotation_store, 'compare',
                        lambda *args: calls.append(('compare', *args)) or
                        {'stored_submission_ids': list(args), 'consensus_created': False})
    saved = client.post('/annotation/current/submissions', json={
        'submission': {'reviewer_id': 'A'}, 'operation_id': operation})
    assert saved.status_code == 200 and saved.json()['individual_opinion_not_gold'] is True
    assert client.get('/annotation/current/submissions?limit=20').status_code == 200
    assert client.get(f'/annotation/current/submissions/{first}').json()['submission_id'] == first
    compared = client.post('/annotation/current/compare-stored', json={
        'left_submission_id': first, 'right_submission_id': second})
    assert compared.status_code == 200 and compared.json()['consensus_created'] is False
    assert calls == [('record', {'reviewer_id': 'A'}, operation), ('history', 20),
                     ('read', first), ('compare', first, second)]


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["version"] == "0.4.65"


def test_external_evidence_page_and_news_endpoint(monkeypatch):
    from saia import news_evidence
    seen = []
    monkeypatch.setattr(news_evidence, 'fetch', lambda query: seen.append(query) or {
        'source': 'gdelt_doc_2_0', 'role': 'news_attention_only',
        'topic_id': query.topic_id, 'status': 'rate_limited',
        'observations': None, 'scientific_score_modified': False,
        'missing_is_zero': False, 'report_payload_sha256': 'a' * 64,
    })
    page = client.get('/external-evidence')
    assert page.status_code == 200
    assert 'семейства пока не склеиваются автоматически' in page.text
    assert 'Финансирование · NIH RePORTER' in page.text
    assert 'Программные пакеты · deps.dev' in page.text
    assert 'Научные связи · Semantic Scholar' in page.text
    assert 'Финансирование · UKRI' in page.text
    assert 'EU Funding &amp; Tenders' in page.text
    assert 'ИИ-артефакты · Hugging Face' in page.text
    response = client.post('/external-evidence/news/gdelt', json={
        'topic_id': 'agents', 'query': 'agentic AI', 'start': '2026-09-14',
        'end': '2026-09-21', 'as_of': '2026-09-21', 'max_records': 10,
    })
    assert response.status_code == 200
    assert response.json()['status'] == 'rate_limited'
    assert seen[0].topic_id == 'agents' and seen[0].max_records == 10


def test_patent_endpoint_keeps_credential_status_and_optional_save(monkeypatch):
    from saia import external_evidence_store, patent_evidence
    payload = {
        'source': 'epo_ops', 'role': 'patent_landscape_only', 'topic_id': 'agents',
        'status': 'credentials_required', 'observations': None,
        'scientific_score_modified': False, 'missing_is_zero': False,
        'report_payload_sha256': 'b' * 64,
    }
    monkeypatch.setattr(patent_evidence, 'fetch', lambda query: payload)
    monkeypatch.setattr(external_evidence_store, 'record', lambda value, operation: {
        'observation_id': operation, 'status': value['status'],
    })
    operation = '00000000-0000-0000-0000-000000000042'
    response = client.post('/external-evidence/patents/epo-ops', json={
        'topic_id': 'agents', 'phrase': 'agentic artificial intelligence',
        'start': '2025-01-01', 'end': '2026-09-21', 'as_of': '2026-09-21',
        'max_records': 10, 'save': True, 'operation_id': operation,
    })
    assert response.status_code == 200
    assert response.json()['status'] == 'credentials_required'
    assert response.json()['saved']['observation_id'] == operation


def test_new_external_source_endpoints_keep_roles_separate(monkeypatch):
    from saia import funding_evidence, scholar_evidence, software_evidence

    def result(source, role, topic):
        return {
            'source': source, 'role': role, 'topic_id': topic,
            'status': 'complete', 'observations': [],
            'scientific_score_modified': False, 'missing_is_zero': False,
            'report_payload_sha256': 'c' * 64,
        }

    monkeypatch.setattr(funding_evidence, 'fetch', lambda query: result(
        'nih_reporter', 'research_funding_only', query.topic_id))
    monkeypatch.setattr(software_evidence, 'fetch', lambda query: result(
        'deps_dev', 'software_diffusion_only', query.topic_id))
    monkeypatch.setattr(scholar_evidence, 'fetch', lambda query: result(
        'semantic_scholar', 'bibliographic_enrichment_only', query.topic_id))

    common = {'topic_id': 'agents', 'start': '2025-01-01',
              'end': '2026-09-21', 'as_of': '2026-09-21'}
    funding = client.post('/external-evidence/funding/nih-reporter', json={
        **common, 'query': 'agentic artificial intelligence', 'max_records': 10,
    })
    software = client.post('/external-evidence/software/deps-dev', json={
        **common, 'system': 'pypi', 'package': 'transformers', 'max_versions': 10,
    })
    scholar = client.post('/external-evidence/scholar/semantic-scholar', json={
        **common, 'query': 'agentic artificial intelligence', 'max_records': 10,
    })
    assert funding.json()['role'] == 'research_funding_only'
    assert software.json()['role'] == 'software_diffusion_only'
    assert scholar.json()['role'] == 'bibliographic_enrichment_only'


def test_wave3_external_source_endpoints_keep_roles_separate(monkeypatch):
    from saia import eu_programme_evidence, huggingface_evidence, ukri_evidence

    def result(source, role, topic):
        return {
            'source': source, 'role': role, 'topic_id': topic,
            'status': 'complete', 'observations': [],
            'scientific_score_modified': False, 'missing_is_zero': False,
            'report_payload_sha256': 'd' * 64,
        }

    monkeypatch.setattr(ukri_evidence, 'fetch', lambda query: result(
        'ukri_gtr', 'research_funding_only', query.topic_id))
    monkeypatch.setattr(eu_programme_evidence, 'fetch', lambda query: result(
        'eu_funding_tenders', 'research_programme_only', query.topic_id))
    monkeypatch.setattr(huggingface_evidence, 'fetch', lambda query: result(
        'huggingface_hub', 'ai_artifact_diffusion_only', query.topic_id))
    common = {'topic_id': 'agents', 'query': 'agentic artificial intelligence',
              'start': '2025-01-01', 'end': '2026-09-21',
              'as_of': '2026-09-21', 'max_records': 10}
    ukri = client.post('/external-evidence/funding/ukri', json=common)
    eu = client.post('/external-evidence/programmes/eu-funding', json=common)
    hf = client.post('/external-evidence/ai-artifacts/hugging-face', json=common)
    assert ukri.json()['role'] == 'research_funding_only'
    assert eu.json()['role'] == 'research_programme_only'
    assert hf.json()['role'] == 'ai_artifact_diffusion_only'


def test_external_evidence_history_endpoint(monkeypatch):
    from saia import external_evidence_store
    monkeypatch.setattr(external_evidence_store, 'history',
                        lambda topic, source, limit: {
                            'topic': topic, 'source': source, 'limit': limit,
                            'observations': [], 'total': 0,
                        })
    response = client.get('/external-evidence/observations?topic_id=agents&source=epo_ops&limit=20')
    assert response.status_code == 200
    assert response.json()['source'] == 'epo_ops'
    assert response.json()['limit'] == 20


def test_external_evidence_read_endpoint(monkeypatch):
    from saia import external_evidence_store
    identifier = '00000000-0000-0000-0000-000000000042'
    monkeypatch.setattr(external_evidence_store, 'read',
                        lambda value: {'observation_id': value, 'payload': {}})
    response = client.get(f'/external-evidence/observations/{identifier}')
    assert response.status_code == 200
    assert response.json()['observation_id'] == identifier


def test_durable_discovery_job_api_keeps_candidate_role_explicit(monkeypatch):
    from saia import jobs
    seen = []
    monkeypatch.setattr(
        jobs, 'enqueue_controlled_discovery',
        lambda *args: seen.append(args) or {
            'job_id': '00000000-0000-0000-0000-000000000011',
            'status': 'queued', 'result_role': 'corpus_candidate_not_signals',
        },
    )
    response = client.post('/missions/demo/jobs/discovery', json={
        'query_version_id': 'demo/v2', 'requested_by': 'analyst',
        'limit_per_source': 25,
        'operation_id': '00000000-0000-0000-0000-000000000010',
    })
    assert response.status_code == 202
    assert response.json()['result_role'] == 'corpus_candidate_not_signals'
    assert seen[0][0:4] == ('demo', 'demo/v2', 'analyst', 25)


def test_full_analysis_job_api_keeps_scope_and_result_role_explicit(monkeypatch):
    from saia import jobs
    seen = []
    def enqueue(*args, **kwargs):
        seen.append((args, kwargs))
        return {
            'job_id': '00000000-0000-0000-0000-000000000021',
            'status': 'queued',
            'result_role': 'scientific_review_queue_not_market_forecast',
        }
    monkeypatch.setattr(jobs, 'enqueue_full_analysis', enqueue)
    response = client.post('/missions/demo/jobs/full-analysis', json={
        'query_version_id': 'demo/v2', 'requested_by': 'analyst',
        'max_records': 5000, 'top_n': 15,
        'acknowledge_source_scope': True,
        'operation_id': '00000000-0000-0000-0000-000000000020',
    })
    assert response.status_code == 202
    assert response.json()['result_role'] == 'scientific_review_queue_not_market_forecast'
    assert seen[0][0] == ('demo', 'demo/v2', 'analyst')
    assert seen[0][1]['acknowledge_source_scope'] is True
    assert seen[0][1]['max_records'] == 5000


def test_job_status_and_actions_are_explicit(monkeypatch):
    from saia import jobs
    job_id = '00000000-0000-0000-0000-000000000011'
    calls = []
    monkeypatch.setattr(jobs, 'read', lambda value: calls.append(('read', value)) or {'status': 'queued'})
    monkeypatch.setattr(jobs, 'cancel', lambda *args: calls.append(('cancel', *args)) or {'status': 'cancelled'})
    monkeypatch.setattr(jobs, 'retry', lambda *args: calls.append(('retry', *args)) or {'status': 'queued'})
    assert client.get(f'/jobs/{job_id}').json()['status'] == 'queued'
    assert client.post(f'/jobs/{job_id}/cancel', json={'requested_by': 'analyst'}).json()['status'] == 'cancelled'
    assert client.post(f'/jobs/{job_id}/retry', json={
        'requested_by': 'analyst',
        'operation_id': '00000000-0000-0000-0000-000000000012',
    }).status_code == 202
    assert [item[0] for item in calls] == ['read', 'cancel', 'retry']


def test_embedding_status_requires_explicit_model_and_generation(monkeypatch):
    from saia import embedding_status
    def read(mission, generation, model):
        assert (mission, generation, model) == ('frozen', 761, 'model+revision')
        return {'state': 'partial', 'committed_vectors': 1, 'eligible_works': 3}
    monkeypatch.setattr(embedding_status, 'read', read)
    assert client.get('/corpus/frozen/embedding-status').status_code == 422
    response = client.get('/corpus/frozen/embedding-status',
                          params={'quality_generation_id': 761, 'model': 'model+revision'})
    assert response.status_code == 200 and response.json()['state'] == 'partial'


def test_embedding_status_invalid_frozen_input_is_not_a_success(monkeypatch):
    from saia import embedding_status
    def read(*args):
        raise ValueError('Wrong generation')
    monkeypatch.setattr(embedding_status, 'read', read)
    assert client.get('/corpus/frozen/embedding-status',
                      params={'quality_generation_id': 0, 'model': 'model'}).status_code == 400


def test_expert_api_saves_opinion_without_assessment(monkeypatch):
    from saia import expert
    seen = []
    def fake(*args):
        seen.append(args)
        return {'review_id': 'opinion', 'identity': 'self_declared_not_authenticated'}
    monkeypatch.setattr(expert, 'record', fake)
    response = client.post('/hybrid/snapshot/candidates/candidate/reviews', json={
        'decision': 'needs_review', 'reviewed_by': 'analyst',
        'rationale': 'Needs an independent follow-up study.', 'sources': []})
    assert response.status_code == 200 and response.json()['review_id'] == 'opinion'
    assert seen[0][:3] == ('snapshot', 'candidate', 'needs_review')


def test_expert_invalid_retry_key_is_rejected_before_storage(monkeypatch):
    from saia import expert
    def unexpected(*args):
        pytest.fail('Storage must not be reached for a malformed retry key')
    monkeypatch.setattr(expert, 'record', unexpected)
    response = client.post('/hybrid/snapshot/candidates/candidate/reviews', json={
        'operation_id': 'not-a-uuid', 'decision': 'needs_review', 'reviewed_by': 'analyst',
        'rationale': 'Needs an independent follow-up study.', 'sources': []})
    assert response.status_code == 422


def test_expert_history_endpoint_is_snapshot_scoped(monkeypatch):
    from saia import expert
    seen = []
    monkeypatch.setattr(expert, 'history', lambda *args: seen.append(args) or {'reviews': []})
    assert client.get('/hybrid/snapshot/candidates/candidate/reviews?limit=20').status_code == 200
    assert seen == [('snapshot', 'candidate', 20)]


def test_score_candidate_review_api_keeps_score_run_explicit(monkeypatch):
    from saia import score_expert
    seen = []
    monkeypatch.setattr(score_expert, 'record',
                        lambda *args: seen.append(args) or {'review_id': 'saved'})
    response = client.post('/signals/demo/17/reviews?score_run_id=66', json={
        'decision': 'needs_review', 'reviewed_by': 'analyst',
        'rationale': 'Нужно проверить полный состав публикаций.', 'sources': []})
    assert response.status_code == 200 and response.json()['review_id'] == 'saved'
    assert seen[0][0:3] == ('demo', 17, 'needs_review') and seen[0][6] == 66


def test_score_candidate_review_history_is_version_scoped(monkeypatch):
    from saia import score_expert
    seen = []
    monkeypatch.setattr(score_expert, 'history',
                        lambda *args: seen.append(args) or {'reviews': []})
    response = client.get('/signals/demo/17/reviews?score_run_id=66&limit=20')
    assert response.status_code == 200
    assert seen == [('demo', 17, 66, 20)]


def test_portfolio_reads_the_requested_frozen_snapshot(monkeypatch):
    from saia import hybrid, portfolio
    seen = []
    monkeypatch.setattr(hybrid, 'read', lambda identifier: seen.append(identifier) or {'snapshot_id': identifier})
    monkeypatch.setattr(portfolio, 'project', lambda s: {'snapshot_id': s['snapshot_id'], 'counts': {}})
    assert client.get('/hybrid/frozen-id/portfolio').json()['snapshot_id'] == 'frozen-id'
    assert seen == ['frozen-id']


def test_assessment_history_and_report_are_explicit_read_only_requests(monkeypatch):
    from saia import assessment_store
    calls = []
    monkeypatch.setattr(assessment_store, 'history', lambda *args: calls.append(args) or {'assessments': []})
    monkeypatch.setattr(assessment_store, 'read', lambda aid: {'assessment_id': aid})
    assert client.get('/hybrid/frozen/assessment-history?limit=20').status_code == 200
    assert calls == [('frozen', 20)]
    assert client.get('/assessments/version-one').json() == {'assessment_id': 'version-one'}


def test_portfolio_overlay_receives_only_the_explicit_assessment(monkeypatch):
    from saia import assessment_store, hybrid, portfolio
    calls = []
    monkeypatch.setattr(hybrid, 'read', lambda sid: {'snapshot_id': sid})
    monkeypatch.setattr(assessment_store, 'read', lambda aid: {'assessment_id': aid})
    monkeypatch.setattr(portfolio, 'project', lambda *args: calls.append(args) or {'ok': True})
    assert client.get('/hybrid/frozen/portfolio?assessment_id=selected').json() == {'ok': True}
    assert calls == [({'snapshot_id': 'frozen'}, {'assessment_id': 'selected'})]


def test_missing_assessment_is_explained_not_recalculated(monkeypatch):
    from saia import assessment_store
    def missing(_): raise ValueError('Сохранённая оценка не найдена.')
    monkeypatch.setattr(assessment_store, 'read', missing)
    assert client.get('/assessments/missing').status_code == 422


def test_methodology_is_versioned():
    response = client.get("/methodology")
    assert response.status_code == 200
    payload = response.json()
    assert payload["hash"]
    assert payload["sources"] == ["OpenAlex", "arXiv"]


@pytest.mark.parametrize('fields', [{}, {'primary_sources': None}, {'primary_sources': 0}])
def test_assessment_accepts_unknown_primary_sources_without_inventing_research(fields):
    response = client.post('/assess', json={'doc_count': 5, **fields})
    assert response.status_code == 200
    assert response.json()['status'] != 'forming'


def test_assessment_rejects_negative_primary_count():
    assert client.post('/assess', json={'doc_count': 5, 'primary_sources': -1}).status_code == 422


def test_terminology_endpoint_passes_explicit_generation(monkeypatch):
    from saia import terminology
    seen = []
    def fake(mission, generation):
        seen.append((mission, generation))
        return {'candidates': [], 'corpus_works': 0}
    monkeypatch.setattr(terminology, 'analyze_mission', fake)
    response = client.get('/corpus/demo/terminology?quality_generation_id=19')
    assert response.status_code == 200
    assert seen == [('demo', 19)]


def test_terminology_missing_corpus_is_explained(monkeypatch):
    from saia import terminology
    def fake(*args):
        raise ValueError('Нет завершённого нормализованного корпуса.')
    monkeypatch.setattr(terminology, 'analyze_mission', fake)
    response = client.get('/corpus/demo/terminology')
    assert response.status_code == 422
    assert 'корпуса' in response.json()['detail']


def test_historical_signal_request_passes_run_identifier(monkeypatch):
    from saia import candidates
    seen = []
    def fake(mission, run):
        seen.append((mission, run))
        return {'cards': [], 'score_run_id': run}
    monkeypatch.setattr(candidates, 'export_cards', fake)
    response = client.get('/signals/demo?score_run_id=66')
    assert response.status_code == 200
    assert seen == [('demo', 66)]


def test_triage_uses_saved_cards_and_explicit_limit(monkeypatch):
    from saia import candidates, triage
    seen = []
    monkeypatch.setattr(candidates, 'export_cards',
                        lambda mission, run: seen.append(('cards', mission, run)) or {'cards': []})
    monkeypatch.setattr(triage, 'build_queue',
                        lambda packet, limit: seen.append(('queue', packet, limit)) or {'queue': []})
    response = client.get('/triage/demo?score_run_id=66&limit=7')
    assert response.status_code == 200 and response.json() == {
        'queue': [], 'parent_corpus_context': None}
    assert seen == [('cards', 'demo', 66), ('queue', {'cards': []}, 7)]


def test_triage_validation_error_is_not_reported_as_missing_cards(monkeypatch):
    from saia import candidates, triage
    monkeypatch.setattr(candidates, 'export_cards', lambda *args: {'cards': []})
    monkeypatch.setattr(triage, 'build_queue',
                        lambda *args: (_ for _ in ()).throw(ValueError('bad queue')))
    response = client.get('/triage/demo')
    assert response.status_code == 422 and response.json()['detail'] == 'bad queue'


def test_triage_adds_bound_parent_corpus_context(monkeypatch):
    from saia import candidates, coverage_passport, triage
    monkeypatch.setattr(candidates, 'export_cards', lambda *args: {
        'cards': [], 'provenance': [{'notes': {'coverage_passport_id': 12}}]})
    monkeypatch.setattr(triage, 'build_queue', lambda *args: {'queue': []})
    monkeypatch.setattr(coverage_passport, 'read_summary',
                        lambda identifier: {'coverage_passport_id': identifier})
    response = client.get('/triage/demo')
    assert response.status_code == 200
    assert response.json()['parent_corpus_context']['coverage_passport_id'] == 12


def test_history_endpoint_passes_scoped_limit(monkeypatch):
    from saia import runs
    seen = []
    def fake(mission, limit):
        seen.append((mission, limit))
        return {'runs': []}
    monkeypatch.setattr(runs, 'history', fake)
    response = client.get('/history/demo?limit=10')
    assert response.status_code == 200
    assert seen == [('demo', 10)]


def test_assessment_explains_missing_values():
    response = client.post("/assess", json={
        "doc_count": 8,
        "independent_orgs": 4,
        "independent_teams": 3,
        "single_org_share": 0.4,
        "maturity_percentile": 30,
        "novelty_percentile": 80,
        "windows_present": 3,
        "momentum_percentile": None,
        "primary_sources": 3,
        "age_years": 2,
    })
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "watch"
    assert any(gate["gate"] == "G4_momentum" and gate["passed"] is None
               for gate in payload["gates"])
