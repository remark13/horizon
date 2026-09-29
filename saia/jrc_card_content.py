"""Cited bilingual report explanations. Never detector input or gold labels."""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
from html import escape
import json
import math
from pathlib import Path

from saia.hybrid import digest

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "data/reference/public_signals/jrc.card-content.v1.json"
VERSION = "jrc-card-content-v1"
SOURCE_ID = "jrc_weak_signals_2024"
PDF_URL = "https://publications.jrc.ec.europa.eu/repository/bitstream/JRC140959/JRC140959_01.pdf"


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Повтор ключа в содержании карточек JRC.")
        value[key] = item
    return value


def validate(value: dict, catalog: dict) -> None:
    if (value.get("version") != VERSION or value.get("source_id") != SOURCE_ID
            or value.get("pdf_url") != PDF_URL or value.get("edition_year") != 2024
            or value.get("license") != "CC BY 4.0"
            or len(value.get("source_pdf_sha256", "")) != 64):
        raise ValueError("Неверный паспорт обогащения JRC.")
    canonical = {row["id"]: row for row in catalog["records"] if row["source_id"] == SOURCE_ID}
    seen = set()
    if not isinstance(value.get("records"), list):
        raise ValueError("Неверный список описаний JRC.")
    allowed = {"id", "title", "pages", "what", "why", "change", "applications", "actors", "historical_radar", "editorial_note"}
    for row in value["records"]:
        source = canonical.get(row.get("id"))
        if (not source or row["id"] in seen or set(row) - allowed or source["title"] != row.get("title")
                or not isinstance(row.get("pages"), list) or source["source_page"] not in row["pages"]
                or any(not isinstance(p, int) or isinstance(p, bool) or not 106 <= p <= 145 for p in row["pages"])
                or row["pages"] != sorted(set(row["pages"]))):
            raise ValueError("Описание JRC не связано с точной записью и страницей.")
        seen.add(row["id"])
        for key in ("what", "why", "change", *(('editorial_note',) if row.get('editorial_note') else ())):
            pair = row.get(key)
            if (not isinstance(pair, dict) or set(pair) != {"ru", "en"}
                    or any(not isinstance(text, str) or not text.strip() or len(text) > 2000 for text in pair.values())):
                raise ValueError("Описание JRC должно иметь русский и английский текст.")
        applications, actors = row.get("applications"), row.get("actors")
        if (not isinstance(applications, dict) or set(applications) != {"ru", "en"}
                or any(not isinstance(items, list) or not 1 <= len(items) <= 8
                       or any(not isinstance(text, str) or not text.strip() for text in items) for items in applications.values())
                or len(applications["ru"]) != len(applications["en"])
                or not isinstance(actors, list) or not 1 <= len(actors) <= 5
                or any(not isinstance(name, str) or not name.strip() for name in actors)):
            raise ValueError("Некорректные применения или участники JRC.")
        metric = row.get("historical_radar")
        if metric is not None:
            expected = {"page", "activity_percent", "publications_percent", "patents_percent", "persistence_years"}
            if (not isinstance(metric, dict) or set(metric) != expected or metric["page"] != 37
                    or any(not isinstance(metric[k], (int, float)) or isinstance(metric[k], bool)
                           or not math.isfinite(metric[k]) or not 0 <= metric[k] <= 100
                           for k in ("activity_percent", "publications_percent", "patents_percent"))
                    or not isinstance(metric["persistence_years"], int) or isinstance(metric["persistence_years"], bool)
                    or not 0 <= metric["persistence_years"] <= 28):
                raise ValueError("Неверное историческое значение радара JRC.")


@lru_cache(maxsize=1)
def load() -> dict:
    value = json.loads(PATH.read_text(encoding="utf-8"), object_pairs_hook=_unique)
    catalog = json.loads((ROOT / "data/reference/public_signals/catalog.v6.json").read_text(encoding="utf-8"))
    validate(value, catalog)
    return value


