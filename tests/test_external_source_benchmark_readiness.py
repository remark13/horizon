import json

from saia.external_source_benchmark_readiness import audit


def test_unlabelled_seed_registry_is_not_declared_a_benchmark(tmp_path):
    path = tmp_path / "seeds.jsonl"
    path.write_text(json.dumps({
        "source_number": 1, "title": "Сигнал", "area": "ИИ",
        "label": None, "label_as_of": None,
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    result = audit(path)
    assert result["records"] == 1
    assert result["benchmark_ready"] is False
    assert result["benchmark_executed"] is False
    assert result["scientific_score_modified"] is False
