"""Сквозные тесты против настоящей базы.

Пропускаются, если SAIA_DATABASE_URL не задан. Проверяют не формулировки в
коде, а поведение: что прогон записывается, что прошлое поколение остаётся
на месте, что каноническая дата опускается до самой ранней версии.

Тесты уровня модуля этого поймать не могут: дефекты P0-2, P0-3 и P0-6 жили
именно в связке кода со схемой, и каждый из них выглядел в исходнике
безобидно.
"""

from __future__ import annotations

import hashlib
import os

import pytest

psycopg = pytest.importorskip("psycopg")

if not os.environ.get("SAIA_DATABASE_URL"):
    pytest.skip("SAIA_DATABASE_URL не задан", allow_module_level=True)

from saia import db, normalize, runs  # noqa: E402
from saia import methodology  # noqa: E402

MISSION = "test-mission"


def sealed_fixture_batch(cur, signature, sources=None):
    """Synthetic DB-only package; no assertion of file or field coverage."""
    from psycopg.types.json import Jsonb
    cur.execute('SELECT source, file_name, file_sha256, record_count, snapshot_id FROM source_snapshot '
                'WHERE mission_id = %s AND query_version_id = %s AND (%s::text[] IS NULL OR source = ANY(%s::text[])) '
                'ORDER BY snapshot_id', (MISSION, MISSION + '/v1', sources, sources))
    snapshots = cur.fetchall()
    cur.execute('SELECT content_sha256 FROM query_version WHERE query_version_id = %s', (MISSION + '/v1',))
    config_hash = cur.fetchone()[0]
    coverage = {'mission_file_sha256': config_hash, 'validation': 'synthetic_db_fixture_not_file_verified',
                'expected_corpus_files': [{'source': s, 'file': f, 'sha256': h, 'records': n}
                                          for s, f, h, n, _ in snapshots]}
    cur.execute("INSERT INTO collection_batch (mission_id, query_version_id, content_sha256, "
                "connector_version, fetched_at, status, coverage) VALUES (%s, %s, %s, 'test', now(), 'complete', %s) "
                'RETURNING batch_id', (MISSION, MISSION + '/v1', signature, Jsonb(coverage)))
    batch_id = cur.fetchone()[0]
    for *_, snapshot_id in snapshots:
        cur.execute('INSERT INTO collection_batch_snapshot (batch_id, snapshot_id) VALUES (%s, %s)',
                    (batch_id, snapshot_id))
    cur.execute("UPDATE collection_batch SET seal_status = 'sealed', sealed_at = now() WHERE batch_id = %s", (batch_id,))
    return batch_id


@pytest.mark.parametrize('change', ['coverage', 'status', 'reopen', 'snapshot', 'unlink', 'append_link', 'append_raw', 'query'])
def test_sealed_collection_inputs_cannot_change_in_database(seeded, change):
    from psycopg.types.json import Jsonb
    with db.connect() as conn, conn.cursor() as cur:
        batch = sealed_fixture_batch(cur, 'immutable-fixture')
        cur.execute('SELECT snapshot_id FROM collection_batch_snapshot WHERE batch_id = %s LIMIT 1', (batch,))
        snapshot = cur.fetchone()[0]
        with pytest.raises(psycopg.errors.RaiseException):
            with conn.transaction():
                if change == 'coverage': cur.execute("UPDATE collection_batch SET coverage = '{}' WHERE batch_id = %s", (batch,))
                if change == 'status': cur.execute("UPDATE collection_batch SET status = 'partial' WHERE batch_id = %s", (batch,))
                if change == 'reopen': cur.execute("UPDATE collection_batch SET seal_status = 'open' WHERE batch_id = %s", (batch,))
                if change == 'snapshot': cur.execute('UPDATE source_snapshot SET record_count = 123 WHERE snapshot_id = %s', (snapshot,))
                if change == 'unlink': cur.execute('DELETE FROM collection_batch_snapshot WHERE batch_id = %s', (batch,))
                if change == 'append_link': cur.execute('INSERT INTO collection_batch_snapshot VALUES (%s, %s)', (batch, snapshot))
                if change == 'append_raw':
                    cur.execute('INSERT INTO raw_record (snapshot_id, source, source_record_id, payload, content_sha256) '
                                'VALUES (%s, %s, %s, %s, %s)', (snapshot, 'arxiv', 'late-append', Jsonb({}), 'fixture'))
                if change == 'query': cur.execute("UPDATE query_version SET payload = '{}' WHERE query_version_id = %s", (MISSION + '/v1',))
        cur.execute('SELECT seal_status, status FROM collection_batch WHERE batch_id = %s', (batch,))
        assert cur.fetchone() == ('sealed', 'complete')


def test_referenced_collection_batch_cannot_be_deleted(seeded):
    with db.connect() as conn, conn.cursor() as cur:
        batch = sealed_fixture_batch(cur, 'referenced-fixture')
    normalized = normalize.normalize(MISSION, verbose=False)['run_id']
    with db.connect() as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.errors.RaiseException, match='referenced'):
            with conn.transaction(): cur.execute('DELETE FROM collection_batch WHERE batch_id = %s', (batch,))
        cur.execute('SELECT notes FROM analysis_run WHERE run_id = %s', (normalized,))
        assert cur.fetchone()[0]['collection_batch_seal_status'] == 'sealed'
        cur.execute('SELECT collection_batch_id FROM analysis_run WHERE run_id = %s', (normalized,))
        assert cur.fetchone()[0] == batch
        for sql in ('UPDATE analysis_run SET collection_batch_id = NULL WHERE run_id = %s',
                    "UPDATE analysis_run SET notes = '{}' WHERE run_id = %s"):
            with pytest.raises(psycopg.errors.RaiseException, match='binding is immutable'):
                with conn.transaction(): cur.execute(sql, (normalized,))


def test_open_complete_package_is_not_a_finished_normalization_input(seeded):
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO collection_batch (mission_id, query_version_id, content_sha256, "
                    "connector_version, fetched_at, status) VALUES (%s, %s, 'open-fixture', 'test', now(), 'complete')",
                    (MISSION, MISSION + '/v1'))
    with pytest.raises(ValueError, match='нет завершённого'): normalize.normalize(MISSION, verbose=False)


def test_full_file_ingest_seals_and_repeats_without_mutating_snapshot(seeded, tmp_path):
    import json
    from saia import ingest as module
    mission = {'mission_id': MISSION, 'query_version': MISSION + '/v99', 'title': 'fixture', 'question': 'fixture',
               'as_of_date': '2018-01-01', 'period': {'from': '2010-01-01', 'to': '2017-12-31'},
               'sources': ['arxiv'], 'query': {'terms': ['machine learning']}}
    text = json.dumps(mission)
    (tmp_path / 'mission.json').write_text(text)
    path = tmp_path / 'arxiv' / 'page.xml'
    path.parent.mkdir()
    path.write_text('<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
                    '<id>http://arxiv.org/abs/1601.00001v1</id><title>Machine learning fixture</title>'
                    '<summary>Fixture abstract</summary><published>2016-01-01T00:00:00Z</published>'
                    '<updated>2016-01-01T00:00:00Z</updated><category term="cs.LG"/>'
                    '<author><name>Fixture Author</name></author></entry></feed>')
    manifest = {'mission_id': MISSION, 'query_version': MISSION + '/v99', 'connector_version': 'fixture',
                'fetch_started_utc': '2026-09-17T00:00:00Z', 'mission_snapshot_file': 'mission.json',
                'mission_file_sha256': module.sha256_of_text(text),
                'sources': {'arxiv': {'access_mode': 'atom-api', 'independent_discovery': True, 'total_records': 1,
                            'files': [{'file': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'records': 1}]}}}
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest))
    first, second = module.ingest(tmp_path, False), module.ingest(tmp_path, False)
    assert first['records'] == 1 and first['batch_id'] == second['batch_id'] and second['already_sealed'] is True
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute('SELECT seal_status FROM collection_batch WHERE batch_id = %s', (first['batch_id'],))
        assert cur.fetchone()[0] == 'sealed'
        cur.execute('SELECT count(*) FROM raw_record r JOIN source_snapshot s USING (snapshot_id) '
                    'WHERE s.query_version_id = %s', (MISSION + '/v99',))
        assert cur.fetchone()[0] == 1


@pytest.mark.parametrize('bad_membership', ['missing', 'extra', 'wrong_count'])
def test_package_cannot_seal_with_missing_extra_or_misdescribed_files(seeded, bad_membership):
    from psycopg.types.json import Jsonb
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute('SELECT source, file_name, file_sha256, record_count, snapshot_id FROM source_snapshot '
                    'WHERE mission_id = %s ORDER BY snapshot_id', (MISSION,))
        snapshots = cur.fetchall()
        expected = [{'source': s, 'file': f, 'sha256': h, 'records': n} for s, f, h, n, _ in snapshots]
        linked = snapshots
        if bad_membership == 'missing': linked = snapshots[:1]
        if bad_membership == 'extra': expected = expected[:1]
        if bad_membership == 'wrong_count': expected[0]['records'] += 1
        coverage = {'mission_file_sha256': '0' * 64, 'expected_corpus_files': expected}
        cur.execute("INSERT INTO collection_batch (mission_id, query_version_id, content_sha256, connector_version, "
                    "fetched_at, status, coverage) VALUES (%s, %s, 'bad-membership', 'test', now(), 'complete', %s) RETURNING batch_id",
                    (MISSION, MISSION + '/v1', Jsonb(coverage)))
        batch = cur.fetchone()[0]
        for *_, snapshot in linked:
            cur.execute('INSERT INTO collection_batch_snapshot VALUES (%s, %s)', (batch, snapshot))
        with pytest.raises(psycopg.errors.RaiseException):
            with conn.transaction():
                cur.execute("UPDATE collection_batch SET seal_status = 'sealed', sealed_at = now() WHERE batch_id = %s", (batch,))
        cur.execute('SELECT seal_status FROM collection_batch WHERE batch_id = %s', (batch,))
        assert cur.fetchone()[0] == 'open'


def test_same_bytes_under_new_query_receive_new_snapshot_and_cannot_be_mixed(seeded):
    from psycopg.types.json import Jsonb
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute('SELECT snapshot_id, file_name, file_sha256 FROM source_snapshot WHERE mission_id = %s '
                    "AND source = 'arxiv'", (MISSION,))
        old, name, checksum = cur.fetchone()
        cur.execute('INSERT INTO query_version (query_version_id, mission_id, version, terms, payload, content_sha256) '
                    'VALUES (%s, %s, 99, %s, %s, %s)', (MISSION + '/v99', MISSION, ['other'], Jsonb({}), 'new-query'))
        cur.execute('INSERT INTO source_snapshot (mission_id, query_version_id, source, connector_version, '
                    "file_name, file_sha256, fetched_at) VALUES (%s, %s, 'arxiv', 'test', %s, %s, now()) RETURNING snapshot_id",
                    (MISSION, MISSION + '/v99', name, checksum))
        new = cur.fetchone()[0]
        assert old != new
        cur.execute("INSERT INTO collection_batch (mission_id, query_version_id, content_sha256, connector_version, "
                    "fetched_at, status) VALUES (%s, %s, 'mixed-input', 'test', now(), 'complete') RETURNING batch_id",
                    (MISSION, MISSION + '/v1'))
        batch = cur.fetchone()[0]
        with pytest.raises(psycopg.errors.RaiseException, match='same mission/query'):
            with conn.transaction(): cur.execute('INSERT INTO collection_batch_snapshot VALUES (%s, %s)', (batch, new))


def test_query_import_is_idempotent_but_rejects_reusing_version_for_changed_config(seeded):
    import json
    from saia.ingest import upsert_query_version
    mission = {'mission_id': MISSION, 'query_version': MISSION + '/v99',
               'query': {'terms': ['machine learning'], 'exclusions': []}}
    text = json.dumps(mission)
    with db.connect() as conn, conn.cursor() as cur:
        assert upsert_query_version(cur, mission, text) == MISSION + '/v99'
        assert upsert_query_version(cur, mission, text) == MISSION + '/v99'
        changed = {**mission, 'query': {'terms': ['different query']}}
        with pytest.raises(ValueError, match='другими настройками'):
            upsert_query_version(cur, changed, json.dumps(changed))
        cur.execute('SELECT count(*), min(terms[1]) FROM query_version WHERE query_version_id = %s',
                    (MISSION + '/v99',))
        assert cur.fetchone() == (1, 'machine learning')


