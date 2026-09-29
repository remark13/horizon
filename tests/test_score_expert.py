from datetime import datetime, timezone

import pytest

from saia import score_expert


class Cursor:
    def __init__(self, rows):
        self.rows = iter(rows)
    def execute(self, *args):
        return None
    def fetchone(self):
        return next(self.rows)
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return None


class Connection:
    def __init__(self, rows):
        self.rows = rows
    def cursor(self):
        return Cursor(self.rows)
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return None


def packet():
    return {'mission_id': 'm', 'score_run_id': 9,
            'cards': [{'candidate_id': 1, 'label': 'topic', 'status': 'watch',
                       'composition_sha256': 'c' * 64}]}


def test_record_binds_opinion_to_exact_saved_card(monkeypatch):
    monkeypatch.setattr(score_expert, 'export_cards', lambda *args: packet())
    monkeypatch.setattr(score_expert.db, 'connect',
                        lambda: Connection([(datetime(2026, 9, 20, tzinfo=timezone.utc),)]))
    result = score_expert.record(
        'm', 1, 'needs_review', 'analyst',
        'Нужно проверить полный состав публикаций.', [], 9,
        '00000000-0000-0000-0000-000000000001')
    assert result['score_run_id'] == 9 and len(result['candidate_content_sha256']) == 64
    assert result['composition_sha256'] == 'c' * 64
    assert result['replayed'] is False


def test_candidate_from_another_run_is_rejected(monkeypatch):
    monkeypatch.setattr(score_expert, 'export_cards', lambda *args: packet())
    with pytest.raises(ValueError, match='отсутствует'):
        score_expert.record('m', 2, 'noise', 'analyst',
                            'Состав не образует единой исследовательской линии.', [])


def test_history_limit_is_bounded_before_database(monkeypatch):
    with pytest.raises(ValueError, match='Лимит'):
        score_expert.history('m', 1, limit=0)


def test_history_follows_exact_composition_across_score_runs(monkeypatch):
    created = datetime(2026, 9, 20, tzinfo=timezone.utc)
    monkeypatch.setattr(score_expert, 'export_cards', lambda *args: packet())
    # Expose the normal fetchall contract used by the history query.
    class HistoryCursor(Cursor):
        def fetchall(self):
            return next(self.rows)

    class HistoryConnection(Connection):
        def cursor(self):
            return HistoryCursor(self.rows)

    monkeypatch.setattr(
        score_expert.db, 'connect',
        lambda: HistoryConnection([
            (1,),
            [('00000000-0000-0000-0000-000000000001', 3, 77,
              'analyst', 'needs_review', 'Нужна независимая проверка состава.',
              [], created, 'a' * 64)],
        ]),
    )
    result = score_expert.history('m', 1, 9)
    assert result['history_scope'] == 'exact_publication_composition_across_score_runs'
    assert result['total'] == 1
    assert result['reviews'][0]['source_score_run_id'] == 3
    assert result['reviews'][0]['source_candidate_id'] == 77
    assert result['reviews'][0]['matches_current_composition'] is True
    assert result['reviews'][0]['matches_current_card'] is False
