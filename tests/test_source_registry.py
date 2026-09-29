from pathlib import Path

import pytest
import yaml
from openpyxl import Workbook

from saia.source_registry import (
    build_report, load_registry, summarize_example_workbook, validate_runtime_sources,
)


def test_project_registry_is_valid_and_separates_external_attention():
    path = Path("config/source-adoption.v0.4.38.yaml")
    registry = load_registry(path)
    assert len(registry["sources"]) >= 12
    assert registry["scientific_score_uses_external_attention"] is False
    assert {item["decision"] for item in registry["sources"]} >= {"accept_now", "adapt", "defer"}
    report = build_report(registry, path)
    assert report["production_thresholds_modified"] is False
    assert report["report_payload_sha256"]
    assert {item["id"] for item in registry["sources"]} >= {
        "nih_reporter", "deps_dev", "semantic_scholar",
        "ukri_gtr", "eu_funding_tenders", "huggingface_hub",
        "gdelt_doc_2_0", "epo_ops",
    }
    assert "gdelt_doc_2" not in {item["id"] for item in registry["sources"]}
    assert len(report["registry_lineage"]) == 4


def test_every_runtime_external_connector_has_one_registered_source_id():
    registry = load_registry(Path("config/source-adoption.v0.4.38.yaml"))
    runtime = yaml.safe_load(Path("config/external-sources.v0.4.31.yaml").read_text())
    result = validate_runtime_sources(registry, runtime)
    assert result["all_runtime_sources_registered"] is True
    assert set(result["configured_source_ids"]) >= {"gdelt_doc_2_0", "epo_ops"}
    assert result["scientific_score_modified"] is False


def test_clinical_trial_connector_has_an_explicit_registry_decision():
    from saia.external_sources import load_policy

    path = Path("config/source-adoption.v0.4.51.yaml")
    registry = load_registry(path)
    assert len(registry["_registry_lineage"]) == 17
    result = validate_runtime_sources(registry, load_policy())
    assert result["all_runtime_sources_registered"] is True
    assert "clinicaltrials_gov" in result["configured_source_ids"]
    assert result["scientific_score_modified"] is False


def test_europe_pmc_connector_has_an_explicit_registry_decision():
    from saia.external_sources import load_policy

    path = Path("config/source-adoption.v0.4.51.yaml")
    registry = load_registry(path)
    assert len(registry["_registry_lineage"]) == 17
    result = validate_runtime_sources(registry, load_policy())
    assert result["all_runtime_sources_registered"] is True
    assert "europe_pmc" in result["configured_source_ids"]


def test_nasa_ntrs_connector_has_an_explicit_registry_decision():
    from saia.external_sources import load_policy
    from saia.source_registry import load_registry, validate_runtime_sources

    path = Path("config/source-adoption.v0.4.51.yaml")
    result = validate_runtime_sources(load_registry(path), load_policy())
    assert "nasa_ntrs" in result["configured_source_ids"]
    assert result["scientific_score_modified"] is False


def test_current_registry_keeps_link_only_and_keyed_sources_out_of_runtime():
    from saia.external_sources import load_policy

    registry = load_registry(Path("config/source-adoption.v0.4.51.yaml"))
    result = validate_runtime_sources(registry, load_policy())
    sources = {item["id"]: item for item in registry["sources"]}
    assert result["all_runtime_sources_registered"] is True
    assert sources["sitra_weak_signals_2025"]["decision"] == "defer"
    assert sources["epo_linked_open_ep_data"]["decision"] == "defer"
    assert sources["uspto_odp_patentsview"]["decision"] == "defer"
    assert sources["nasa_ntrs_bulk_metadata"]["decision"] == "defer"
    assert not {"sitra_weak_signals_2025", "uspto_odp_patentsview"} & set(result["configured_source_ids"])


def test_korean_spri_report_is_link_only_due_to_reuse_terms():
    from saia.external_sources import load_policy

    registry = load_registry(Path("config/source-adoption.v0.4.51.yaml"))
    result = validate_runtime_sources(registry, load_policy())
    sources = {item["id"]: item for item in registry["sources"]}
    assert result["all_runtime_sources_registered"] is True
    assert sources["spri_digital_weak_signals_2025"]["decision"] == "defer"
    assert "spri_digital_weak_signals_2025" not in result["configured_source_ids"]
    assert sources["nistep_kidsashi_horizon_scanning"]["decision"] == "defer"
    assert "nistep_kidsashi_horizon_scanning" not in result["configured_source_ids"]


def test_usaspending_is_registered_but_sbir_is_not_a_live_connector():
    from saia.external_sources import load_policy

    registry = load_registry(Path("config/source-adoption.v0.4.51.yaml"))
    result = validate_runtime_sources(registry, load_policy())
    sources = {item["id"]: item for item in registry["sources"]}
    assert result["all_runtime_sources_registered"] is True
    assert sources["usaspending"]["decision"] == "adapt"
    assert sources["usaspending"]["role"] == "public_procurement_only"
    assert "usaspending" in result["configured_source_ids"]
    assert sources["sbir_sttr_awards"]["decision"] == "defer"
    assert "sbir_sttr_awards" not in result["configured_source_ids"]


def test_unknown_license_cannot_be_accepted(tmp_path):
    source = yaml.safe_load(Path("config/source-adoption.v0.4.31.yaml").read_text())
    source.pop("extends")
    source["sources"][0]["license_status"] = "unknown"
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(source), encoding="utf-8")
    with pytest.raises(ValueError, match="неизвестными правами"):
        load_registry(path)


def test_example_workbook_is_inventory_not_approval(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Источники"
    sheet.append(["Домен", "Упоминаний", "Категория", "Что это",
                  "Доступ к данным (API/скачивание)", "Достоверность",
                  "Пример: сигнал", "Пример: ссылка"])
    sheet.append(["example.org", 2, "Блог", "Пример", "Нет", "не проверялось", "X", "https://example.org/x"])
    path = tmp_path / "sources.xlsx"
    workbook.save(path)
    report = summarize_example_workbook(path)
    assert report["rows"] == 1
    assert report["reported_mentions"] == 2
    assert report["approval_inferred"] is False