def cleanup_test_mission(cur) -> None:
    """Удалить только данные фикстуры, не трогая рабочие миссии.

    Сырые записи защищены от DELETE боевым триггером. На время удаления
    записей именно test-mission триггер выключается в этой транзакции и
    обязательно включается обратно. Прежний TRUNCATE mission CASCADE
    уничтожал всю локальную базу разработчика при обычном pytest.
    """
    # New public opinions/dispatches are deleted only for the disposable
    # mission, in the same test transaction, before RESTRICT-protected runs.
    try:
        cur.execute('ALTER TABLE public_signal_review DISABLE TRIGGER public_signal_review_no_change')
        cur.execute('DELETE FROM public_signal_review WHERE request_id IN '
                    '(SELECT request_id FROM expert_validation_request WHERE mission_id=%s)', (MISSION,))
    finally:
        cur.execute('ALTER TABLE public_signal_review ENABLE TRIGGER public_signal_review_no_change')
    try:
        cur.execute('ALTER TABLE expert_validation_request DISABLE TRIGGER expert_validation_request_no_change')
        cur.execute('DELETE FROM expert_validation_request WHERE mission_id=%s', (MISSION,))
    finally:
        cur.execute('ALTER TABLE expert_validation_request ENABLE TRIGGER expert_validation_request_no_change')
    # External evidence uses an independent topic identity. Tests bind it to
    # the disposable mission ID so unrelated live observations stay intact.
    try:
        cur.execute('ALTER TABLE candidate_analysis_note DISABLE TRIGGER candidate_analysis_note_no_change')
        cur.execute('DELETE FROM candidate_analysis_note WHERE mission_id=%s', (MISSION,))
    finally:
        cur.execute('ALTER TABLE candidate_analysis_note ENABLE TRIGGER candidate_analysis_note_no_change')
    try:
        cur.execute('ALTER TABLE candidate_presentation DISABLE TRIGGER candidate_presentation_no_change')
        cur.execute('DELETE FROM candidate_presentation WHERE mission_id=%s', (MISSION,))
    finally:
        cur.execute('ALTER TABLE candidate_presentation ENABLE TRIGGER candidate_presentation_no_change')
    # Delete only disposable links before their RESTRICT-protected observations.
    # This guard is restored within the same isolated-test transaction.
    try:
        cur.execute('ALTER TABLE candidate_external_evidence_link DISABLE TRIGGER candidate_external_evidence_link_no_change')
        cur.execute('DELETE FROM candidate_external_evidence_link WHERE mission_id=%s', (MISSION,))
    finally:
        cur.execute('ALTER TABLE candidate_external_evidence_link ENABLE TRIGGER candidate_external_evidence_link_no_change')
    try:
        cur.execute('ALTER TABLE external_evidence_observation DISABLE TRIGGER external_evidence_no_change')
        cur.execute('DELETE FROM external_evidence_observation WHERE topic_id=%s '
                    "OR payload->'candidate_context_binding'->>'mission_id'=%s", (MISSION, MISSION))
    finally:
        cur.execute('ALTER TABLE external_evidence_observation ENABLE TRIGGER external_evidence_no_change')
    # Durable job history is protected in production. Remove only this
    # disposable fixture, restoring both guards in the same transaction.
    try:
        cur.execute('ALTER TABLE analysis_job_event DISABLE TRIGGER analysis_job_event_no_change')
        cur.execute('DELETE FROM analysis_job_event WHERE job_id IN '
                    '(SELECT job_id FROM analysis_job WHERE mission_id=%s)', (MISSION,))
    finally:
        cur.execute('ALTER TABLE analysis_job_event ENABLE TRIGGER analysis_job_event_no_change')
    try:
        cur.execute('ALTER TABLE analysis_job DISABLE TRIGGER analysis_job_state_guard')
        cur.execute('DELETE FROM analysis_job WHERE mission_id=%s', (MISSION,))
    finally:
        cur.execute('ALTER TABLE analysis_job ENABLE TRIGGER analysis_job_state_guard')
    # Disposable synthetic opinions only; restore the guard in the transaction.
    try:
        cur.execute('ALTER TABLE expert_review DISABLE TRIGGER expert_review_no_change')
        cur.execute('DELETE FROM expert_review WHERE snapshot_id IN '
                    '(SELECT snapshot_id FROM hybrid_snapshot WHERE mission_id = %s)', (MISSION,))
    finally:
        cur.execute('ALTER TABLE expert_review ENABLE TRIGGER expert_review_no_change')
    try:
        cur.execute('ALTER TABLE composition_assessment_snapshot DISABLE TRIGGER composition_assessment_no_delete')
        cur.execute('DELETE FROM composition_assessment_snapshot WHERE snapshot_id IN '
                    '(SELECT snapshot_id FROM hybrid_snapshot WHERE mission_id=%s)', (MISSION,))
    finally:
        cur.execute('ALTER TABLE composition_assessment_snapshot ENABLE TRIGGER composition_assessment_no_delete')
    cur.execute('DELETE FROM hybrid_snapshot WHERE mission_id = %s', (MISSION,))
    cur.execute('DELETE FROM query_expansion_proposal WHERE mission_id = %s', (MISSION,))
    try:
        cur.execute('ALTER TABLE score_candidate_review DISABLE TRIGGER score_candidate_review_no_change')
        cur.execute('DELETE FROM score_candidate_review WHERE score_run_id IN '
                    '(SELECT run_id FROM analysis_run WHERE mission_id=%s)', (MISSION,))
    finally:
        cur.execute('ALTER TABLE score_candidate_review ENABLE TRIGGER score_candidate_review_no_change')
    # RESTRICT защищает публикации, на которые ссылаются доказательства.
    # Удаляем доказательства и производные прогоны только этой фикстуры,
    # начиная с потомков: каскад миссии не гарантирует такой порядок.
    try:
        cur.execute('ALTER TABLE evidence_item DISABLE TRIGGER completed_evidence_item_no_change')
        cur.execute(
            "DELETE FROM evidence_item WHERE candidate_id IN "
            "(SELECT c.candidate_id FROM signal_candidate c "
            "JOIN analysis_run r ON r.run_id = c.run_id WHERE r.mission_id = %s)",
            (MISSION,),
        )
    finally:
        cur.execute('ALTER TABLE evidence_item ENABLE TRIGGER completed_evidence_item_no_change')
    cur.execute(
        "SELECT run_id FROM analysis_run WHERE mission_id = %s ORDER BY run_id DESC",
        (MISSION,),
    )
    run_ids = [row[0] for row in cur.fetchall()]
    try:
        cur.execute('ALTER TABLE signal_candidate DISABLE TRIGGER completed_signal_candidate_no_change')
        for run_id in run_ids:
            cur.execute(
                "DELETE FROM analysis_run WHERE run_id = %s AND mission_id = %s",
                (run_id, MISSION),
            )
    finally:
        cur.execute('ALTER TABLE signal_candidate ENABLE TRIGGER completed_signal_candidate_no_change')
    # Пакеты ссылаются на снимки через RESTRICT; удаляем пакеты только
    # собственной одноразовой фикстуры перед каскадом mission/snapshot.
    cur.execute('DELETE FROM collection_batch WHERE mission_id = %s', (MISSION,))
    try:
        cur.execute("ALTER TABLE raw_record DISABLE TRIGGER raw_record_no_update")
        cur.execute(
            "DELETE FROM raw_record WHERE snapshot_id IN "
            "(SELECT snapshot_id FROM source_snapshot WHERE mission_id = %s)",
            (MISSION,),
        )
    finally:
        cur.execute("ALTER TABLE raw_record ENABLE TRIGGER raw_record_no_update")
    cur.execute("DELETE FROM mission WHERE mission_id = %s", (MISSION,))


def arxiv_payload(identifier: str, title: str, created: str) -> dict:
    """Форма записи такая, какую отдаёт читатель OAI в saia.ingest."""
    return {
        "id": identifier, "title": title, "created": created,
        "summary": "abstract text", "categories": "cs.LG",
        "author": [{"name": "Ivan Ivanov"}],
    }


def openalex_payload(identifier: str, title: str, date: str, doi: str) -> dict:
    return {
        "id": f"https://openalex.org/{identifier}", "title": title,
        "publication_date": date, "publication_year": int(date[:4]),
        "doi": f"https://doi.org/{doi}",
        "authorships": [{"author": {"id": "A1", "display_name": "Ivan Ivanov"},
                         "institutions": [], "author_position": "first"}],
        "abstract_inverted_index": None, "cited_by_count": 0,
        "type": "article", "language": "en", "is_retracted": False,
    }


@pytest.fixture()
def seeded(tmp_path):
    """Миссия с двумя версиями одной работы: препринт мартом, журнал декабрём."""
    from psycopg.types.json import Jsonb

    with db.connect() as conn, conn.cursor() as cur:
        cleanup_test_mission(cur)
        cur.execute(
            "INSERT INTO mission (mission_id, title, question, as_of_date, "
            "period_from, period_to, sources) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (MISSION, "тест", "вопрос?", "2018-01-01", "2010-01-01",
             "2017-12-31", ["arxiv", "openalex"]),
        )
        cur.execute(
            "INSERT INTO query_version (query_version_id, mission_id, version, "
            "terms, payload, content_sha256) VALUES (%s, %s, 1, %s, %s, %s)",
            (f"{MISSION}/v1", MISSION, ["test"], Jsonb({}), "0" * 64),
        )
        for source, record_id, payload in [
            ("openalex", "W1", openalex_payload("W1", "Graph Convolutional Networks",
                                                "2017-12-04", "10.1109/X.2017.1")),
            ("arxiv", "1609.02907", arxiv_payload("1609.02907",
                                                  "Graph Convolutional Networks",
                                                  "2016-09-09")),
        ]:
            cur.execute(
                "INSERT INTO source_snapshot (mission_id, query_version_id, source, "
                "connector_version, file_name, file_sha256, fetched_at) "
                "VALUES (%s, %s, %s, '0.1.0', %s, %s, now()) RETURNING snapshot_id",
                (MISSION, f"{MISSION}/v1", source, f"{record_id}.json", record_id),
            )
            snapshot_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO raw_record (snapshot_id, source, source_record_id, "
                "payload, content_sha256) VALUES (%s, %s, %s, %s, %s)",
                (snapshot_id, source, record_id, Jsonb(payload),
                 hashlib.sha256(record_id.encode()).hexdigest()),
            )
        conn.commit()
    yield
    with db.connect() as conn, conn.cursor() as cur:
        cleanup_test_mission(cur)
        conn.commit()


def test_normalization_records_unproven_arxiv_identity_without_merging(seeded):
    from psycopg.types.json import Jsonb
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute('INSERT INTO source_snapshot (mission_id, query_version_id, source, '
                    'connector_version, file_name, file_sha256, fetched_at) '
                    "VALUES (%s, %s, 'arxiv', 'fixture', 'other-deposit', 'other-sha', now()) RETURNING snapshot_id",
                    (MISSION, MISSION + '/v1'))
        snapshot = cur.fetchone()[0]
        cur.execute('INSERT INTO raw_record (snapshot_id, source, source_record_id, payload, content_sha256) '
                    "VALUES (%s, 'arxiv', '1610.00001', %s, 'other-record-sha')",
                    (snapshot, Jsonb(arxiv_payload('1610.00001', 'Graph Convolutional Networks', '2016-10-01'))))
    result = normalize.normalize(MISSION, verbose=False)
    assert result['created'] == 2 and result['merged'] == 1 and result['blocked'] == 1
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute('SELECT d.rule FROM dedup_decision d JOIN work w USING(work_id) '
                    'WHERE w.run_id = %s AND d.record_id = %s ORDER BY d.rule',
                    (result['run_id'], '1610.00001'))
        assert cur.fetchall() == [('blocked_conflicting_arxiv_ids',), ('new_work',)]


def _append_text_candidate(cur, source, identifier, payload, filename):
    """Append a new source snapshot, never edit an immutable raw fixture."""
    import json
    from psycopg.types.json import Jsonb
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    cur.execute('INSERT INTO source_snapshot (mission_id, query_version_id, source, '
                'connector_version, file_name, file_sha256, fetched_at) '
                "VALUES (%s, %s, %s, 'fixture', %s, %s, now()) RETURNING snapshot_id",
                (MISSION, MISSION + '/v1', source, filename, digest))
    snapshot = cur.fetchone()[0]
    cur.execute('INSERT INTO raw_record (snapshot_id, source, source_record_id, payload, content_sha256) '
                'VALUES (%s, %s, %s, %s, %s) RETURNING raw_record_id',
                (snapshot, source, identifier, Jsonb(payload), digest))
    return cur.fetchone()[0]


@pytest.mark.parametrize('revision', ['2016-09-09', '2018-02-01', None])
def test_canonical_text_binding_and_legacy_generation_are_separate(seeded, revision):
    from saia import canonical_text
    title = 'Graph Convolutional Networks'
    oa = openalex_payload('W1', title, '2017-12-04', '10.1109/X.2017.1')
    oa['abstract_inverted_index'] = {'Unrelated': [0], 'aggregate': [1], 'text': [2]}
    native = arxiv_payload('1609.02907', title, '2016-09-09')
    native['summary'] = 'A native abstract about the original method.'
    if revision:
        native['updated'] = revision
    with db.connect() as conn, conn.cursor() as cur:
        aggregate_id = _append_text_candidate(cur, 'openalex', 'W1', oa, 'text-aggregate.json')
        native_id = _append_text_candidate(cur, 'arxiv', '1609.02907', native, 'text-native.json')
        batch = sealed_fixture_batch(cur, 'text-bound-fixture')
    legacy = normalize.normalize(MISSION, verbose=False, text_policy_mode=canonical_text.LEGACY)
    result = normalize.normalize(MISSION, verbose=False)
    expected_native = revision == '2016-09-09'
    assert result['created'] == legacy['created'] == 1
    assert result['canonical_text_selection']['abstracts_changed'] == int(expected_native)
    from saia.full_analysis import _existing_normalize
    assert _existing_normalize(MISSION, batch, MISSION + '/v1') == result['run_id']
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute('SELECT w.abstract,p.abstract_raw_record_id,p.selection,p.abstract_sha256 '
                    'FROM work w JOIN work_text_provenance p USING(work_id) WHERE w.run_id=%s',
                    (result['run_id'],))
        abstract, raw_id, selection, digest = cur.fetchone()
        assert raw_id == (native_id if expected_native else aggregate_id)
        assert abstract == (native['summary'] if expected_native else 'Unrelated aggregate text')
        assert digest == canonical_text.text_sha256(abstract)
        assert selection['chosen_raw_record_id'] == raw_id
        cur.execute('SELECT abstract FROM work WHERE run_id=%s', (legacy['run_id'],))
        assert cur.fetchone()[0] == 'Unrelated aggregate text'
        # A duplicate source ID may be omitted from work_version by its unique
        # constraint; the field provenance still binds the actual selected raw.
        cur.execute('SELECT count(*) FROM work_version WHERE run_id=%s', (result['run_id'],))
        assert cur.fetchone()[0] == 2
        cur.execute('SELECT payload FROM raw_record WHERE raw_record_id=%s', (native_id,))
        assert cur.fetchone()[0] == native


def test_legacy_normalization_is_not_reused_by_current_text_policy(seeded):
    from saia import canonical_text
    from saia.full_analysis import _existing_normalize
    with db.connect() as conn, conn.cursor() as cur:
        batch = sealed_fixture_batch(cur, 'legacy-text-policy')
    result = normalize.normalize(MISSION, verbose=False, text_policy_mode=canonical_text.LEGACY)
    assert result['run_id'] is not None
    assert _existing_normalize(MISSION, batch, MISSION + '/v1') is None


@pytest.mark.parametrize('operation', ['update', 'delete', 'move_to_new_running_work'])
def test_completed_text_provenance_is_immutable_in_database(seeded, operation):
    result = normalize.normalize(MISSION, verbose=False)
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute('SELECT work_id FROM work WHERE run_id=%s', (result['run_id'],))
        original_work = cur.fetchone()[0]
        new_run = runs.start_run(cur, MISSION, 'normalize', methodology.load_default())
        cur.execute("UPDATE analysis_run SET status='normalizing' WHERE run_id=%s", (new_run,))
        cur.execute('INSERT INTO work (mission_id,run_id,canonical_title,title_key,abstract,effective_date) '
                    "VALUES (%s,%s,'Other work','other work','abstract text','2016-01-01') RETURNING work_id",
                    (MISSION, new_run))
        other_work = cur.fetchone()[0]
        with pytest.raises(psycopg.errors.RaiseException, match='immutable'):
            with conn.transaction():
                if operation == 'delete':
                    cur.execute('DELETE FROM work_text_provenance WHERE work_id=%s', (original_work,))
                elif operation == 'update':
                    cur.execute("UPDATE work_text_provenance SET selection=selection || '{\"extra\":true}' "
                                'WHERE work_id=%s', (original_work,))
                else:
                    cur.execute('UPDATE work_text_provenance SET work_id=%s,run_id=%s WHERE work_id=%s',
                                (other_work, new_run, original_work))


@pytest.mark.parametrize('problem', ['wrong_checksum', 'foreign_raw_source', 'wrong_payload'])
def test_text_provenance_rejects_invalid_source_binding_or_checksums(seeded, problem):
    from psycopg.types.json import Jsonb
    from saia import canonical_text
    with db.connect() as conn, conn.cursor() as cur:
        source = openalex_payload('W2', 'Different unrelated method', '2016-05-01', '10.1234/other')
        source['abstract_inverted_index'] = {'Other': [0], 'abstract': [1]}
        foreign_raw = _append_text_candidate(cur, 'openalex', 'W2', source, 'foreign-text.json')
    result = normalize.normalize(MISSION, verbose=False)
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT w.work_id,w.abstract,p.abstract_raw_record_id,p.selection FROM work w "
                    'JOIN work_text_provenance p USING(work_id) WHERE w.run_id=%s '
                    "AND w.canonical_title='Graph Convolutional Networks'", (result['run_id'],))
        original_work, abstract, raw_id, selection = cur.fetchone()
        new_run = runs.start_run(cur, MISSION, 'normalize', methodology.load_default())
        cur.execute("UPDATE analysis_run SET status='normalizing' WHERE run_id=%s", (new_run,))
        cur.execute('INSERT INTO work (mission_id,run_id,canonical_title,title_key,abstract,effective_date) '
                    "VALUES (%s,%s,'Bound method','bound method',%s,'2016-01-01') RETURNING work_id",
                    (MISSION, new_run, abstract))
        new_work = cur.fetchone()[0]
        cur.execute('INSERT INTO work_version '
                    '(work_id,run_id,raw_record_id,source,source_record_id,version_kind) '
                    'SELECT %s,%s,raw_record_id,source,source_record_id,version_kind '
                    'FROM work_version WHERE work_id=%s', (new_work, new_run, original_work))
        digest = canonical_text.text_sha256(abstract)
        selection = {**selection}
        if problem == 'wrong_checksum':
            digest = '0' * 64
            selection['abstract_sha256'] = digest
        elif problem == 'foreign_raw_source':
            raw_id = foreign_raw
            selection['chosen_raw_record_id'] = raw_id
        else:
            selection['chosen_raw_record_id'] = None
        with pytest.raises(psycopg.errors.RaiseException):
            with conn.transaction():
                cur.execute('INSERT INTO work_text_provenance '
                            '(work_id,run_id,policy_version,abstract_raw_record_id,abstract_sha256,selection) '
                            'VALUES (%s,%s,%s,%s,%s,%s)',
                            (new_work, new_run, canonical_text.VERSION, raw_id, digest, Jsonb(selection)))


