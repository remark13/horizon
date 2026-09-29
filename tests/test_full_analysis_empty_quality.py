from saia import full_analysis
from saia.scout_web import SCOUT_WEB_HTML


def test_quality_counts_are_scoped_to_exact_run_and_generation(monkeypatch):
    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def execute(self, sql, params):
            assert "q.generation_id=%s" in sql
            assert "w.run_id=%s" in sql
            assert params == (31, 17)

        def fetchall(self):
            return [("exclude", 3)]

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def cursor(self):
            return Cursor()

    monkeypatch.setattr(full_analysis.db, "connect", lambda: Connection())
    assert full_analysis._quality_counts(17, 31) == {"exclude": 3}


def test_scout_distinguishes_no_eligible_publications_from_job_failure():
    assert "input_status==='no_eligible_publications'" in SCOUT_WEB_HTML
    assert "Это не означает, что слабых сигналов нет" in SCOUT_WEB_HTML
