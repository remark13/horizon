"""Точный вход долгого семантического анализа, без latest и частичных векторов."""
from argparse import Namespace

import numpy as np
import pytest

from saia import cluster


class Cursor:
    def __init__(self, row):
        self.row = row
        self.calls = []

    def execute(self, sql, params):
        self.calls.append((sql, params))

    def fetchone(self):
        return self.row


def test_explicit_generation_never_resolves_latest(monkeypatch):
    monkeypatch.setattr(cluster.runs, "current_run_id", lambda *a: pytest.fail("latest"))
    cur = Cursor((2517, 761))
    assert cluster.resolve_quality_input(cur, "corpus", 761) == (2517, 761)
    sql, params = cur.calls[0]
    assert params == (761, "corpus")
    assert "q.status='done'" in sql and "n.status='done'" in sql
    assert "n.mission_id=%s" in sql and "n.kind='normalize'" in sql


@pytest.mark.parametrize("generation", [0, -1, True, "761"])
def test_invalid_generation_fails_before_query(generation):
    cur = Cursor(None)
    with pytest.raises(ValueError):
        cluster.resolve_quality_input(cur, "corpus", generation)
    assert not cur.calls


def test_missing_or_unfinished_or_other_mission_generation_rejected():
    with pytest.raises(ValueError, match="завершённый"):
        cluster.resolve_quality_input(Cursor(None), "corpus", 761)


@pytest.mark.parametrize("expected,actual", [(0, 0), (3, 2), (3, 4)])
def test_partial_or_empty_vector_corpus_rejected(expected, actual):
    with pytest.raises(ValueError):
        cluster.require_vector_count(expected, actual)


def test_complete_vector_count_allowed():
    cluster.require_vector_count(3, 3)


def test_analyze_pins_every_input_and_full_coverage(monkeypatch):
    config = object()
    result = {"run_id": 42}

    def generate(args, frozen):
        assert frozen is config
        assert args == Namespace(mission_id="corpus", quality_generation=761,
                                 model="exact-revision", backend="graph", step="quarter",
                                 scale="micro", require_complete_vectors=True,
                                 current_period_reconstruction=False)
        return result

    monkeypatch.setattr(cluster, "generate", generate)
    assert cluster.analyze("corpus", 761, "exact-revision", "graph", "quarter", "micro", config) == result


def test_analyze_requires_explicit_current_period_reconstruction(monkeypatch):
    config = object()

    def generate(args, frozen):
        assert frozen is config
        assert args.backend == "global_bertopic"
        assert args.current_period_reconstruction is True
        return {"run_id": 43}

    monkeypatch.setattr(cluster, "generate", generate)
    assert cluster.analyze(
        "corpus", 761, "exact-revision", "global_bertopic", "quarter",
        "micro", config, current_period_reconstruction=True,
    ) == {"run_id": 43}


def test_analyze_does_not_declare_empty_result_ready(monkeypatch):
    monkeypatch.setattr(cluster, "generate", lambda *a: 0)
    with pytest.raises(ValueError, match="не создан"):
        cluster.analyze("corpus", 761, "model")


@pytest.mark.parametrize("generation,model", [(None, "model"), (True, "model"), (761, ""), (761, None)])
def test_analyze_rejects_implicit_input_before_work(monkeypatch, generation, model):
    monkeypatch.setattr(cluster, "generate", lambda *a: pytest.fail("work started"))
    with pytest.raises(ValueError):
        cluster.analyze("corpus", generation, model)


def test_cli_keeps_success_exit_code(monkeypatch):
    monkeypatch.setattr(cluster, "generate", lambda args: {"run_id": 42})
    assert cluster.main(["corpus", "--quality-generation", "761", "--require-complete-vectors"]) == 0


def test_global_topics_keep_fixed_identity_across_months():
    vectors = [np.array([1.0, 0.0]), np.array([0.9, 0.1]), np.array([0.8, 0.2])]
    items = [
        (1, 'alpha', vectors[0], '2025-01'),
        (2, 'alpha', vectors[1], '2025-02'),
        (3, 'beta', vectors[2], '2025-03'),
    ]
    topics, snapshots, memberships, noise = cluster.project_global_topics(
        items, np.array([0, 0, -1]), {0: ['alpha']},
        ['2025-01', '2025-02', '2025-03'], 'month', 365, 'exponential', 3, 8,
    )
    assert list(topics) == [0] and topics[0]['first_window'] == '2025-01'
    assert topics[0]['last_window'] == '2025-02'
    assert [item['doc_count'] for item in snapshots] == [1, 1, 0]
    assert memberships == [(0, 1, '2025-01'), (0, 2, '2025-02')]
    assert noise == 1


def test_global_backend_requires_explicit_current_period_mode(monkeypatch, capsys):
    args = Namespace(mission_id='corpus', quality_generation=1, model='model',
                     backend='global_bertopic', step='month', scale='micro',
                     require_complete_vectors=True, current_period_reconstruction=False)
    assert cluster.generate(args) == 1
    assert 'запрещён' in capsys.readouterr().out