def test_arxiv_doi_identity_survives_conflicting_openalex_location_in_database(seeded):
    from psycopg.types.json import Jsonb
    payload = openalex_payload('W2', 'Independent method', '2016-01-01',
                               '10.48550/arxiv.1601.00011')
    payload['locations'] = [{'landing_page_url': 'https://arxiv.org/abs/1708.06073'}]
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute('INSERT INTO source_snapshot (mission_id, query_version_id, source, '
                    'connector_version, file_name, file_sha256, fetched_at) '
                    "VALUES (%s, %s, 'openalex', 'fixture', 'conflicting-link', 'link-sha', now()) RETURNING snapshot_id",
                    (MISSION, MISSION + '/v1'))
        snapshot = cur.fetchone()[0]
        cur.execute('INSERT INTO raw_record (snapshot_id, source, source_record_id, payload, content_sha256) '
                    "VALUES (%s, 'openalex', 'W2', %s, 'link-record-sha')", (snapshot, Jsonb(payload)))
    result = normalize.normalize(MISSION, verbose=False)
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT value FROM identifier WHERE run_id=%s AND kind='arxiv' ORDER BY value",
                    (result['run_id'],))
        assert cur.fetchall() == [('1601.00011',), ('1609.02907',)]


def test_conservative_doi_assertions_split_new_generation_and_preserve_legacy(seeded):
    from psycopg.types.json import Jsonb
    from saia.quality import evaluate_mission
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute('UPDATE query_version SET payload=%s WHERE query_version_id=%s',
                    (Jsonb({'query': {'arxiv_categories': ['cs.LG']}, 'as_of_date': '2018-01-01'}), MISSION + '/v1'))
        for number, title in [(1, 'A concrete method'), (2, 'A different method')]:
            identifier = f'1601.0000{number}'
            payload = arxiv_payload(identifier, title, '2016-01-10')
            payload['arxiv_doi'] = '10.1234/disputed'
            cur.execute('INSERT INTO source_snapshot (mission_id, query_version_id, source, connector_version, '
                        'file_name, file_sha256, fetched_at) VALUES (%s,%s,%s,%s,%s,%s,now()) RETURNING snapshot_id',
                        (MISSION, MISSION + '/v1', 'arxiv', 'fixture', identifier, identifier))
            cur.execute('INSERT INTO raw_record (snapshot_id, source, source_record_id, payload, content_sha256) '
                        'VALUES (%s,%s,%s,%s,%s)',
                        (cur.fetchone()[0], 'arxiv', identifier, Jsonb(payload), identifier))
    old = normalize.normalize(MISSION, verbose=False, identity_mode='legacy')
    assert old['created'] == 2 and old['merged'] == 2
    with db.connect() as conn:
        before = conn.execute('SELECT work_id, canonical_title, effective_date FROM work WHERE run_id=%s ORDER BY work_id', (old['run_id'],)).fetchall()
    new = normalize.normalize(MISSION, verbose=False)
    assert new['created'] == 3 and new['merged'] == 1
    assert new['ambiguous_doi_count'] == 1 and new['identity_conflict_assertions'] == 2
    with db.connect() as conn:
        assert before == conn.execute('SELECT work_id, canonical_title, effective_date FROM work WHERE run_id=%s ORDER BY work_id', (old['run_id'],)).fetchall()
        assert conn.execute("SELECT count(*) FROM identifier WHERE run_id=%s AND kind='doi' AND value=%s", (new['run_id'], '10.1234/disputed')).fetchone()[0] == 0
        assert conn.execute("SELECT count(DISTINCT work_id) FROM identifier WHERE run_id=%s AND kind='arxiv' AND value=ANY(%s)", (new['run_id'], ['1601.00001', '1601.00002'])).fetchone()[0] == 2
        notes = conn.execute('SELECT notes FROM analysis_run WHERE run_id=%s', (new['run_id'],)).fetchone()[0]
        assert len(notes['identity_conflict_queue']) == 2
        assert notes['normalization_policy']['version'] == 'canonical-identity-0.4.5'
        assert notes['normalization_policy_sha256']
    result = evaluate_mission(MISSION)
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM quality_snapshot WHERE generation_id=%s AND flags->>'canonical_publication_identity_unknown'='true'", (result['quality_generation_id'],)).fetchone()[0] == 2


def test_withdrawn_deposit_does_not_quarantine_another_source_identity(seeded):
    from psycopg.types.json import Jsonb
    from saia.quality import evaluate_mission
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute('UPDATE query_version SET payload=%s WHERE query_version_id=%s',
                    (Jsonb({'query': {'arxiv_categories': ['cs.LG']}, 'as_of_date': '2018-01-01'}), MISSION + '/v1'))
        for number in (1, 2):
            identifier = f'1601.0000{number}'
            payload = arxiv_payload(identifier, 'A concrete method', '2016-01-10')
            payload['arxiv_doi'] = '10.1234/disputed'
            if number == 1:
                payload['arxiv_comment'] = 'This paper has been withdrawn by the author due to an error.'
            cur.execute('INSERT INTO source_snapshot (mission_id, query_version_id, source, connector_version, '
                        'file_name, file_sha256, fetched_at) VALUES (%s,%s,%s,%s,%s,%s,now()) RETURNING snapshot_id',
                        (MISSION, MISSION + '/v1', 'arxiv', 'fixture', identifier, identifier))
            cur.execute('INSERT INTO raw_record (snapshot_id, source, source_record_id, payload, content_sha256) '
                        'VALUES (%s,%s,%s,%s,%s)',
                        (cur.fetchone()[0], 'arxiv', identifier, Jsonb(payload), identifier))
    normalize.normalize(MISSION, verbose=False)
    result = evaluate_mission(MISSION)
    with db.connect() as conn:
        rows = conn.execute("SELECT i.value, q.decision, q.flags FROM quality_snapshot q JOIN identifier i USING(work_id) WHERE q.generation_id=%s AND i.kind='arxiv' AND i.value=ANY(%s) ORDER BY i.value",
                            (result['quality_generation_id'], ['1601.00001', '1601.00002'])).fetchall()
        assert [(r[0], r[1]) for r in rows] == [('1601.00001', 'quarantine'), ('1601.00002', 'include')]
        evidence = rows[0][2]['deposit_status_evidence'][0]
        assert evidence['status_observed_at'] and evidence['status_effective_date'] is None


def test_repeated_oa_identity_retains_second_raw_status_without_duplicate_work(seeded):
    from psycopg.types.json import Jsonb
    from saia.quality import evaluate_mission
    payload = openalex_payload('W1', 'Graph Convolutional Networks', '2017-12-04', '10.1109/X.2017.1')
    payload['is_retracted'] = True
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute('UPDATE query_version SET payload=%s WHERE query_version_id=%s',
                    (Jsonb({'query': {'arxiv_categories': ['cs.LG']}, 'as_of_date': '2018-01-01'}), MISSION + '/v1'))
        cur.execute('INSERT INTO source_snapshot (mission_id, query_version_id, source, connector_version, '
                    'file_name, file_sha256, fetched_at) VALUES (%s,%s,%s,%s,%s,%s,now()) RETURNING snapshot_id',
                    (MISSION, MISSION + '/v1', 'openalex', 'fixture', 'W1-second', 'W1-second'))
        cur.execute('INSERT INTO raw_record (snapshot_id, source, source_record_id, payload, content_sha256) '
                    'VALUES (%s,%s,%s,%s,%s) RETURNING raw_record_id',
                    (cur.fetchone()[0], 'openalex', 'W1', Jsonb(payload), 'W1-second'))
        raw_id = cur.fetchone()[0]
    normalized = normalize.normalize(MISSION, verbose=False)
    assert normalized['created'] == 1 and normalized['merged'] == 2
    with db.connect() as conn:
        notes = conn.execute('SELECT notes FROM analysis_run WHERE run_id=%s', (normalized['run_id'],)).fetchone()[0]
        assert len(notes['source_identity_decisions']) == 1
        assert notes['source_identity_decisions'][0]['raw_record_id'] == raw_id
    result = evaluate_mission(MISSION)
    with db.connect() as conn:
        flags = conn.execute('SELECT flags FROM quality_snapshot WHERE generation_id=%s', (result['quality_generation_id'],)).fetchone()[0]
        assert any(e['status'] == 'retracted' and e['raw_record_id'] == raw_id for e in flags['deposit_status_evidence'])


def test_exact_corpus_comparison_reads_real_finished_generations(seeded):
    from saia.corpus_comparison import analyze
    old = normalize.normalize(MISSION, verbose=False)['run_id']
    new = normalize.normalize(MISSION, verbose=False)['run_id']
    result = analyze(old, new)
    assert result['baseline_provenance']['run_id'] == old
    assert result['target_provenance']['run_id'] == new
    assert result['comparison']['same_embedding_text_pairs'] == 1
    assert result['comparison']['changed_embedding_text_pairs'] == 0
    assert result['comparison']['missing_in_target'] == []


def test_strict_cluster_pins_old_quality_and_rejects_missing_vectors(seeded):
    from psycopg.types.json import Jsonb
    from saia import cluster
    from saia.quality import evaluate_mission
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute('UPDATE query_version SET payload=%s WHERE mission_id=%s',
                    (Jsonb({'query': {'terms': ['graph convolutional networks']}, 'as_of_date': '2018-01-01'}), MISSION))
    root = normalize.normalize(MISSION, verbose=False)['run_id']
    generation = evaluate_mission(MISSION)['quality_generation_id']
    newer = normalize.normalize(MISSION, verbose=False)['run_id']
    assert root != newer
    with db.connect() as conn, conn.cursor() as cur:
        assert cluster.resolve_quality_input(cur, MISSION, generation) == (root, generation)
        cur.execute("SELECT count(*) FROM analysis_run WHERE mission_id=%s AND kind='cluster'", (MISSION,))
        before = cur.fetchone()[0]
    from saia.embedding_status import read as readiness
    pinned = readiness(MISSION, generation, 'uncomputed-fixture-model')
    assert pinned['normalize_run_id'] == root
    assert pinned['state'] == 'pending' and not pinned['ready_for_strict_clustering']
    with pytest.raises(ValueError, match='Векторы не готовы'):
        cluster.analyze(MISSION, generation, 'uncomputed-fixture-model', backend='graph')
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM analysis_run WHERE mission_id=%s AND kind='cluster'", (MISSION,))
        assert cur.fetchone()[0] == before


def test_embedding_job_retains_complete_batches_and_resumes_exact_input(seeded, monkeypatch):
    from psycopg.types.json import Jsonb
    from saia import embed
    from saia.quality import evaluate_mission
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute('UPDATE query_version SET payload=%s WHERE mission_id=%s',
                    (Jsonb({'query': {'terms': ['graph convolutional networks']}, 'as_of_date': '2018-01-01'}), MISSION))
        for n in [1, 2]:
            record_id = f'1601.0000{n}'
            cur.execute('INSERT INTO source_snapshot (mission_id,query_version_id,source,connector_version,file_name,file_sha256,fetched_at) '
                        "VALUES (%s,%s,'arxiv','fixture',%s,%s,now()) RETURNING snapshot_id",
                        (MISSION, MISSION + '/v1', record_id, record_id))
            snapshot = cur.fetchone()[0]
            cur.execute('INSERT INTO raw_record(snapshot_id,source,source_record_id,payload,content_sha256) '
                        "VALUES (%s,'arxiv',%s,%s,%s)",
                        (snapshot, record_id, Jsonb(arxiv_payload(record_id, f'Graph Convolutional Networks application {n}', '2016-01-12')), record_id))
    root = normalize.normalize(MISSION, verbose=False)['run_id']
    generation = evaluate_mission(MISSION)['quality_generation_id']
    class Model:
        name = 'resumable-fixture'
        def embed(self, texts): return [[1.,0.] for _ in texts]
    class Interrupted(Model):
        calls = 0
        def embed(self, texts):
            self.calls += 1
            if self.calls > 1: raise embed.EmbeddingError('Synthetic interruption')
            return super().embed(texts)
    monkeypatch.setenv('SAIA_EMBEDDING_BATCH', '1')
    monkeypatch.setattr(embed, 'make_embedder', Interrupted)
    with pytest.raises(embed.EmbeddingError, match='Synthetic'):
        embed.embed_mission(MISSION, quality_generation_id=generation)
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute('SELECT e.work_id,e.embedding::text,e.created_at FROM work_embedding e JOIN work w USING(work_id) '
                    'WHERE w.run_id=%s AND e.model=%s ORDER BY e.work_id', (root, Model.name))
        retained = cur.fetchall()
        assert len(retained) == 1
    from saia.embedding_status import read as readiness
    partial = readiness(MISSION, generation, Model.name)
    assert partial['state'] == 'partial' and partial['committed_vectors'] == 1
    assert not partial['ready_for_strict_clustering']
    monkeypatch.setattr(embed, 'make_embedder', Model)
    resumed = embed.embed_mission(MISSION, quality_generation_id=generation)
    assert resumed['computed'] == 2 and resumed['skipped'] == 1
    complete = readiness(MISSION, generation, Model.name)
    assert complete['state'] == 'complete' and complete['committed_vectors'] == 3
    assert complete['ready_for_strict_clustering']
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute('SELECT e.work_id,e.embedding::text,e.created_at FROM work_embedding e JOIN work w USING(work_id) '
                    'WHERE w.run_id=%s AND e.model=%s ORDER BY e.work_id', (root, Model.name))
        assert cur.fetchall()[0] == retained[0]


