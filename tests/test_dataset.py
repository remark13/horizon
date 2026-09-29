from openpyxl import Workbook

from saia.dataset import load_registry, parse_evidence, registry_report


HEADERS = [
    None,
    "№",
    "Технология (слабый сигнал)",
    "Область",
    "Компании",
    "Почему это слабый сигнал",
    "Стадия развития",
    "Тренд упоминаний",
    "Балл (стадия+тренд)",
    "Источники",
]


def make_registry(path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Экспертный реестр"])
    sheet.append(HEADERS)
    sheet.append([
        None, 1, "Тестовая технология", "ИИ", "Компания",
        "Редкая, но растущая", "Прототип/PoC → Раннее внедрение",
        "Умеренный рост", 6,
        "[Статья](https://arxiv.org/abs/1234.5678), "
        "[Новость](https://techcrunch.com/example)",
    ])
    workbook.save(path)


def test_markdown_sources_are_normalized():
    refs = parse_evidence(
        "[Paper](https://arxiv.org/abs/1), [Release](https://example.com/a)"
    )
    assert [ref.domain for ref in refs] == ["arxiv.org", "example.com"]
    assert refs[0].source_type == "preprint"


def test_registry_import_preserves_expert_fields(tmp_path):
    path = tmp_path / "registry.xlsx"
    make_registry(path)
    records = load_registry(path)
    assert len(records) == 1
    assert records[0].title == "Тестовая технология"
    assert records[0].stage_code == "prototype"
    assert len(records[0].evidence) == 2
    assert records[0].label is None


def test_registry_is_not_mislabeled_as_train_ready(tmp_path):
    path = tmp_path / "registry.xlsx"
    make_registry(path)
    records = load_registry(path)
    report = registry_report(path, records)
    assert report["training_readiness"] == "not_ready"
    assert report["labels_present"] == 0
    assert report["negative_labels"] == 0

