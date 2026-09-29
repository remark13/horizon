from openpyxl import Workbook
import pytest

from saia.jrc_reference import HEADERS, import_jrc


def workbook_file(tmp_path, description="Описание", flag="да"):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Слабые сигналы JRC 2024"
    sheet.append(HEADERS)
    sheet.append([1, "JRC", "2024", "Кластер", "Cluster", "Сигнал", "Signal",
                  description, flag, "https://example.org/report"])
    path = tmp_path / "jrc.xlsx"
    workbook.save(path)
    return path


def test_jrc_import_preserves_source_and_never_becomes_truth(tmp_path):
    result = import_jrc(workbook_file(tmp_path), official_claimed_signals=2)
    assert result["counts"]["rows"] == 1
    assert result["counts"]["gap_vs_official_claim"] == 1
    assert result["records"][0]["description_ru"] == "Описание"
    assert "future_success_ground_truth" in result["prohibited_use"]
    assert result["report_payload_sha256"]


def test_jrc_import_keeps_missing_description_null(tmp_path):
    result = import_jrc(workbook_file(tmp_path, None, "нет в источнике"), 1)
    assert result["records"][0]["description_ru"] is None
    assert result["counts"]["descriptions_missing"] == 1


def test_jrc_import_rejects_description_contradiction(tmp_path):
    with pytest.raises(ValueError, match="Противоречие"):
        import_jrc(workbook_file(tmp_path, "Описание", "нет в источнике"))