def test_identical_text_embedding_is_reused_across_normalize_runs(seeded, monkeypatch):
    from psycopg.types.json import Jsonb
    from saia import embed
    from saia.quality import evaluate_mission

    with db.connect() as conn, conn.cursor() as cur:
        cur.execute('UPDATE query_version SET payload=%s WHERE mission_id=%s',
                    (Jsonb({'query': {'terms': ['graph convolutional networks']},
                            'as_of_date': '2018-01-01'}), MISSION))

    class FirstModel:
        name = 'exact-text-reuse-fixture'
        def embed(self, texts):
            return [[1.0, 0.0] for _ in texts]

    first_run = normalize.normalize(MISSION, verbose=False)['run_id']
    first_generation = evaluate_mission(MISSION)['quality_generation_id']
    monkeypatch.setattr(embed, 'make_embedder', FirstModel)
    first = embed.embed_mission(MISSION, quality_generation_id=first_generation)
    assert first['computed'] == 1 and first['reused_exact_text'] == 0

    second_run = normalize.normalize(MISSION, verbose=False)['run_id']
    assert first_run != second_run
    second_generation = evaluate_mission(MISSION)['quality_generation_id']

    class NoModelCall(FirstModel):
        def embed(self, _texts):
            raise AssertionError('Exact text should be reused without model inference')

    monkeypatch.setattr(embed, 'make_embedder', NoModelCall)
    second = embed.embed_mission(MISSION, quality_generation_id=second_generation)
    assert second['computed'] == 0 and second['reused_exact_text'] == 1
    with db.connect() as conn:
        rows = conn.execute(
            'SELECT w.run_id,e.embedding::text FROM work_embedding e '
            'JOIN work w USING(work_id) WHERE e.model=%s AND w.run_id=ANY(%s) '
            'ORDER BY w.run_id', (FirstModel.name, [first_run, second_run]),
        ).fetchall()
    assert len(rows) == 2 and rows[0][1] == rows[1][1]


def test_normalize_creates_a_finished_run(seeded):
    """Дефект P0-6: результат обязан быть привязан к прогону."""
    summary = normalize.normalize(MISSION, verbose=False)
    run_id = summary["run_id"]

    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT status, methodology_hash, code_version, as_of_date "
            "FROM analysis_run WHERE run_id = %s",
            (run_id,),
        )
        status, hash_value, code_version, as_of = cur.fetchone()

    assert status == "done"
    assert hash_value == methodology.load_default().config_hash
    assert code_version
    assert str(as_of) == "2018-01-01"


def test_new_query_does_not_change_provenance_of_old_upstream(seeded):
    from psycopg.types.json import Jsonb
    parent = normalize.normalize(MISSION, verbose=False)['run_id']
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            'INSERT INTO query_version (query_version_id, mission_id, version, terms, '
            'payload, content_sha256) VALUES (%s, %s, 2, %s, %s, %s)',
            (f'{MISSION}/v2', MISSION, ['different query'], Jsonb({}), '1' * 64))
        derived = runs.start_run(cur, MISSION, 'cluster', methodology.load_default(),
                                 upstream_run_id=parent)
        cur.execute('SELECT query_version_id FROM analysis_run WHERE run_id = %s', (derived,))
        assert cur.fetchone()[0] == f'{MISSION}/v1'
        conn.commit()


def test_normalized_period_survives_changed_mission_period(seeded):
    first = normalize.normalize(MISSION, verbose=False)['run_id']
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE mission SET period_from = '2017-01-01' WHERE mission_id = %s", (MISSION,))
        start, end, origin = runs.analysis_period(cur, first)
        assert str(start) == '2010-01-01'
        assert str(end) == '2018-01-01'
        assert origin == 'run_snapshot'


def test_quality_generations_do_not_mutate_previous_decisions(seeded):
    from saia.quality import evaluate_mission
    normalize.normalize(MISSION, verbose=False)
    first = evaluate_mission(MISSION)['quality_generation_id']
    second = evaluate_mission(MISSION)['quality_generation_id']
    assert first != second
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute('SELECT generation_id, count(*) FROM quality_snapshot '
                    'WHERE generation_id = ANY(%s) GROUP BY generation_id', ([first, second],))
        counts = dict(cur.fetchall())
    assert counts[first] > 0
    assert counts[first] == counts[second]


def test_finished_quality_generation_is_protected_by_database(seeded):
    from saia.quality import evaluate_mission
    normalize.normalize(MISSION, verbose=False)
    generation = evaluate_mission(MISSION)['quality_generation_id']
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute('SELECT code_version, runtime_provenance FROM quality_generation '
                    'WHERE generation_id = %s', (generation,))
        code, runtime = cur.fetchone()
        assert code and runtime['packages']['numpy']
        with pytest.raises(psycopg.errors.RaiseException):
            with conn.transaction():
                cur.execute("UPDATE quality_snapshot SET decision = 'exclude' "
                            "WHERE generation_id = %s", (generation,))
        with pytest.raises(psycopg.errors.RaiseException):
            with conn.transaction():
                cur.execute("UPDATE quality_generation SET policy_version = 'changed' "
                            "WHERE generation_id = %s", (generation,))
        with pytest.raises(psycopg.errors.RaiseException):
            with conn.transaction():
                cur.execute('INSERT INTO quality_snapshot SELECT * FROM quality_snapshot '
                            'WHERE generation_id = %s', (generation,))


def test_terminology_uses_requested_quality_generation(seeded):
    from psycopg.types.json import Jsonb
    from saia.quality import evaluate_mission
    from saia.terminology import analyze_mission
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute('UPDATE query_version SET payload = %s WHERE mission_id = %s',
                    (Jsonb({'query': {'terms': ['graph convolutional networks']},
                            'as_of_date': '2018-01-01'}), MISSION))
    normal = normalize.normalize(MISSION, verbose=False)['run_id']
    generation = evaluate_mission(MISSION)['quality_generation_id']
    result = analyze_mission(MISSION, generation)
    assert result['provenance']['normalize_run_id'] == normal
    assert result['provenance']['quality_generation_id'] == generation
    assert result['provenance']['query_version_id'] == f'{MISSION}/v1'
    assert result['corpus_works'] == 1
    assert result['candidates'] == []
    phrase = next(c for c in result['low_support_review'] if c['phrase'] == 'graph convolutional networks')
    assert phrase['first_observed_in_corpus'] == '2016-09-09'
    sources = phrase['contexts'][0]['source_records']
    assert {s['source'] for s in sources} == {'arxiv', 'openalex'}
    with pytest.raises(ValueError, match='другой миссии'):
        analyze_mission('nonexistent-mission', generation)


def test_embedding_pins_quality_and_database_protects_completed_inputs(seeded, monkeypatch):
    from psycopg.types.json import Jsonb
    from saia.quality import evaluate_mission
    from saia import embed
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute('UPDATE query_version SET payload = %s WHERE mission_id = %s',
                    (Jsonb({'query': {'terms': ['graph convolutional networks']},
                            'as_of_date': '2018-01-01'}), MISSION))
    normal = normalize.normalize(MISSION, verbose=False)['run_id']
    generation = evaluate_mission(MISSION)['quality_generation_id']
    monkeypatch.setattr(embed, 'make_embedder', lambda: embed.HashingNgramEmbedder(dim=64))
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE work_quality SET decision = 'exclude' WHERE run_id = %s", (normal,))
    result = embed.embed_mission(MISSION, quality_generation_id=generation)
    assert result['normalize_run_id'] == normal
    assert result['quality_generation_id'] == generation
    assert result['computed'] == 1
    repeated = embed.embed_mission(MISSION, quality_generation_id=generation)
    assert repeated['computed'] == 0 and repeated['skipped'] == 1
    with db.connect() as conn, conn.cursor() as cur:
        cluster = runs.start_run(cur, MISSION, 'cluster', methodology.load_default(),
                                 embedding_model=result['model'], upstream_run_id=normal)
        runs.finish_run(cur, cluster)
        with pytest.raises(psycopg.errors.RaiseException):
            with conn.transaction():
                cur.execute('DELETE FROM work_embedding WHERE model = %s AND work_id IN '
                            '(SELECT work_id FROM work WHERE run_id = %s)', (result['model'], normal))
        with pytest.raises(psycopg.errors.RaiseException):
            with conn.transaction():
                cur.execute('UPDATE work_embedding SET char_count = 0 WHERE model = %s '
                            'AND work_id IN (SELECT work_id FROM work WHERE run_id = %s)',
                            (result['model'], normal))


def test_partial_collection_cannot_publish_empty_normalization(seeded):
    from psycopg.types.json import Jsonb
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO collection_batch (mission_id, query_version_id, "
                    "content_sha256, connector_version, fetched_at, status, coverage) "
                    "VALUES (%s, %s, %s, 'test', now(), 'partial', %s)",
                    (MISSION, f'{MISSION}/v1', 'partial-fixture', Jsonb({})))
    with pytest.raises(ValueError, match='нет завершённого'):
        normalize.normalize(MISSION, verbose=False)
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute('SELECT count(*) FROM analysis_run WHERE mission_id = %s', (MISSION,))
        assert cur.fetchone()[0] == 0


@pytest.mark.parametrize('pinned_batch', [False, True])
def test_v04_candidate_uses_pinned_quality_and_never_future_detection_date(seeded, monkeypatch, pinned_batch):
    import numpy as np
    from psycopg.types.json import Jsonb
    from saia.quality import evaluate_mission
    from saia.cluster import write_results
    from saia.candidates import build_candidates, export_cards
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute('UPDATE query_version SET payload = %s WHERE mission_id = %s',
                    (Jsonb({'query': {'terms': ['graph convolutional networks']},
                            'as_of_date': '2018-01-01'}), MISSION))
        if pinned_batch:
            batch_id = sealed_fixture_batch(cur, 'pinned-fixture')
    normalized = normalize.normalize(MISSION, verbose=False)['run_id']
    generation = evaluate_mission(MISSION)['quality_generation_id']
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute('SELECT work_id FROM work WHERE run_id = %s', (normalized,))
        work_id = cur.fetchone()[0]
        cur.execute('INSERT INTO work_embedding (work_id, model, dim, embedding, text_source, char_count) '
                    "VALUES (%s, 'synthetic-fixture', 2, '[1,0]'::vector, 'title_abstract', 50)", (work_id,))
    topic = {'terms': ['synthetic fixture'], 'anchor': np.asarray([1., 0.]),
             'anchor_window': '2016', 'first_window': '2016', 'last_window': '2017', 'alive': True}
    write_results(MISSION, 'year', {0: topic},
                  [{'topic_key': 0, 'window': '2016', 'doc_count': 1, 'popularity': 1., 'slope': 1.},
                   {'topic_key': 0, 'window': '2017', 'doc_count': 0, 'popularity': .5, 'slope': -.5}],
                  [], [(0, work_id, '2016')], methodology.load_default(), 'synthetic-fixture',
                  normalized, 'seed', 2, 'graph', {}, generation)
    # Новое текущee legacy-решение не изменяет вход уже построенного кластера.
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE work_quality SET decision = 'exclude' WHERE work_id = %s", (work_id,))
    result = build_candidates(MISSION)
    payload = export_cards(MISSION)
    assert result['run_id'] == payload['score_run_id']
    assert len(payload['cards']) == 1
    assert [r['kind'] for r in payload['provenance']] == ['score', 'cluster', 'normalize']
    assert payload['provenance'][1]['notes']['quality_generation_id'] == generation
    assert payload['provenance'][0]['methodology_version'] == '0.4'
    assert export_cards(MISSION, result['run_id'])['score_run_id'] == result['run_id']
    with pytest.raises(ValueError, match='другой миссии'):
        export_cards('another-mission', result['run_id'])
    history = runs.history(MISSION)
    assert history['runs'][0]['run_id'] == result['run_id']
    assert history['runs'][0]['quality_generation_id'] == generation
    card = payload['cards'][0]
    assert isinstance(card['topic_id'], int)
    assert len(card['composition_sha256']) == 64
    assert card['status'] != 'forming'
    assert card['signal_detected'] is None
    assert card['metrics']['observed']['assessed_at'] == '2018-01-01'
    assert card['metrics']['observed']['primary_sources_verified'] is None
    assert card['metrics']['observed']['title_proxy_is_scientific_evidence'] is False
    assert card['metrics']['observed']['paper_title_diagnostics']['version'] == 'title-evidence-diagnostic-v2'
    assert card['evidence'][0]['title_diagnostic']['version'] == 'title-evidence-diagnostic-v2'
    assert card['evidence'][0]['openalex_record_types'] == ['article']
    assert next(g for g in card['gates'] if g['gate'] == 'G6_primary_sources')['passed'] is None
    assert card['metrics']['publication_series']['points'][-1]['end'] <= '2018-01-01'
    assert card['metrics']['publication_series']['coverage_comparable'] is None
    assert all(point['coverage_comparable'] is None
               for point in card['metrics']['publication_series']['points'])
    assert card['evidence'][0]['published_at'] == '2016-09-09'
    from saia import benchmark
    monkeypatch.setattr(benchmark, 'load_benchmark_config', lambda _: {
        'name': 'fixture-topic-recovery-not-signal', 'target_arxiv_ids': ['1609.02907'],
        'acceptance': {'min_target_works_same_topic': 1, 'min_eligible_target_recall': 1,
                       'min_target_share_in_dominant_topic': 0.1, 'min_peer_topics': 1,
                       'max_forming_single_window_topics': 0,
                       'max_forming_above_maturity_percentile': 0}})
    # Текущий корпус уже другой, но запрос старого score обязан использовать
    # прежние normalize/cluster/quality, а не смешанную текущую цепочку.
    normalize.normalize(MISSION, verbose=False)
    # Новый пакет с иным набором сырья не подменяет паспорт старого score.
    with db.connect() as conn, conn.cursor() as cur:
        latest_batch = sealed_fixture_batch(cur, 'latest-fixture', ['arxiv'])
    report = benchmark.build_benchmark(MISSION, result['run_id'])
    assert report['runs']['normalize'] == normalized
    assert report['runs']['quality_generation_id'] == generation
    assert report['corpus']['eligible_works_in_period'] == 1
    assert report['corpus']['raw_count_provenance'] == ('pinned_batch' if pinned_batch else 'unknown_legacy_batch')
    assert report['corpus']['raw_records'] == ({'arxiv': 1, 'openalex': 1} if pinned_batch else {})
    assert report['target_topic_recovery_passed'] is True
    assert report['target_detection_passed'] is False
    assert report['overall_benchmark_passed'] is False
    assert 'Поколение качества закреплено' in benchmark.benchmark_markdown(report)
    assert report['controls']['repeatable_partition']['passed'] is None
    assert 'не проверено | нет сопоставимого повторного прогона' in benchmark.benchmark_markdown(report)


