import json

from scripts.audit_free_ru_semantic_qualifiers import audit


def test_frozen_semantic_qualifiers_require_all_concepts_in_one_work(tmp_path):
    config_dir = tmp_path / "config"
    output_dir = tmp_path / "outputs"
    config_dir.mkdir()
    output_dir.mkdir()
    cases = []
    source_rows = []
    for index in range(3):
        case_id = f"case-{index}"
        query = f"русский запрос {index}"
        cases.append({"case_id": case_id, "query_ru": query,
                      "source_report": "outputs/source.json",
                      "required_concepts": {"material": ["hydrogen"],
                                            "device": ["optical fiber"]}})
        source_rows.append({"case_id": case_id, "query_ru": query,
                            "mode": "semantic", "status": "succeeded",
                            "results": [
                                {"rank_in_first_page": 1, "openalex_id": f"W{index}",
                                 "doi": None, "title": "Hydrogen optical-fiber sensor",
                                 "abstract": "Leak detector", "within_exact_period": True},
                                {"rank_in_first_page": 2, "openalex_id": f"X{index}",
                                 "doi": None, "title": "Hydrogen sensor",
                                 "abstract": None, "within_exact_period": True},
                                {"rank_in_first_page": 3, "openalex_id": f"Y{index}",
                                 "doi": None, "title": "Hydrogen optical fiber sensor",
                                 "abstract": "Out of period", "within_exact_period": False},
                            ]})
    config = config_dir / "config.json"
    config.write_text(json.dumps({
        "version": "free-ru-semantic-qualifier-gate-v1",
        "cases": cases, "interpretation": {"not_scientific_truth": True},
    }), encoding="utf-8")
    (output_dir / "source.json").write_text(json.dumps({"rows": source_rows}), encoding="utf-8")
    result = audit(config)
    assert [row["all_concepts_mentioned_count"] for row in result["cases"]] == [1, 1, 1]
    assert [row["semantic_first_page_in_period"] for row in result["cases"]] == [2, 2, 2]
    assert result["cases"][0]["rows"][0]["matched_terms_by_concept"]["device"] == [
        "optical fiber"]
    assert result["cases"][0]["missing_abstract_count"] == 1