def for_record(row: dict) -> dict | None:
    if row.get("source_id") != SOURCE_ID:
        return None
    document = load()
    content = next((entry for entry in document["records"] if entry["id"] == row.get("id")), None)
    if content is None or content["title"] != row.get("title") or row.get("source_page") not in content["pages"]:
        return None
    result = {**deepcopy(content), "version": VERSION, "source_id": SOURCE_ID,
              "report_url": document["report_url"], "pdf_url": PDF_URL,
              "source_pdf_sha256": document["source_pdf_sha256"], "published_at": document["published_at"],
              "edition_year": 2024, "license": document["license"], "attribution": document["attribution"],
              "adaptation": document["adaptation"], "scope": "historical_publisher_explanation",
              "primary_papers_verified": False, "used_for_score": False,
              "actors_identity_verified": False, "current_weak_signal_verified": False,
              "chart_activity_window_label": "2021–2024",
              "method_activity_window_label": "2021–2023",
              "activity_period_consistent": False}
    result["explanation_payload_sha256"] = digest(result)
    result["html"] = render(result)
    return result


def render(value: dict, lang: str = "ru") -> str:
    if lang not in {"ru", "en"}:
        raise ValueError("Доступны языки содержимого ru и en.")
    def esc(item):
        return escape(str(item), quote=True)
    def pair(text):
        return (f'<span data-public-content data-public-ru="{esc(text["ru"])}" '
                f'data-public-en="{esc(text["en"])}" lang="{lang}">{esc(text[lang])}</span>')
    pieces = ['<section class="jrc-content"><h3>Что сообщает JRC</h3><p class="form-note">Подборка 2024 года. Описание из отчёта, не текущий вывод Horizon.</p>']
    for key, label in (("what", "Что это"), ("why", "Зачем это нужно"), ("change", "Что меняется")):
        pieces.append(f'<h4>{label}</h4><p>{pair(value[key])}</p>')
    applications = {key: " · ".join(value["applications"][key]) for key in ("ru", "en")}
    pieces.append('<p class="jrc-applications">' + pair(applications) + '</p>')
    metric = value.get("historical_radar")
    if metric:
        pieces.append('<details class="jrc-details"><summary>Исторический профиль JRC</summary><p class="form-note">Значения из рисунка 27. Не балл Horizon и не вероятность успеха.</p><dl class="jrc-metrics">')
        for key, label, unit in (("publications_percent", "Научные публикации", "%"), ("patents_percent", "Патенты", "%"),
                                 ("activity_percent", "Документы за последнее окно", "%"), ("persistence_years", "Длительность следа", " лет")):
            number = str(metric[key]).replace(".", ",")
            pieces.append(f'<div><dt>{label}</dt><dd>{number}{unit}</dd></div>')
        pieces.append('</dl><p class="form-note">Доли публикаций и патентов относятся к набору документов JRC. Активность означает долю документов последнего окна, а не скорость роста. Длительность означает возраст публикационного следа, а не устойчивость ежегодного роста.</p><p class="form-note">В подписи графика окно 2021–2024. В методике указано 2021–2023. Это несогласованность отчёта, которую нельзя считать проверенным текущим периодом.</p>'
                      f'<a href="{PDF_URL}#page={metric["page"]}" target="_blank" rel="noopener noreferrer">График JRC, стр. {metric["page"]} ↗</a></details>')
    pieces.append('<details class="jrc-details"><summary>Участники, указанные JRC</summary><ul>'
                  + ''.join(f'<li>{esc(name)}</li>' for name in value["actors"])
                  + '</ul><p class="form-note">Исторический список из отчёта. Не текущий рейтинг, не поставщики и не независимые подтверждения. Названия отдельно не нормализованы.</p></details>')
    if value.get("editorial_note"):
        pieces.append('<details class="jrc-details"><summary>Что уточнено при переносе</summary><p>' + pair(value["editorial_note"]) + '</p></details>')
    pages = ', '.join(str(page) for page in value["pages"])
    pieces.append(f'<p class="form-note"><a href="{PDF_URL}#page={value["pages"][0]}" target="_blank" rel="noopener noreferrer">Описание JRC, стр. {pages} ↗</a></p>')
    pieces.append('<details class="jrc-details"><summary>Происхождение описания</summary><p class="form-note">' + esc(value["adaptation"])
                  + '</p><p class="form-note">' + esc(value["attribution"]) + ' Пересказ и перевод Horizon. CC BY 4.0.</p></details></section>')
    return ''.join(pieces)