def test_controlled_query_is_immutable_idempotent_and_does_not_relabel_old_raw(seeded, monkeypatch):
    from psycopg.types.json import Jsonb
    from saia.quality import evaluate_mission
    from saia import query_expansion
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute('UPDATE query_version SET payload = %s WHERE mission_id = %s',
                    (Jsonb({'query': {'terms': ['graph convolutional networks']},
                            'as_of_date': '2018-01-01'}), MISSION))
        cur.execute("SELECT snapshot_id FROM source_snapshot WHERE mission_id = %s AND source = 'arxiv'", (MISSION,))
        snapshot = cur.fetchone()[0]
        cur.execute('INSERT INTO raw_record (snapshot_id, source, source_record_id, payload, content_sha256) '
                    "VALUES (%s, 'arxiv', '1601.00001', %s, 'fixture-second')",
                    (snapshot, Jsonb(arxiv_payload('1601.00001',
                                                  'Graph Convolutional Networks for Training Data', '2016-01-02'))))
    root = normalize.normalize(MISSION, verbose=False)['run_id']
    generation = evaluate_mission(MISSION)['quality_generation_id']
    p = query_expansion.create_proposal(MISSION, 'graph convolutional networks', generation)
    stale = query_expansion.create_proposal(MISSION, 'graph convolutional networks', generation)
    term = next(t for t in p['suggestions'] if t['phrase'] == 'convolutional networks')
    assert term['contexts'][0]['sources']
    with pytest.raises(ValueError, match='нет в сохранённом'):
        query_expansion.approve(p['proposal_id'], ['invented-term'], [], 'test')
    with monkeypatch.context() as patch:
        def changed_policy():
            raise AssertionError('Approval must use the frozen proposal policy')
        patch.setattr(query_expansion, 'policy', changed_policy)
        result = query_expansion.approve(p['proposal_id'], [term['suggestion_id']], ['clinical study'], 'test')
        assert query_expansion.approve(p['proposal_id'], [term['suggestion_id']], ['clinical study'], 'test')['already_saved']
    assert result['query_version_id'] == f'{MISSION}/v2'
    assert query_expansion.approve(p['proposal_id'], [term['suggestion_id']], ['clinical study'], 'test')['already_saved']
    with pytest.raises(query_expansion.QueryConflict):
        query_expansion.approve(p['proposal_id'], [term['suggestion_id']], [], 'test')
    assert query_expansion.saved_plan(result['query_version_id'])['exclusions'] == ['clinical study']
    assert query_expansion.read_approved(result['query_version_id'])['decision']['reviewed_by'] == 'test'
    assert query_expansion.get_proposal(p['proposal_id'])['decision']['query_version_id'] == result['query_version_id']
    with db.connect() as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.errors.RaiseException):
            with conn.transaction():
                cur.execute("UPDATE query_version SET terms = ARRAY['changed'] WHERE query_version_id = %s",
                            (result['query_version_id'],))
        with pytest.raises(psycopg.errors.RaiseException):
            with conn.transaction():
                cur.execute("UPDATE query_expansion_proposal SET payload = '{}' WHERE proposal_id = %s", (p['proposal_id'],))
        cur.execute('SELECT query_version_id FROM analysis_run WHERE run_id = %s', (root,))
        assert cur.fetchone()[0] == f'{MISSION}/v1'
    # Новая версия поиска не имеет своих исходных снимков; прежнее сырьё
    # остаётся входом старого запроса даже при явном повторе нормализации.
    repeated = normalize.normalize(MISSION, verbose=False)['run_id']
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute('SELECT query_version_id FROM analysis_run WHERE run_id = %s', (repeated,))
        assert cur.fetchone()[0] == f'{MISSION}/v1'
        import uuid
        with pytest.raises(psycopg.errors.RaiseException, match='согласованные'):
            with conn.transaction():
                cur.execute('INSERT INTO query_expansion_proposal (proposal_id, mission_id, base_query_version_id, '
                            'normalize_run_id, quality_generation_id, payload, content_sha256) '
                            'VALUES (%s, %s, %s, %s, %s, %s, %s)',
                            (str(uuid.uuid4()), MISSION, f'{MISSION}/v1', repeated, generation, Jsonb(p), 'invalid-fixture'))
    with pytest.raises(query_expansion.QueryConflict, match='новая версия'):
        query_expansion.approve(stale['proposal_id'], [term['suggestion_id']], [], 'test')
    # Тот же широкий индекс может обслуживать последовательные запросы:
    # его source-query и логический parent-query не обязаны совпадать.
    newer = query_expansion.create_proposal(MISSION, 'graph convolutional networks', generation)
    assert newer['base_query_version_id'] == f'{MISSION}/v2'
    assert newer['provenance']['query_version_id'] == f'{MISSION}/v1'
    assert query_expansion.approve(newer['proposal_id'], [term['suggestion_id']], [], 'test')['query_version_id'] == f'{MISSION}/v3'


def test_durable_discovery_job_is_idempotent_restartable_and_not_a_signal(seeded):
    import uuid
    from psycopg.types.json import Jsonb
    from saia import jobs, query_expansion
    from saia.quality import evaluate_mission

    with db.connect() as conn, conn.cursor() as cur:
        cur.execute('UPDATE query_version SET payload=%s WHERE mission_id=%s',
                    (Jsonb({'query': {'terms': ['graph convolutional networks']},
                            'as_of_date': '2018-01-01'}), MISSION))
        cur.execute("SELECT snapshot_id FROM source_snapshot WHERE mission_id=%s AND source='arxiv'",
                    (MISSION,))
        snapshot = cur.fetchone()[0]
        cur.execute('INSERT INTO raw_record (snapshot_id,source,source_record_id,payload,content_sha256) '
                    "VALUES (%s,'arxiv','1601.00001',%s,'durable-job-second')",
                    (snapshot, Jsonb(arxiv_payload(
                        '1601.00001', 'Graph Convolutional Networks for Training Data', '2016-01-02'))))
    root = normalize.normalize(MISSION, verbose=False)['run_id']
    generation = evaluate_mission(MISSION)['quality_generation_id']
    proposal = query_expansion.create_proposal(
        MISSION, 'graph convolutional networks', generation)
    term = next(item for item in proposal['suggestions']
                if item['phrase'] == 'convolutional networks')
    approved = query_expansion.approve(
        proposal['proposal_id'], [term['suggestion_id']], [], 'test')
    assert approved['query_version_id'] == MISSION + '/v2'

    operation_id = str(uuid.uuid4())
    queued = jobs.enqueue_controlled_discovery(
        MISSION, MISSION + '/v2', 'test analyst', 7, operation_id)
    repeated = jobs.enqueue_controlled_discovery(
        MISSION, MISSION + '/v2', 'test analyst', 7, operation_id)
    assert repeated['job_id'] == queued['job_id']
    with pytest.raises(jobs.JobConflict):
        jobs.enqueue_controlled_discovery(
            MISSION, MISSION + '/v2', 'test analyst', 8, operation_id)

    claimed = jobs.claim('worker-a', 30)
    assert claimed['job_id'] == queued['job_id'] and claimed['attempt_count'] == 1
    seen = []
    def fake_collector(*args, **kwargs):
        seen.append((args, kwargs))
        return {'works': [{'title': 'candidate only'}], 'errors': {}}
    finished = jobs.execute(queued['job_id'], 'worker-a', fake_collector)
    assert finished['status'] == 'succeeded'
    assert finished['result_completeness'] == 'complete'
    assert finished['result_role'] == 'corpus_candidate_not_signals'
    assert finished['result']['result_role'] == 'corpus_candidate_not_signals'
    assert seen[0][0][0] == 'graph convolutional networks'
    with db.connect() as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.errors.RaiseException, match='terminal'):
            with conn.transaction():
                cur.execute("UPDATE analysis_job SET status='queued' WHERE job_id=%s", (queued['job_id'],))

    cancelled = jobs.enqueue_controlled_discovery(
        MISSION, MISSION + '/v2', 'test analyst', 7, str(uuid.uuid4()))
    cancelled = jobs.cancel(cancelled['job_id'], 'test analyst')
    assert cancelled['status'] == 'cancelled'
    retried = jobs.retry(cancelled['job_id'], 'test analyst', str(uuid.uuid4()))
    assert retried['status'] == 'queued' and retried['retry_of_job_id'] == cancelled['job_id']
    packet = jobs.read(retried['job_id'])
    assert [event['event_type'] for event in packet['events']] == ['queued', 'retry_created']
    first_claim = jobs.claim('worker-before-restart', 30)
    assert first_claim['job_id'] == retried['job_id'] and first_claim['attempt_count'] == 1
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE analysis_job SET lease_expires_at=now()-interval '1 second' WHERE job_id=%s",
                    (retried['job_id'],))
    recovered = jobs.claim('worker-after-restart', 30)
    assert recovered['job_id'] == retried['job_id'] and recovered['attempt_count'] == 2
    assert 'lease_expired_requeued' in [
        event['event_type'] for event in jobs.read(retried['job_id'])['events']]


def test_durable_full_analysis_derives_source_profile_and_returns_review_queue(seeded):
    import uuid
    from psycopg.types.json import Jsonb
    from saia import jobs, query_expansion
    from saia.full_analysis import RESULT_ROLE
    from saia.quality import evaluate_mission

    with db.connect() as conn, conn.cursor() as cur:
        cur.execute('UPDATE query_version SET payload=%s WHERE mission_id=%s',
                    (Jsonb({'query': {'terms': ['graph convolutional networks']},
                            'as_of_date': '2018-01-01'}), MISSION))
        cur.execute("SELECT snapshot_id FROM source_snapshot WHERE mission_id=%s AND source='arxiv'",
                    (MISSION,))
        snapshot = cur.fetchone()[0]
        cur.execute('INSERT INTO raw_record (snapshot_id,source,source_record_id,payload,content_sha256) '
                    "VALUES (%s,'arxiv','1601.00001',%s,'full-job-second')",
                    (snapshot, Jsonb(arxiv_payload(
                        '1601.00001', 'Graph Convolutional Networks for Training Data',
                        '2016-01-02'))))
    root = normalize.normalize(MISSION, verbose=False)['run_id']
    generation = evaluate_mission(MISSION, root)['quality_generation_id']
    proposal = query_expansion.create_proposal(
        MISSION, 'graph convolutional networks', generation)
    term = next(item for item in proposal['suggestions']
                if item['phrase'] == 'convolutional networks')
    approved = query_expansion.approve(
        proposal['proposal_id'], [term['suggestion_id']], [], 'test analyst')

    operation_id = str(uuid.uuid4())
    queued = jobs.enqueue_full_analysis(
        MISSION, approved['query_version_id'], 'test analyst',
        max_records=5000, top_n=15, acknowledge_source_scope=True,
        operation_id=operation_id,
    )
    repeated = jobs.enqueue_full_analysis(
        MISSION, approved['query_version_id'], 'test analyst',
        max_records=5000, top_n=15, acknowledge_source_scope=True,
        operation_id=operation_id,
    )
    assert repeated['job_id'] == queued['job_id']
    assert queued['job_kind'] == 'controlled_full_analysis'
    assert queued['result_role'] == RESULT_ROLE
    assert queued['query_version_id'] != approved['query_version_id']
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute('SELECT expansion_source,payload FROM query_version WHERE query_version_id=%s',
                    (queued['query_version_id'],))
        source, profile = cur.fetchone()
    assert source == 'controlled_source_profile'
    assert profile['sources'] == ['arxiv']
    assert profile['collection_profile']['base_query_version_id'] == approved['query_version_id']
    assert profile['collection_profile']['openalex_observation']['status'] == 'not_collected_by_full_job'
    assert profile['collection_profile']['limitations'][0].startswith('Полный сбор')

    claimed = jobs.claim('full-worker', 30)
    assert claimed['job_id'] == queued['job_id']
    def fake_runner(payload, *, progress, cancel_check):
        assert cancel_check() is False
        assert payload['source_scope'] == ['arxiv']
        progress('collect', 'started', {'source_scope': ['arxiv']})
        progress('collect', 'reused', {'selected_records': 42})
        progress('triage', 'succeeded', {'shown': 15})
        return {
            'result_role': RESULT_ROLE,
            'runs': {'score': 99},
            'cards': {'shown': 15},
            'limitations': ['not a market forecast'],
        }
    finished = jobs.execute(
        queued['job_id'], 'full-worker', full_runner=fake_runner,
        lease_seconds=30,
    )
    assert finished['status'] == 'succeeded'
    assert finished['result']['runs']['score'] == 99
    assert finished['result_role'] == RESULT_ROLE
    assert 'автоматически выявленные кандидаты' in finished['interpretation']
    assert 'без обязательной экспертизы' in finished['interpretation']
    event_types = [item['event_type'] for item in jobs.read(queued['job_id'])['events']]
    assert event_types == [
        'queued', 'claimed', 'stage_started', 'stage_reused',
        'stage_succeeded', 'succeeded',
    ]


def test_mixed_legacy_query_snapshots_require_explicit_batch(seeded):
    from psycopg.types.json import Jsonb
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute('INSERT INTO query_version (query_version_id, mission_id, version, terms, payload, content_sha256) '
                    'VALUES (%s, %s, 2, %s, %s, %s)', (f'{MISSION}/v2', MISSION, ['other'], Jsonb({}), 'query-two'))
        cur.execute('INSERT INTO source_snapshot (mission_id, query_version_id, source, connector_version, '
                    "file_name, file_sha256, fetched_at) VALUES (%s, %s, 'arxiv', 'test', 'mixed-fixture', 'two', now())",
                    (MISSION, f'{MISSION}/v2'))
    with pytest.raises(ValueError, match='разным запросам'):
        normalize.normalize(MISSION, verbose=False)


def test_canonical_date_drops_to_earliest_version(seeded):
    """Дефект P0-2: препринт не должен получать журнальную дату.

    OpenAlex обходится первым и приносит декабрь 2017. Препринт arXiv
    сентября 2016 приходит следом и должен опустить дату работы, иначе
    система теряет пятнадцать месяцев форы — ровно то, ради чего она есть.
    """
    normalize.normalize(MISSION, verbose=False)

    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT effective_date, publication_date FROM work_current "
            "WHERE mission_id = %s",
            (MISSION,),
        )
        rows = cur.fetchall()

    assert len(rows) == 1, f"версии не склеились: {rows}"
    effective, published = rows[0]
    assert str(effective) == "2016-09-09", (
        f"работа датирована {effective}, а препринт вышел 2016-09-09"
    )
    assert str(published) == "2016-09-09"


def test_completed_normalized_text_cannot_be_rewritten(seeded):
    normal = normalize.normalize(MISSION, verbose=False)['run_id']
    with db.connect() as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.errors.RaiseException):
            with conn.transaction():
                cur.execute("UPDATE work SET canonical_title = 'rewritten' WHERE run_id = %s", (normal,))


def test_completed_score_candidate_and_reviews_are_append_only(seeded):
    from psycopg.types.json import Jsonb
    from saia.candidates import composition_sha256
    import uuid
    normalize.normalize(MISSION, verbose=False)
    with db.connect() as conn, conn.cursor() as cur:
        score = runs.start_run(cur, MISSION, 'score', methodology.load_default())
        cur.execute(
            "INSERT INTO topic (mission_id,run_id,label,anchor_embedding,anchor_window,first_window,last_window) "
            "VALUES (%s,%s,'review fixture','[1,0]','2017','2017','2017') RETURNING topic_id",
            (MISSION, score),
        )
        topic = cur.fetchone()[0]
        cur.execute('SELECT work_id FROM work WHERE mission_id=%s ORDER BY work_id LIMIT 1',
                    (MISSION,))
        work_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO topic_membership (topic_id,work_id,window_key,probability) "
            "VALUES (%s,%s,'2017',1)", (topic, work_id))
        composition = composition_sha256([work_id])
        cur.execute(
            "INSERT INTO signal_candidate "
            "(run_id,topic_id,status,label,label_basis,evidence_confidence,metrics,gates,limitations) "
            "VALUES (%s,%s,'watch','review fixture','fixture',0,%s,%s,'{}') RETURNING candidate_id",
            (score, topic, Jsonb({}), Jsonb([])),
        )
        candidate = cur.fetchone()[0]
        runs.finish_run(cur, score)
        other_score = runs.start_run(cur, MISSION, 'score', methodology.load_default())
        runs.finish_run(cur, other_score)
    with db.connect() as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.errors.RaiseException, match='immutable'):
            with conn.transaction():
                cur.execute("UPDATE signal_candidate SET label='changed' WHERE candidate_id=%s", (candidate,))
        review = str(uuid.uuid4())
        cur.execute(
            "INSERT INTO score_candidate_review "
            "(review_id,score_run_id,candidate_id,composition_sha256,candidate_content_sha256,"
            "reviewed_by,decision,rationale,sources) "
            "VALUES (%s,%s,%s,%s,%s,'Synthetic analyst','needs_review',%s,%s)",
            (review, score, candidate, composition, 'a' * 64,
             'Synthetic fixture: independent follow-up is required.', Jsonb([])),
        )
        with pytest.raises(psycopg.errors.RaiseException, match='append-only'):
            with conn.transaction():
                cur.execute('DELETE FROM score_candidate_review WHERE review_id=%s', (review,))
        with pytest.raises(psycopg.errors.RaiseException, match='exact completed score composition'):
            with conn.transaction():
                cur.execute(
                    "INSERT INTO score_candidate_review "
                    "(review_id,score_run_id,candidate_id,composition_sha256,"
                    "candidate_content_sha256,reviewed_by,decision,rationale,sources) "
                    "VALUES (%s,%s,%s,%s,%s,'Synthetic analyst','needs_review',%s,%s)",
                    (str(uuid.uuid4()), other_score, candidate, composition, 'b' * 64,
                     'Synthetic fixture: wrong score run must fail.', Jsonb([])),
                )


