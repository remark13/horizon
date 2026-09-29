from saia import canonical_text, full_analysis


def test_normalization_cache_requires_current_text_policy(monkeypatch):
    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def execute(self, sql, params):
            assert "kind='normalize' AND status='done'" in sql
            assert "notes->'canonical_text_policy'->>'version'=%s" in sql
            assert params == ("mission", "mission/v2", "3", canonical_text.VERSION)

        def fetchone(self):
            return (7,)

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def cursor(self):
            return Cursor()

    monkeypatch.setattr(full_analysis.db, "connect", lambda: Connection())
    assert full_analysis._existing_normalize("mission", 3, "mission/v2") == 7
