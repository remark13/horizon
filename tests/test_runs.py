from datetime import date
import pytest

from saia import methodology, runs


class CursorStub:
    def __init__(self):
        self.results = iter([(date(2017, 1, 1),), ("query-v3", date(2017, 1, 1)), (77,)])
        self.calls = []

    def execute(self, sql, params):
        self.calls.append((sql, params))

    def fetchone(self):
        return next(self.results)


def test_code_version_is_never_unknown():
    assert runs.code_version()
    assert runs.code_version() != "unknown"


def test_runtime_snapshot_records_versions_not_environment_secrets():
    result = runs.runtime_snapshot()
    assert result['python']
    assert result['packages']['numpy']
    assert 'SAIA_DATABASE_URL' not in str(result)


def test_effective_overrides_and_upstream_are_recorded():
    cursor = CursorStub()
    run_id = runs.start_run(
        cursor,
        "mission",
        "cluster",
        methodology.load_default(),
        embedding_model="model-a",
        upstream_run_id=41,
        window_step="year",
        clustering_scale="established",
    )
    assert run_id == 77
    insert_sql, params = cursor.calls[-1]
    assert "upstream_run_id" in insert_sql
    assert "clustering_scale" in insert_sql
    assert params[8] == "year"
    assert params[-3:] == (41, None, "established")
    assert params[1] == 'query-v3'
    assert 'FROM analysis_run' in cursor.calls[1][0]


def test_unfinished_or_foreign_upstream_is_rejected():
    cursor = CursorStub()
    cursor.results = iter([(date(2017, 1, 1),), None])
    with pytest.raises(ValueError, match='Входной прогон'):
        runs.start_run(cursor, 'mission', 'score', methodology.load_default(), upstream_run_id=41)


def test_changed_mission_date_does_not_relabel_old_input():
    cursor = CursorStub()
    cursor.results = iter([(date(2026, 1, 1),), ('old-query', date(2017, 1, 1))])
    with pytest.raises(ValueError, match='Дата входного'):
        runs.start_run(cursor, 'mission', 'score', methodology.load_default(), upstream_run_id=41)