def test_second_run_does_not_destroy_the_first(seeded):
    """Дефект P0-3: прошлый ответ должен оставаться доказуемым."""
    first = normalize.normalize(MISSION, verbose=False)["run_id"]
    second = normalize.normalize(MISSION, verbose=False)["run_id"]
    assert first != second

    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM work WHERE run_id = %s", (first,))
        assert cur.fetchone()[0] > 0, "первое поколение стёрто вторым прогоном"

        cur.execute("SELECT run_id FROM work_current WHERE mission_id = %s", (MISSION,))
        current = {row[0] for row in cur.fetchall()}
        assert current == {second}, "текущим должно быть последнее поколение"


def test_unfinished_run_does_not_become_current(seeded):
    """Упавший прогон не подменяет собой предыдущий ответ."""
    done = normalize.normalize(MISSION, verbose=False)["run_id"]

    with db.connect() as conn, conn.cursor() as cur:
        broken = runs.start_run(cur, MISSION, "normalize",
                                methodology.load_default())
        runs.finish_run(cur, broken, "failed", "тестовый сбой")
        conn.commit()
        assert runs.current_run_id(cur, MISSION, "normalize") == done


def test_hybrid_snapshot_pins_inputs_and_survives_new_corpus(seeded):
    from psycopg.types.json import Jsonb
    from saia import hybrid
    from saia.quality import evaluate_mission
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute('UPDATE query_version SET payload = %s WHERE mission_id = %s',
                    (Jsonb({'query': {'terms': ['graph convolutional networks']},
                            'as_of_date': '2018-01-01'}), MISSION))
    root = normalize.normalize(MISSION, verbose=False)['run_id']
    generation = evaluate_mission(MISSION)['quality_generation_id']
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT work_id FROM quality_snapshot WHERE generation_id = %s AND decision = 'include'",
                    (generation,))
        works = [r[0] for r in cur.fetchall()]
        assert works
        for work in works:
            cur.execute('INSERT INTO work_embedding (work_id, model, dim, embedding, text_source, char_count) '
                        "VALUES (%s, 'hybrid-test', 2, '[1,0]', 'title_only', 10)", (work,))
        cluster = runs.start_run(cur, MISSION, 'cluster', methodology.load_default(),
                                 embedding_model='hybrid-test', upstream_run_id=root,
                                 window_step='year', notes={'quality_generation_id': generation})
        cur.execute('INSERT INTO topic (mission_id, run_id, label, anchor_embedding, '
                    "anchor_window, first_window, last_window) VALUES (%s, %s, 'fixture', '[1,0]', "
                    "'2016', '2016', '2016') RETURNING topic_id", (MISSION, cluster))
        topic = cur.fetchone()[0]
        for work in works:
            cur.execute("INSERT INTO topic_membership (topic_id, work_id, window_key) VALUES (%s,%s,'2016')",
                        (topic, work))
        runs.finish_run(cur, cluster)
    result = hybrid.analyze(MISSION, cluster)
    assert result['provenance']['normalize_run_id'] == root
    assert result['counts']['semantic_lines'] == 1
    assert hybrid.read(result['snapshot_id']) == result
    from saia import composition_assessment
    composed = composition_assessment.analyze(result['snapshot_id'])
    assert len(composed['candidates']) == len(result['candidates'])
    assert composed['snapshot_content_sha256'] == hybrid.digest(result)
    assert all(c['assessment']['status'] != 'forming' for c in composed['candidates'])
    assert all(c['observed']['primary_sources'] is None for c in composed['candidates'])
    assert hybrid.read(result['snapshot_id']) == result
    from saia import assessment_store, portfolio
    saved = assessment_store.save(composed)
    again = assessment_store.save(composed)
    assert again['assessment_id'] == saved['assessment_id']
    assert again['created_at'] == saved['created_at'] and again['replayed']
    stored = assessment_store.read(saved['assessment_id'])
    assert stored['report'] == composed
    assert assessment_store.history(result['snapshot_id'])['total'] == 1
    viewed = portfolio.project(result, stored)
    assert viewed['saved_assessment']['assessment_id'] == saved['assessment_id']
    assert hybrid.read(result['snapshot_id']) == result
    with db.connect() as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.errors.RaiseException):
            with conn.transaction():
                cur.execute("UPDATE composition_assessment_snapshot SET payload='{}' WHERE assessment_id=%s",
                            (saved['assessment_id'],))
        with pytest.raises(psycopg.errors.RaiseException):
            with conn.transaction():
                cur.execute('DELETE FROM composition_assessment_snapshot WHERE assessment_id=%s',
                            (saved['assessment_id'],))
    with db.connect() as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.errors.RaiseException):
            with conn.transaction():
                cur.execute("UPDATE hybrid_snapshot SET payload = '{}' WHERE snapshot_id = %s", (result['snapshot_id'],))
    from saia import expert
    candidate_id = result['candidates'][0]['candidate_id']
    sid = result['snapshot_id']
    assert expert.history(sid, candidate_id)['total'] == 0
    from fastapi.testclient import TestClient
    from saia.api import app
    client = TestClient(app)
    response = client.post(f'/hybrid/{sid}/candidates/{candidate_id}/reviews', json={
        'decision': 'needs_review', 'reviewed_by': 'Synthetic test analyst',
        'rationale': 'Synthetic fixture: independent follow-up is still required.', 'sources': []})
    assert response.status_code == 200
    first = response.json()
    repeat = client.post(f'/hybrid/{sid}/candidates/{candidate_id}/reviews', json={
        'operation_id': first['review_id'], 'decision': 'needs_review',
        'reviewed_by': 'Synthetic test analyst',
        'rationale': 'Synthetic fixture: independent follow-up is still required.', 'sources': []})
    assert repeat.status_code == 200 and repeat.json()['replayed'] is True
    assert repeat.json()['created_at'] == first['created_at']
    changed = client.post(f'/hybrid/{sid}/candidates/{candidate_id}/reviews', json={
        'operation_id': first['review_id'], 'decision': 'noise', 'reviewed_by': 'Synthetic test analyst',
        'rationale': 'Synthetic fixture: changed content must not reuse the key.', 'sources': []})
    assert changed.status_code == 422
    second = expert.record(sid, candidate_id, 'insufficient_evidence', 'Synthetic test analyst',
                           'Synthetic fixture: identity and coverage remain unknown.', ['https://arxiv.org/abs/1609.02907'])
    assert first['review_id'] != second['review_id']
    opinions = expert.history(sid, candidate_id)
    assert client.get(f'/hybrid/{sid}/candidates/{candidate_id}/reviews').json() == opinions
    assert opinions['total'] == 2 and opinions['returned'] == 2
    assert opinions['identity'] == 'self_declared_not_authenticated'
    assert hybrid.read(sid) == result
    with pytest.raises(ValueError, match='отсутствует'):
        expert.record(sid, 'foreign-candidate', 'needs_review', 'Synthetic test analyst',
                      'Synthetic fixture: this candidate belongs elsewhere.', [])
    with db.connect() as conn, conn.cursor() as cur:
        for sql in ('UPDATE expert_review SET rationale = rationale WHERE review_id = %s',
                    'DELETE FROM expert_review WHERE review_id = %s'):
            with pytest.raises(psycopg.errors.RaiseException, match='append-only'):
                with conn.transaction(): cur.execute(sql, (first['review_id'],))
        import uuid
        with pytest.raises(psycopg.errors.RaiseException, match='exact frozen composition'):
            with conn.transaction():
                cur.execute('INSERT INTO expert_review (review_id,snapshot_id,candidate_id,composition_sha256,'
                            'snapshot_content_sha256,reviewed_by,decision,rationale,sources) '
                            'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                            (str(uuid.uuid4()), sid, candidate_id, first['composition_sha256'],
                             'foreign-hash', 'Synthetic test analyst',
                             'needs_review', 'Synthetic fixture: wrong snapshot hash.', Jsonb([])))
    later_root = normalize.normalize(MISSION, verbose=False)['run_id']
    assert later_root != root
    assert hybrid.read(result['snapshot_id']) == result
    assert hybrid.analyze(MISSION, cluster)['input_text_hash'] == result['input_text_hash']
    other = hybrid.analyze(MISSION, cluster)
    carried = expert.history(other['snapshot_id'], candidate_id)
    assert carried['total'] == 2
    assert carried['history_scope'] == 'exact_publication_composition_across_snapshots'
    assert carried['composition_sha256'] == first['composition_sha256']
    with pytest.raises(ValueError, match='другого мнения'):
        expert.record(other['snapshot_id'], candidate_id, 'needs_review', 'Synthetic test analyst',
                      'Synthetic fixture: independent follow-up is still required.', [], first['review_id'])
    from concurrent.futures import ThreadPoolExecutor
    import uuid
    key = str(uuid.uuid4())
    def submit():
        return expert.record(sid, candidate_id, 'needs_review', 'Synthetic concurrency analyst',
                             'Synthetic fixture: concurrent retries are one operation.', [], key)
    with ThreadPoolExecutor(max_workers=2) as pool:
        concurrent = list(pool.map(lambda _: submit(), range(2)))
    assert {r['replayed'] for r in concurrent} == {False, True}
    assert concurrent[0]['review_id'] == concurrent[1]['review_id']
    assert concurrent[0]['created_at'] == concurrent[1]['created_at']
    assert expert.history(sid, candidate_id)['total'] == 3
    with db.connect() as conn, conn.cursor() as cur:
        import uuid
        with pytest.raises(psycopg.errors.RaiseException, match='согласованные'):
            with conn.transaction():
                cur.execute('INSERT INTO hybrid_snapshot (snapshot_id, mission_id, normalize_run_id, '
                            'quality_generation_id, cluster_run_id, payload, content_sha256) '
                            'VALUES (%s,%s,%s,%s,%s,%s,%s)',
                            (str(uuid.uuid4()), MISSION, later_root, generation, cluster, Jsonb(result), 'broken'))


def test_external_evidence_storage_is_append_only_and_idempotent(seeded):
    import json
    import uuid
    from psycopg.types.json import Jsonb
    from saia import external_evidence_store
    from saia.news_evidence import NewsQuery, parse_response, request_url

    query = NewsQuery(MISSION, 'agentic AI', '2026-09-14', '2026-09-21',
                      '2026-09-21', 10)
    payload = parse_response(
        query,
        json.dumps({'articles': [{
            'url': 'https://example.org/agentic-ai', 'title': 'Agentic AI update',
            'seendate': '20260920T120000Z', 'domain': 'example.org',
            'language': 'English', 'sourcecountry': 'United States',
        }]}).encode(),
        '2026-09-21T12:00:00+00:00', request_url(query),
    )
    operation = str(uuid.uuid4())
    first = external_evidence_store.record(payload, operation)
    replay = external_evidence_store.record(payload, operation)
    deduplicated = external_evidence_store.record(payload, str(uuid.uuid4()))
    assert first['observation_id'] == replay['observation_id'] == deduplicated['observation_id']
    assert first['replayed'] is False and replay['replayed'] is True
    assert deduplicated['deduplicated_by_content'] is True
    history = external_evidence_store.history(MISSION, 'gdelt_doc_2_0')
    assert history['total'] == 1 and history['scientific_score_modified'] is False
    stored = external_evidence_store.read(operation)
    assert stored['payload'] == payload
    assert stored['report_payload_sha256'] == payload['report_payload_sha256']

    with db.connect() as conn, conn.cursor() as cur:
        for sql in (
            'UPDATE external_evidence_observation SET status=status WHERE observation_id=%s',
            'DELETE FROM external_evidence_observation WHERE observation_id=%s',
        ):
            with pytest.raises(psycopg.errors.RaiseException, match='append-only'):
                with conn.transaction():
                    cur.execute(sql, (operation,))
        bad = dict(payload)
        bad['scientific_score_modified'] = True
        with pytest.raises(psycopg.errors.RaiseException, match='guarded payload'):
            with conn.transaction():
                cur.execute(
                    'INSERT INTO external_evidence_observation '
                    '(observation_id,source,role,topic_id,status,report_payload_sha256,payload) '
                    'VALUES (%s,%s,%s,%s,%s,%s,%s)',
                    (str(uuid.uuid4()), payload['source'], payload['role'], payload['topic_id'],
                     payload['status'], payload['report_payload_sha256'], Jsonb(bad)),
                )
        mismatched = dict(payload)
        mismatched['role'] = 'patent_landscape_only'
        from saia.hybrid import digest
        canonical = dict(mismatched)
        canonical.pop('report_payload_sha256')
        mismatched['report_payload_sha256'] = digest(canonical)
        with pytest.raises(psycopg.errors.RaiseException, match='source and role do not match'):
            with conn.transaction():
                cur.execute(
                    'INSERT INTO external_evidence_observation '
                    '(observation_id,source,role,topic_id,status,report_payload_sha256,payload) '
                    'VALUES (%s,%s,%s,%s,%s,%s,%s)',
                    (str(uuid.uuid4()), mismatched['source'], mismatched['role'],
                     mismatched['topic_id'], mismatched['status'],
                     mismatched['report_payload_sha256'], Jsonb(mismatched)),
                )


def _source_ui_candidate_fixture():
    """One disposable saved publication and candidate; no scientific verdict."""
    from psycopg.types.json import Jsonb
    from saia.candidates import export_cards
    normalize.normalize(MISSION, verbose=False)
    with db.connect() as conn, conn.cursor() as cur:
        score = runs.start_run(cur, MISSION, 'score', methodology.load_default())
        cur.execute("INSERT INTO topic (mission_id,run_id,label,anchor_embedding,anchor_window,first_window,last_window) "
                    "VALUES (%s,%s,'Graph convolutional','[1,0]','2017','2017','2017') RETURNING topic_id", (MISSION, score))
        topic = cur.fetchone()[0]
        cur.execute('SELECT work_id FROM work WHERE mission_id=%s ORDER BY work_id LIMIT 1', (MISSION,))
        work = cur.fetchone()[0]
        cur.execute("INSERT INTO topic_membership (topic_id,work_id,window_key,probability) VALUES (%s,%s,'2017',1)", (topic, work))
        cur.execute("INSERT INTO signal_candidate (run_id,topic_id,status,label,label_basis,evidence_confidence,metrics,gates,limitations) "
                    "VALUES (%s,%s,'watch','Graph convolutional','fixture',0,%s,%s,'{}') RETURNING candidate_id",
                    (score, topic, Jsonb({}), Jsonb([])))
        candidate = cur.fetchone()[0]
        cur.execute("INSERT INTO evidence_item (candidate_id,work_id,evidence_rank,role,rationale,sources) "
                    "VALUES (%s,%s,1,'раннее основание',%s,%s)",
                    (candidate, work, 'Основание: Synthetic scientific snippet for a graph method.',
                     Jsonb([{'type': 'arxiv', 'url': 'https://arxiv.org/abs/1609.02907'}])))
        runs.finish_run(cur, score)
    return score, candidate, export_cards(MISSION, score)['cards'][0]


def test_public_only_and_mixed_expert_requests_keep_scores_and_append_only_history(seeded, monkeypatch):
    """Synthetic expert opinions are written only to the isolated test DB."""
    import uuid
    from saia import candidates, expert_requests, public_signal_reviews, scout_public_signals
    from saia.hybrid import digest
    if '_test_' not in db.database_url():
        pytest.skip('Public-opinion fixtures require an explicitly isolated test database')
    score, candidate, card = _source_ui_candidate_fixture()
    before = digest(candidates.export_cards(MISSION, score))
    refs = scout_public_signals.match('БАС', included_phrases=['UAV airframe'])
    monkeypatch.setattr(scout_public_signals, 'for_packet', lambda packet: refs)
    identifier = refs['records'][0]['id']
    dispatch_key, review_key = str(uuid.uuid4()), str(uuid.uuid4())
    dispatch = expert_requests.create(MISSION, score, [], 'Synthetic developer test', 'Synthetic QA queue',
                                     operation_id=dispatch_key, public_signal_ids=[identifier])
    replayed = expert_requests.create(MISSION, score, [], 'Synthetic developer test', 'Synthetic QA queue',
                                     operation_id=dispatch_key, public_signal_ids=[identifier])
    assert replayed['replayed'] is True and dispatch['candidate_ids'] == []
    unrelated_dispatch = expert_requests.create(MISSION, score, [], 'Synthetic second scout', 'Synthetic second QA queue',
                                               public_signal_ids=[identifier])
    args = (dispatch['request_id'], identifier, 'needs_review', 'Synthetic developer test',
            'Синтетическая проверка хранения; не экспертная оценка технологии.', [refs['records'][0]['source_url']])
    review = public_signal_reviews.record(*args, operation_id=review_key)
    assert public_signal_reviews.record(*args, operation_id=review_key)['replayed'] is True
    queue = {row['request_id']: row for row in expert_requests.history()['requests']}
    assert queue[dispatch_key]['status'] == 'reviewed'
    assert queue[unrelated_dispatch['request_id']]['status'] == 'awaiting_expert_review'
    assert public_signal_reviews.history_for_reference(refs['records'][0]['reference_content_sha256'],
                                                      request_id=unrelated_dispatch['request_id'])['reviews'] == []
    mixed = expert_requests.create(MISSION, score, [candidate], 'Synthetic developer test', 'Synthetic QA queue', public_signal_ids=[identifier])
    public_signal_reviews.record(mixed['request_id'], identifier, *args[2:])
    queue = {row['request_id']: row for row in expert_requests.history()['requests']}
    assert queue[mixed['request_id']]['status'] == 'partially_reviewed'
    assert queue[mixed['request_id']]['total_count'] == 2
    assert digest(candidates.export_cards(MISSION, score)) == before
    with pytest.raises(ValueError):
        public_signal_reviews.record(dispatch['request_id'], 'jrc-2024-p122-081', *args[2:])
    with db.connect() as conn, conn.cursor() as cur:
        for statement in ('UPDATE public_signal_review SET rationale=rationale WHERE review_id=%s',
                          'DELETE FROM public_signal_review WHERE review_id=%s'):
            with pytest.raises(psycopg.errors.RaiseException, match='append-only'):
                with conn.transaction():
                    cur.execute(statement, (review['review_id'],))


@pytest.mark.parametrize('source', ['mit_news_rss', 'nasa_news_rss', 'jpl_news_rss', 'nist_news_rss', 'aist_press_rss'])
def test_official_rss_storage_source_role_guard_and_replay(seeded, source):
    from saia.rss_evidence import RSSQuery, parse_response
    from saia import external_evidence_store
    from saia.hybrid import digest
    from psycopg.types.json import Jsonb
    import uuid
    from saia.rss_evidence import FEEDS
    host = FEEDS[source][3]
    path = '/aist_j/press_release/synthetic-robot-control' if source == 'aist_press_rss' else '/robot-control'
    payload = f'<rss><channel><item><title>Robot control</title><link>https://{host}{path}</link><pubDate>Mon, 28 Sep 2026 09:00:00 +0000</pubDate></item></channel></rss>'.encode()
    value = parse_response(RSSQuery(MISSION, 'robot control', '2026-01-01', '2026-09-28', '2026-09-28', source), payload, '2026-09-28T12:00:00Z')
    first = external_evidence_store.record(value)
    second = external_evidence_store.record(value)
    assert first['observation_id'] == second['observation_id']
    with db.connect() as conn, conn.cursor() as cur:
        bad = {**value, 'role': 'patent_landscape_only'}
        bad.pop('report_payload_sha256')
        bad['report_payload_sha256'] = digest(bad)
        with pytest.raises(psycopg.errors.RaiseException, match='source and role'):
            with conn.transaction():
                cur.execute('INSERT INTO external_evidence_observation (observation_id,source,role,topic_id,status,report_payload_sha256,payload) '
                            'VALUES (%s,%s,%s,%s,%s,%s,%s)',
                            (str(uuid.uuid4()), source, bad['role'], MISSION, bad['status'], bad['report_payload_sha256'], Jsonb(bad)))


@pytest.mark.parametrize('source', ['nsf_awards', 'datacite', 'openaire_projects'])
def test_new_public_metadata_storage_linking_and_db_guards_preserve_science(seeded, source):
    import json
    import uuid
    from saia import candidate_external_links, candidates, external_evidence_store
    from saia.hybrid import digest
    from psycopg.types.json import Jsonb
    from saia import datacite_evidence, nsf_evidence, openaire_project_evidence
    score, candidate, card = _source_ui_candidate_fixture()
    before = digest(candidates.export_cards(MISSION, score))
    specs = {
        'nsf_awards': (nsf_evidence, nsf_evidence.NSFQuery, {'response': {'metadata': {'totalCount': 1}, 'award': [
            {'id': '2600001', 'title': 'Graph convolutional research', 'date': '08/01/2026', 'startDate': '01/01/2027', 'fundsObligatedAmt': '200000'}]}}),
        'datacite': (datacite_evidence, datacite_evidence.DataCiteQuery, {'data': [{'id': '10.1234/synthetic-graph', 'attributes': {
            'doi': '10.1234/synthetic-graph', 'state': 'findable', 'titles': [{'title': 'Graph convolutional dataset'}],
            'types': {'resourceTypeGeneral': 'Dataset'}, 'publicationYear': 2026, 'dates': [{'date': '2026-08-01', 'dateType': 'Issued'}]}}], 'meta': {'total': 1}}),
        'openaire_projects': (openaire_project_evidence, openaire_project_evidence.OpenAIREProjectQuery, {'header': {'numFound': 1}, 'results': [
            {'id': 'corda__he::synthetic-graph', 'code': '101000001', 'title': 'Graph convolutional project', 'startDate': '2026-08-01',
             'fundings': [{'shortName': 'EC', 'name': 'European Commission'}]}]}),
    }
    module, cls, raw = specs[source]
    query = cls(MISSION, 'graph convolutional', '2021-01-01', '2026-09-28', '2026-09-28', 5)
    value = module.parse_response(query, json.dumps(raw).encode(), '2026-09-28T12:00:00Z', module.request_url(query))
    first = external_evidence_store.record(value)
    assert external_evidence_store.record(value)['observation_id'] == first['observation_id']
    assert external_evidence_store.read(first['observation_id'])['payload'] == value
    linked = candidate_external_links.link(MISSION, score, candidate, first['observation_id'], value['observations'][0]['url'],
        'background_only', 'Synthetic developer test', 'Синтетическая проверка хранения, не реальное экспертное доказательство.')
    assert linked['role'] == external_evidence_store.SOURCES[source]
    assert linked['validation_status'] == 'analyst_selected_not_expert_validated'
    assert digest(candidates.export_cards(MISSION, score)) == before
    with db.connect() as conn, conn.cursor() as cur:
        for change in ('role', 'scientific_score_modified', 'missing_is_zero'):
            bad = dict(value)
            bad[change] = 'news_attention_only' if change == 'role' else True
            bad.pop('report_payload_sha256')
            bad['report_payload_sha256'] = digest(bad)
            with pytest.raises(psycopg.errors.RaiseException):
                with conn.transaction():
                    cur.execute('INSERT INTO external_evidence_observation (observation_id,source,role,topic_id,status,report_payload_sha256,payload) '
                                'VALUES (%s,%s,%s,%s,%s,%s,%s)',
                                (str(uuid.uuid4()), source, bad['role'], MISSION, bad['status'], bad['report_payload_sha256'], Jsonb(bad)))
        with pytest.raises(psycopg.errors.RaiseException, match='append-only'):
            with conn.transaction():
                cur.execute('UPDATE external_evidence_observation SET status=status WHERE observation_id=%s', (first['observation_id'],))


@pytest.mark.parametrize('source', ['osti_gov', 'nist_news_rss'])
def test_osti_nist_cached_context_storage_links_and_permissions_preserve_science(seeded, monkeypatch, source):
    import json
    import uuid
    from psycopg.types.json import Jsonb
    from saia import candidate_external_links, candidates, external_evidence_store, osti_evidence, rss_evidence, source_context
    from saia.hybrid import digest
    score, candidate, card = _source_ui_candidate_fixture()
    before = digest(candidates.export_cards(MISSION, score))
    calls = []
    def fake(q, timeout):
        calls.append(q)
        if source == 'osti_gov':
            raw = [{'osti_id': '3399445', 'title': q.query+' synthetic report',
                    'product_type': 'Technical Report', 'publication_date': str(q.end)}]
            return osti_evidence.parse_response(q, json.dumps(raw).encode(), '2026-09-28T12:00:00Z', osti_evidence.request_url(q))
        raw = ('<rss><channel><item><title>'+q.query+' synthetic announcement</title>'
               '<link>https://www.nist.gov/news/synthetic</link><pubDate>'+str(q.end)+'T12:00:00Z</pubDate></item></channel></rss>').encode()
        return rss_evidence.parse_response(q, raw, '2026-09-28T12:00:00Z')
    monkeypatch.setattr(osti_evidence if source == 'osti_gov' else rss_evidence, 'fetch', fake)
    first = source_context.collect(MISSION, score, candidate, sources=[source])
    second = source_context.collect(MISSION, score, candidate, sources=[source])
    saved_report = first['reports'][0]
    assert len(calls) == 1 and second['cache_reused_sources'] == [source]
    assert saved_report['observation_id'] == second['reports'][0]['observation_id']
    saved = external_evidence_store.read(saved_report['observation_id'])
    record = saved['payload']['observations'][0]
    assert record['url'] == saved_report['materials'][0]['url']
    assert saved['payload']['candidate_context_binding'] == first['binding']
    linked = candidate_external_links.link(MISSION, score, candidate, saved['observation_id'], record['url'],
        'background_only', 'Synthetic developer test', 'Синтетическая проверка нового канала, не экспертная валидация сигнала.')
    assert linked['validation_status'] == 'analyst_selected_not_expert_validated'
    assert digest(candidates.export_cards(MISSION, score)) == before
    assert external_evidence_store.read(saved['observation_id'])['payload'] == saved['payload']
    with db.connect() as conn, conn.cursor() as cur:
        bad = {**saved['payload'], 'role': 'research_funding_only'}
        bad.pop('report_payload_sha256')
        bad['report_payload_sha256'] = digest(bad)
        with pytest.raises(psycopg.errors.RaiseException, match='source and role'):
            with conn.transaction():
                cur.execute('INSERT INTO external_evidence_observation (observation_id,source,role,topic_id,status,report_payload_sha256,payload) '
                            'VALUES (%s,%s,%s,%s,%s,%s,%s)',
                            (str(uuid.uuid4()),source,bad['role'],saved['topic_id'],bad['status'],bad['report_payload_sha256'],Jsonb(bad)))
        if source == 'osti_gov':
            for flag in ('model_input_allowed','model_training_allowed','bulk_reuse_approved'):
                bad = {**saved['payload'],flag:True}
                bad.pop('report_payload_sha256')
                bad['report_payload_sha256'] = digest(bad)
                with pytest.raises(psycopg.errors.RaiseException, match='not approved'):
                    with conn.transaction():
                        cur.execute('INSERT INTO external_evidence_observation (observation_id,source,role,topic_id,status,report_payload_sha256,payload) '
                                    'VALUES (%s,%s,%s,%s,%s,%s,%s)',
                                    (str(uuid.uuid4()),source,bad['role'],saved['topic_id'],bad['status'],bad['report_payload_sha256'],Jsonb(bad)))
        with pytest.raises(psycopg.errors.RaiseException, match='append-only'):
            with conn.transaction():
                cur.execute('UPDATE external_evidence_observation SET status=status WHERE observation_id=%s',(saved['observation_id'],))


def test_aist_personal_reader_cache_db_permissions_and_link_do_not_change_science(seeded, monkeypatch):
    import uuid
    from psycopg.types.json import Jsonb
    from saia import candidate_external_links, candidates, external_evidence_store, rss_evidence, source_context
    from saia.hybrid import digest
    score, candidate, card = _source_ui_candidate_fixture()
    before = digest(candidates.export_cards(MISSION, score))
    calls = []
    def fake(q, timeout):
        calls.append(q)
        raw = ('<rss><channel><item><title>イメージング材料の研究</title>'
               '<link>https://www.aist.go.jp/aist_j/press_release/synthetic-materials.html</link>'
               '<pubDate>'+str(q.end)+'T00:00:00+09:00</pubDate></item></channel></rss>').encode()
        return rss_evidence.parse_response(q, raw, '2026-09-28T12:00:00Z')
    monkeypatch.setattr(rss_evidence, 'fetch', fake)
    first = source_context.collect(MISSION, score, candidate, query='imaging materials', sources=['aist_press_rss'])
    second = source_context.collect(MISSION, score, candidate, query='imaging materials', sources=['aist_press_rss'])
    assert len(calls) == 1 and second['cache_reused_sources'] == ['aist_press_rss']
    report = first['reports'][0]
    assert report['observation_id'] == second['reports'][0]['observation_id']
    assert report['materials'][0]['title_original'] == 'イメージング材料の研究'
    assert report['materials'][0]['usage_scope'] == 'personal_research'
    saved = external_evidence_store.read(report['observation_id'])
    assert saved['payload']['candidate_context_binding'] == first['binding']
    assert saved['payload']['model_input_allowed'] is False
    linked = candidate_external_links.link(MISSION, score, candidate, saved['observation_id'],
        saved['payload']['observations'][0]['url'], 'background_only', 'Synthetic developer test',
        'Синтетическая проверка подключения японского RSS; не экспертная валидация.')
    assert linked['validation_status'] == 'analyst_selected_not_expert_validated'
    assert digest(candidates.export_cards(MISSION, score)) == before
    with db.connect() as conn, conn.cursor() as cur:
        for flag in ('public_republication_allowed', 'model_input_allowed', 'model_training_allowed',
                     'bulk_reuse_approved', 'model_inputs_modified'):
            for invalid in (True, 'false', None):
                bad = {**saved['payload'], flag: invalid}
                bad.pop('report_payload_sha256')
                bad['report_payload_sha256'] = digest(bad)
                with pytest.raises(psycopg.errors.RaiseException, match='personal local reader'):
                    with conn.transaction():
                        cur.execute('INSERT INTO external_evidence_observation '
                            '(observation_id,source,role,topic_id,status,report_payload_sha256,payload) VALUES (%s,%s,%s,%s,%s,%s,%s)',
                            (str(uuid.uuid4()), 'aist_press_rss', bad['role'], saved['topic_id'],
                             bad['status'], bad['report_payload_sha256'], Jsonb(bad)))
        bad = {**saved['payload'], 'usage_scope': 'public_service'}
        bad.pop('report_payload_sha256')
        bad['report_payload_sha256'] = digest(bad)
        with pytest.raises(psycopg.errors.RaiseException, match='personal local reader'):
            with conn.transaction():
                cur.execute('INSERT INTO external_evidence_observation '
                    '(observation_id,source,role,topic_id,status,report_payload_sha256,payload) VALUES (%s,%s,%s,%s,%s,%s,%s)',
                    (str(uuid.uuid4()), 'aist_press_rss', bad['role'], saved['topic_id'],
                     bad['status'], bad['report_payload_sha256'], Jsonb(bad)))
        with pytest.raises(psycopg.errors.RaiseException, match='append-only'):
            with conn.transaction():
                cur.execute('UPDATE external_evidence_observation SET status=status WHERE observation_id=%s', (saved['observation_id'],))
    assert external_evidence_store.read(saved['observation_id'])['payload'] == saved['payload']


def test_candidate_context_cache_is_saved_bound_and_not_scientific_score(seeded, monkeypatch):
    from saia import source_context, external_evidence_store
    from saia.hybrid import digest
    score, candidate, card = _source_ui_candidate_fixture()
    calls = []
    def fake(source, scope):
        calls.append((source, scope))
        value = {'source': source, 'role': 'news_attention_only', 'topic_id': source_context.topic_key(scope),
                 'status': 'complete', 'scientific_score_modified': False, 'missing_is_zero': False,
                 'candidate_context_binding': scope, 'observations': [{'url': 'https://news.mit.edu/synthetic-test',
                 'title': 'Synthetic graph news', 'published_at': '2026-09-28'}]}
        value['report_payload_sha256'] = digest(value)
        return {**external_evidence_store.record(value), 'payload': value}
    monkeypatch.setattr(source_context, '_fetch_one', fake)
    first = source_context.collect(MISSION, score, candidate, sources=['mit_news_rss'])
    second = source_context.collect(MISSION, score, candidate, sources=['mit_news_rss'])
    assert len(calls) == 1
    assert first['binding']['composition_sha256'] == card['composition_sha256']
    assert second['cache_reused_sources'] == ['mit_news_rss']
    assert second['material_count'] == 1
    assert second['reports'][0]['materials'][0]['expert_validated'] is False
    assert second['scientific_score_modified'] is False
    with pytest.raises(ValueError, match='принадлежит другой миссии'):
        source_context.read('foreign-mission', score, candidate)
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute('SELECT evidence_confidence FROM signal_candidate WHERE candidate_id=%s', (candidate,))
        assert cur.fetchone()[0] == 0


@pytest.mark.parametrize('kind', ['artifact', 'grant'])
def test_context_grouping_reuses_immutable_observations_and_links_every_record(seeded, monkeypatch, kind):
    from copy import deepcopy
    from saia import source_context, external_evidence_store, candidate_external_links, candidates
    from saia.hybrid import digest
    score, candidate, card = _source_ui_candidate_fixture()
    scientific_before = digest(candidates.export_cards(MISSION, score))
    rows = {'datacite': [
        {'doi': '10.1234/group-test', 'title': 'Synthetic dataset', 'url': 'https://doi.org/10.1234/group-test',
         'record_type': 'research_dataset', 'resource_publication_date': '2026-09-01'},
        {'doi': '10.1234/group-test.v1', 'title': 'Synthetic dataset version', 'url': 'https://doi.org/10.1234/group-test.v1',
         'record_type': 'research_dataset', 'resource_publication_date': '2026-09-02',
         'related_dois': [{'doi': '10.1234/group-test', 'relation': 'IsVersionOf'}]}],
        'nsf_awards': [{'award_id': '2554349', 'title': 'Synthetic NSF grant',
            'url': 'https://www.nsf.gov/awardsearch/showAward?AWD_ID=2554349', 'award_date': '2026-09-01',
            'record_type': 'research_grant_award', 'funding_amount': 100}],
        'openaire_projects': [{'project_id': 'nsf::synthetic-project', 'grant_reference': '2554349',
            'funders': [{'shortName': 'NSF', 'jurisdiction': 'US'}], 'title': 'Synthetic aggregate grant',
            'url': 'https://example.org/synthetic-project', 'project_start_date': '2026-09-02',
            'record_type': 'funded_research_project', 'funding_amount': 200}]}
    sources = ['datacite'] if kind == 'artifact' else ['openaire_projects', 'nsf_awards']
    original, calls = {}, []
    def fake(source, scope):
        calls.append(source)
        value = {'source': source, 'role': external_evidence_store.SOURCES[source],
            'topic_id': source_context.topic_key(scope), 'status': 'complete',
            'scientific_score_modified': False, 'missing_is_zero': False,
            'candidate_context_binding': scope, 'observations': deepcopy(rows[source])}
        value['report_payload_sha256'] = digest(value)
        result = external_evidence_store.record(value)
        original[result['observation_id']] = deepcopy(value)
        return {**result, 'payload': value}
    monkeypatch.setattr(source_context, '_fetch_one', fake)
    first = source_context.collect(MISSION, score, candidate, 'synthetic grouping', sources)
    second = source_context.collect(MISSION, score, candidate, 'synthetic grouping', sources)
    packet = source_context.read(MISSION, score, candidate, 'synthetic grouping', sources)
    assert len(calls) == len(sources) and second['fetched_sources'] == []
    assert second['cache_reused_sources'] == sources
    assert packet['binding']['composition_sha256'] == card['composition_sha256']
    assert packet['material_count'] == 2
    assert packet['material_grouping']['display_group_count'] == 1
    assert packet['material_grouping'] == first['material_grouping'] == second['material_grouping']
    assert packet['material_grouping']['groups'][0]['record_count'] == 2
    for family in packet['material_grouping']['groups']:
        for material in family['records']:
            linked = candidate_external_links.link(MISSION, score, candidate, material['observation_id'], material['url'],
                'background_only', 'Synthetic developer grouping test',
                'Синтетическая проверка доступности исходных записей; не экспертное подтверждение.')
            assert linked['validation_status'] == 'analyst_selected_not_expert_validated'
    for identifier, value in original.items():
        assert external_evidence_store.read(identifier)['payload'] == value
    assert digest(candidates.export_cards(MISSION, score)) == scientific_before


def test_russian_presentation_is_idempotent_guarded_draft_not_score(seeded, monkeypatch):
    from saia import card_presentation
    from psycopg.types.json import Jsonb
    import uuid
    score, candidate, card = _source_ui_candidate_fixture()
    calls = []
    def fake(model, packet):
        calls.append(model)
        return ({'title_ru': 'Графовые свёртки', 'description': {'text': 'Исследуется графовый метод.', 'evidence_ids': [1]},
                 'problem': None, 'advantage': None, 'case': None}, {'eval_count': 1})
    monkeypatch.setattr(card_presentation, '_generate_content', fake)
    first = card_presentation.generate(MISSION, score, candidate, 'synthetic-model')
    second = card_presentation.generate(MISSION, score, candidate, 'synthetic-model')
    assert calls == ['synthetic-model'] and second['cache_reused'] is True
    value = first['presentation']
    assert value['binding']['composition_sha256'] == card['composition_sha256']
    assert value['claim_faithfulness_verified'] is False
    assert value['scientific_results_modified'] is False
    with db.connect() as conn, conn.cursor() as cur:
        for sql in ('UPDATE candidate_presentation SET model=model WHERE candidate_id=%s',
                    'DELETE FROM candidate_presentation WHERE candidate_id=%s'):
            with pytest.raises(psycopg.errors.RaiseException, match='append-only'):
                with conn.transaction():
                    cur.execute(sql, (candidate,))
        bad = {**value, 'scientific_results_modified': True}
        with pytest.raises(psycopg.errors.RaiseException, match='guarded draft'):
            with conn.transaction():
                cur.execute('INSERT INTO candidate_presentation (presentation_id,mission_id,score_run_id,candidate_id,'
                            'composition_sha256,input_sha256,version,model,report_sha256,payload) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                            (str(uuid.uuid4()), MISSION, score, candidate, card['composition_sha256'], value['binding']['input_sha256'],
                             card_presentation.VERSION, 'synthetic-model', value['report_sha256'], Jsonb(bad)))


def test_pestle_notes_are_revisioned_idempotent_and_do_not_modify_science(seeded):
    from saia import candidate_analysis as notes
    from saia.candidates import export_cards
    import uuid
    score, candidate, card = _source_ui_candidate_fixture()
    before = export_cards(MISSION, score)
    assert notes.read(MISSION, score, candidate)['revision'] == 0
    reference = notes.publication_references(card)[0]['id']
    content = {'author': 'Synthetic test scout', 'summary': 'Это тестовая гипотеза.', 'pestle': [
        {'dimension': 'technological', 'text': 'Метод может изменить обработку графов.', 'effect': 'opportunity',
         'horizon': 'not_assessed', 'dependencies': 'Нужна независимая проверка.', 'evidence_ids': [reference]}], 'industry_impacts': []}
    operation = str(uuid.uuid4())
    first = notes.save(MISSION, score, candidate, content, 0, operation)
    replay = notes.save(MISSION, score, candidate, content, 0, operation)
    assert first['note'] == replay['note'] and replay['replayed'] is True
    assert first['note']['claim_faithfulness_verified'] is False
    assert first['note']['scientific_results_modified'] is False
    with pytest.raises(notes.RevisionConflict):
        notes.save(MISSION, score, candidate, content, 0)
    revised = {**content, 'summary': 'Гипотеза дополнена новой проверкой.'}
    second = notes.save(MISSION, score, candidate, revised, 1)
    assert second['note']['revision'] == 2 and second['note']['previous_note_id'] == operation
    history = notes.read(MISSION, score, candidate)
    assert len(history['history']) == 2 and history['revision'] == 2
    assert export_cards(MISSION, score) == before
    with db.connect() as conn, conn.cursor() as cur:
        for sql in ('UPDATE candidate_analysis_note SET revision=revision WHERE candidate_id=%s',
                    'DELETE FROM candidate_analysis_note WHERE candidate_id=%s'):
            with pytest.raises(psycopg.errors.RaiseException, match='append-only'):
                with conn.transaction(): cur.execute(sql, (candidate,))


def test_analysis_note_database_rejects_false_expert_claim(seeded):
    from saia import candidate_analysis as notes
    from psycopg.types.json import Jsonb
    import uuid
    score, candidate, card = _source_ui_candidate_fixture()
    value = notes.save(MISSION, score, candidate, {'author': 'Synthetic test scout', 'summary': 'Тестовая запись без оснований.',
                       'pestle': [], 'industry_impacts': []}, 0)['note']
    foreign_id = str(uuid.uuid4())
    altered = {**value, 'note_id': foreign_id, 'revision': 2, 'claim_faithfulness_verified': True}
    with db.connect() as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.errors.RaiseException, match='hypothesis-only'):
            with conn.transaction():
                cur.execute('INSERT INTO candidate_analysis_note (note_id,mission_id,score_run_id,candidate_id,composition_sha256,'
                            'revision,version,report_sha256,payload) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                            (foreign_id, MISSION, score, candidate, card['composition_sha256'], 2, notes.VERSION, value['report_sha256'], Jsonb(altered)))


def test_readable_brief_is_read_only_including_optional_notes(seeded, monkeypatch):
    from saia import candidate_brief, source_context, card_presentation
    from saia.candidates import export_cards
    score, candidate, card = _source_ui_candidate_fixture()
    def unexpected_remote_call(*a, **kw): raise AssertionError('Export must not fetch or generate')
    monkeypatch.setattr(source_context, 'collect', unexpected_remote_call)
    monkeypatch.setattr(card_presentation, 'generate', unexpected_remote_call)
    before = export_cards(MISSION, score)
    html = candidate_brief.build(MISSION, score, candidate)
    assert 'PESTLE: возможные последствия' in html and 'Не проработано' in html
    assert card['label'] in html
    assert export_cards(MISSION, score) == before
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute('SELECT count(*) FROM candidate_analysis_note WHERE candidate_id=%s', (candidate,))
        assert cur.fetchone()[0] == 0


def test_parallel_scout_note_edits_cannot_silently_overwrite_each_other(seeded):
    from concurrent.futures import ThreadPoolExecutor
    from saia import candidate_analysis as notes
    score, candidate, card = _source_ui_candidate_fixture()
    def write(text):
        try:
            return notes.save(MISSION, score, candidate, {'author': 'Synthetic concurrent test', 'summary': text,
                              'pestle': [], 'industry_impacts': []}, 0)['note']['revision']
        except notes.RevisionConflict:
            return 'revision_conflict'
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(write, ['Первый параллельный черновик.', 'Второй параллельный черновик.']))
    assert sorted(map(str, results)) == ['1', 'revision_conflict']
    assert len(notes.read(MISSION, score, candidate)['history']) == 1


def test_analysis_note_external_reference_is_bound_and_preserved(seeded):
    from saia import candidate_analysis as notes, source_context, external_evidence_store
    from saia.hybrid import digest
    score, candidate, card = _source_ui_candidate_fixture()
    scope = source_context.binding(MISSION, score, candidate, card, 'graph method')
    record = {'title': 'Synthetic test context', 'url': 'https://news.mit.edu/synthetic-impact-test', 'published_at': '2026-09-28'}
    payload = {'source': 'mit_news_rss', 'role': 'news_attention_only', 'topic_id': source_context.topic_key(scope),
               'candidate_context_binding': scope, 'status': 'complete', 'scientific_score_modified': False,
               'missing_is_zero': False, 'observations': [record]}
    payload['report_payload_sha256'] = digest(payload)
    stored = external_evidence_store.record(payload)
    ref = notes.external_reference({**stored, 'payload': payload}, record)
    content = {'author': 'Synthetic test scout', 'summary': '', 'pestle': [], 'industry_impacts': [
        {'industry': 'Логистика', 'text': 'Возможное влияние требует предметной проверки.', 'effect': 'unknown',
         'horizon': 'not_assessed', 'dependencies': '', 'evidence_ids': [ref['id']]}]}
    note = notes.save(MISSION, score, candidate, content, 0)['note']
    assert note['content']['references'][0]['observation_sha256'] == payload['report_payload_sha256']
    foreign = {**payload, 'candidate_context_binding': {**scope, 'candidate_id': candidate + 99999}}
    foreign.pop('report_payload_sha256')
    foreign['report_payload_sha256'] = digest(foreign)
    other = external_evidence_store.record(foreign)
    ref2 = notes.external_reference({**other, 'payload': foreign}, record)
    content['industry_impacts'][0]['evidence_ids'] = [ref2['id']]
    with pytest.raises(ValueError, match='не относится'):
        notes.save(MISSION, score, candidate, content, 1)
