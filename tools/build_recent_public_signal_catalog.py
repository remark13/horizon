"""Build v6 from the unchanged v5 archive and reviewed 2024-2026 references.

Titles below are short topic labels, not copied report descriptions. PDF
locators are checked offline against the exact page before the catalogue is
written. HTML sources were reviewed on the official publisher's site; no PDF
page or downloaded full-text snapshot is claimed for them.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/reference/public_signals"
VERIFIED = "2026-09-29"
TYPE_LABELS = {
    "weak_signal_report": "Слабый сигнал из отчёта",
    "horizon_scan": "Форсайт-наблюдение",
    "technology_selection": "Перспективная технология",
}
EEA_URL = "https://www.eionet.europa.eu/etcs/etc-st/products/etc-st-report-2024-5-horizon-scanning-2024-results-of-the-eea-2013-eionet-participatory-horizon-scan-to-identify-emerging-issues-relevant-to-the-environment-and-environmental-policy"
EEA_PDF = EEA_URL + "/@@download/file/ETC%20ST%20Report%202024-05_Veenhoff%20et%20al.%20Horizon%20Scanning%202024.pdf"
WEF_URL = "https://www.weforum.org/publications/top-10-emerging-technologies-of-2026/"
WEF_READER = WEF_URL + "in-full/1-everything-to-grid-energy/"
# Heading IDs and the World Models table-of-contents href were verified on
# the live official reader on 2026-09-29. These are not guessed URL variants.
WEF_SECTIONS = (
    "1-everything-to-grid-energy", "2-direct-lithium-extraction",
    "3-passive-radiative-cooling-materials", "4-pfas-destruction",
    "5-precision-fermentation", "6-exosome-drug-delivery",
    "7-personalized-mrna-cancer-vaccines", "8-quantum-simulation-for-drug-discovery",
    "9-world-models", "10-lattice-based-cryptography",
)
KOREA_URL = "https://msit.go.kr/eng/bbs/view.do?bbsSeqNo=42&mId=4&mPid=2&nttSeqNo=1076&sCode=eng&searchOpt=ALL"


def pdf_text(path: Path, page: int) -> str:
    return subprocess.run(["pdftotext", "-f", str(page), "-l", str(page), str(path), "-"],
                          capture_output=True, text=True, check=True).stdout


def normal(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def source(source_id, publisher, short_label, title, url, year, date, kind,
           note, *, pdf=None, pdf_url=None, license="Metadata and source links only", **extra):
    value = {"source_id": source_id, "publisher": publisher, "short_label": short_label,
             "title": title, "url": url, "edition_year": year,
             "published_at": date, "source_type": kind, "type_label": TYPE_LABELS[kind],
             "coverage_note_ru": note, "license": license,
             "added_at": VERIFIED, "verified_at": VERIFIED, **extra}
    if pdf:
        value.update({"local_pdf": pdf, "pdf_url": pdf_url,
                      "pdf_sha256": hashlib.sha256((DATA / pdf).read_bytes()).hexdigest(),
                      "verification_method": "pinned_pdf_page_text_and_visual_review"})
    else:
        value["verification_method"] = "official_html_reader_manual_topic_review"
        value["full_text_snapshot_available"] = False
    return value


def record(document, number, title, ru, category, *, page=None, section=None,
           anchor=None, category_basis="saia_editorial_topic_tag"):
    if page is not None:
        if not anchor or normal(anchor) not in normal(pdf_text(DATA / document["local_pdf"], page)):
            raise ValueError(f"Missing verified page anchor: {document['source_id']} / {page}: {anchor}")
        url = document["pdf_url"] + "#page=" + str(page)
    else:
        if not section:
            raise ValueError("HTML items require a named section, not an invented PDF page")
        url = document.get("reader_url", document["url"])
    kind = ("published_emerging_technology" if document["source_type"] == "technology_selection"
            else "published_horizon_scan_observation")
    return {"id": f"{document['id_prefix']}-{number:02d}", "source_id": document["source_id"],
            "title": title, "title_ru": ru, "title_basis": "short_topic_label_not_verbatim_heading",
            "category": category, "category_basis": category_basis,
            "source_page": page, "source_section": section,
            "source_url": url, "source_date": document["published_at"],
            "source_date_precision": document.get("date_precision", "day"),
            "source_date_basis": document.get("date_basis", "publisher_release_date"),
            "source_year": document["edition_year"], "source_label": document["title"],
            "source_short_label": document["short_label"], "source_type": document["source_type"],
            "type_label": document["type_label"], "record_kind": kind,
            "current_weak_signal_verified": False, "added_at": VERIFIED}


def main():
    catalog = json.loads((DATA / "catalog.v5.json").read_text(encoding="utf-8"))
    catalog.update({"version": "public-signals-catalog-v6", "minimum_active_year": 2023,
                    "built_at": VERIFIED,
                    "freshness_policy": "edition_year_not_download_or_webpage_update_year",
                    "interpretation": "External, source-labelled observations and technology selections. Not SAIA detections, gold labels, proof of current weakness or future success."})
    legacy = {
        "jrc_weak_signals_2024": (2024, "JRC 2024", "weak_signal_report", "216 распознанных названий из 221 заявленного в отчёте; недостающие записи не придуманы."),
        "jrc_weak_signals_2021": (2021, "JRC 2021 · архив", "weak_signal_report", "93 записи сохранены только для истории. В актуальную выдачу не включаются."),
        "espas_signal_cards_2026": (2026, "ESPAS 2026", "horizon_scan", "92 карточки закреплённого PDF, не вся обновляемая онлайн-подборка. Есть общественные и политические наблюдения, не только технологии."),
    }
    for document in catalog["source_documents"]:
        year, label, kind, note = legacy[document["source_id"]]
        document.update({"edition_year": year, "short_label": label, "source_type": kind,
                         "type_label": TYPE_LABELS[kind], "coverage_note_ru": note,
                         "active": year >= 2023})
    sources = {document["source_id"]: document for document in catalog["source_documents"]}
    for row in catalog["records"]:
        document = sources[row["source_id"]]
        row.update({"source_year": document["edition_year"], "source_type": document["source_type"],
                    "source_short_label": document["short_label"], "type_label": document["type_label"],
                    "source_date_precision": "day", "category_basis": "publisher_category",
                    "current_weak_signal_verified": False})

    bf26 = source("business_finland_signals_2026", "Business Finland", "Business Finland 2026",
                  "Signals of Change, Summer 2026", "https://www.businessfinland.fi/en/services/Advice-and-market-information/data-bank/publications/signals-of-change-summer-2026/",
                  2026, "2026-06-23", "horizon_scan",
                  "5 выбранных наблюдений о технологиях и условиях их развития. Отчёт в целом посвящён геополитике; это не пять подтверждённых научных слабых сигналов.",
                  pdf="business-finland-signals-summer-2026.pdf", pdf_url="https://www.businessfinland.fi/globalassets/julkaisut/signals-of-change-summer-2026.pdf",
                  license="Business Finland terms: non-commercial use with attribution; commercial use requires permission",
                  license_url="https://www.businessfinland.fi/en/Terms-of-Use/", id_prefix="bf-2026",
                  date_basis="publisher_page_last_updated", coverage_period="2026-03/2026-05")
    bf25 = source("business_finland_signals_2025", "Business Finland", "Business Finland 2025",
                  "Signals of Change, Summer 2025", "https://www.businessfinland.fi/en/services/Advice-and-market-information/data-bank/publications/signals-of-change-summer-2025/",
                  2025, "2025-09-01", "horizon_scan",
                  "3 технологически релевантных наблюдения из 9 разделов Signals of Change. Сценарии и методические главы не импортированы; фактические утверждения отчёта отдельно не подтверждены.",
                  pdf="business-finland-signals-summer-2025.pdf", pdf_url="https://www.businessfinland.fi/4a490a/globalassets/julkaisut/signals-of-change-summer-2025.pdf",
                  license=bf26["license"], license_url=bf26["license_url"], id_prefix="bf-2025",
                  date_basis="publisher_page_last_updated_not_original_release_date")
    eea = source("eea_eionet_horizon_scan_2024", "EEA / Eionet / ETC ST", "EEA–Eionet 2024",
                 "Horizon Scanning 2024, ETC ST Report 2024/5", EEA_URL, 2024, "2025-02-10", "horizon_scan",
                 "25 выбранных технологических наблюдений из подразделов 14 emerging issues. Сканирование проводилось в марте–августе 2024 года; публикация вышла в 2025 году.",
                 pdf="eea-eionet-horizon-scanning-2024.pdf", pdf_url=EEA_PDF, license="CC BY 4.0",
                 license_evidence="PDF page 2, copyright notice", id_prefix="eea-2024",
                 reported_total=14, reported_total_unit="broad_emerging_issues_not_imported_topic_count",
                 coverage_period="2024-03/2024-08", doi="10.5281/zenodo.14674165")
    wef = source("wef_emerging_technologies_2026", "World Economic Forum / Frontiers", "WEF 2026",
                 "Top 10 Emerging Technologies of 2026", WEF_URL, 2026, "2026-06-23", "technology_selection",
                 "10 перспективных технологий, выбранных экспертами. Это не перечень гарантированно слабых сигналов; сценарии Imagining 2031 не импортированы как реальные события.",
                 id_prefix="wef-2026", reader_url=WEF_READER, reported_total=10,
                 license="Copyright WEF. Only short factual topic labels and links; no full text, figures or scenarios copied.")
    korea = source("msit_kribb_biotechnologies_2025", "MSIT / KRIBB / KISTI, Republic of Korea", "MSIT–KRIBB 2025",
                   "Top 10 Promising Biotechnologies of 2025", KOREA_URL, 2025, "2025", "technology_selection",
                   "10 перспективных биотехнологий. Отбор сочетает экспертную оценку и KISTI Weak Signal model; опубликованных баллов модели нет. Точный день публикации на прочитанной странице не указан.",
                   id_prefix="kribb-2025", date_precision="year", date_basis="edition_year_only",
                   license="KOGL Type 1 (source indication)", license_url="https://www.kogl.or.kr/info/licenseType1.do", reported_total=10)

    new = []
    for number, (page, en, ru, anchor) in enumerate([
        (4, "AI chip trade controls", "Ограничения торговли ИИ-чипами", "RAPID HARDENING"),
        (7, "AI shipbuilding robots", "ИИ-роботы для судостроения", "JAPAN"),
        (10, "Economy-wide AI deployment", "Масштабирование ИИ во всей экономике", "AI OF EVERYTHING"),
        (13, "Critical minerals procurement platform", "Платформа закупок критических минералов", "RAW MATERIALS PLATFORM"),
        (15, "Chipmaking equipment export controls", "Экспортные ограничения оборудования для чипов", "MATCH ACT"),
    ], 1):
        new.append(record(bf26, number, en, ru, "Technology and geopolitics", page=page, anchor=anchor))
    for number, (page, en, ru, anchor) in enumerate([
        (5, "Critical mineral supply chains", "Цепочки поставок критических минералов", "Race for critical minerals"),
        (6, "Thorium energy in China", "Ториевая энергетика в Китае", "energy self"),
        (7, "Fragmented AI ecosystems", "Разделение мировых экосистем ИИ", "Tech War"),
    ], 1):
        new.append(record(bf25, number, en, ru, "Technology and geopolitics", page=page, anchor=anchor))
    # The source has fourteen umbrella issues. We import named subtopics,
    # not the umbrellas and their children together as artificial duplicates.
    eea_topics = [
        (14, "3.2.1", "AI waste recycling", "ИИ для переработки отходов", "AI in recycling"),
        (15, "3.2.2", "AI biodiversity protection", "ИИ для защиты биоразнообразия", "AI to safeguard biodiversity"),
        (15, "3.2.3", "AI resource consumption", "Ресурсоёмкость ИИ", "AI"),
        (18, "3.3.3", "Environmental digital visualisation", "Цифровая визуализация экологических данных", "digital visualisation"),
        (19, "3.4.1", "Deep-sea mining", "Глубоководная добыча", "deep sea mining"),
        (19, "3.4.2", "Space resource exploration", "Освоение космических ресурсов", "human efforts in space"),
        (20, "3.4.3", "Space debris risks", "Риски космического мусора", "Space debris"),
        (23, "3.6.1", "Airborne plastics and weather", "Влияние воздушного пластика на погоду", "Plastics in the air"),
        (24, "3.6.2", "Persistent PFAS pollution", "Устойчивое загрязнение PFAS", "PFAS"),
        (24, "3.6.3", "Plastic recycling toxicity", "Токсичность переработки пластика", "plastic recycling"),
        (26, "3.7.2", "AI warfare", "ИИ в военных системах", "AI driven evolution"),
        (27, "3.8.1", "Invasive species control", "Контроль инвазивных видов", "invasive species"),
        (27, "3.8.2", "Climate-adapted engineered plants", "Растения с адаптацией к климату", "Engineering plants"),
        (31, "3.10.1", "Synthetic biology", "Синтетическая биология", "Synthetic biology"),
        (32, "3.10.2", "Unilateral climate geoengineering", "Односторонняя климатическая геоинженерия", "geoengineering"),
        (32, "3.10.3", "Bio-inspired flapping-wing drones", "Биомиметические беспилотники с машущими крыльями", "Next generation of drones"),
        (33, "3.10.4", "Artificial reefs", "Искусственные рифы", "Artificial reefs"),
        (34, "3.11.2", "Demographic-driven automation", "Автоматизация при демографических изменениях", "Automation"),
        (35, "3.12.1", "Underwater agriculture", "Подводное сельское хозяйство", "underwater agriculture"),
        (36, "3.12.3", "Food by-product valorisation", "Использование пищевых и аграрных отходов", "food waste"),
        (37, "3.12.4", "Urban farming", "Городское сельское хозяйство", "urban farming"),
        (38, "3.13.2", "Water desalination", "Опреснение воды", "Desalination plants"),
        (40, "3.14.1", "Novel construction materials", "Новые строительные материалы", "materials for construction"),
        (40, "3.14.2", "Novel construction methods", "Новые методы строительства", "construction methods"),
        (41, "3.14.3", "Sustainable cooling", "Устойчивые решения охлаждения", "cooling solutions"),
    ]
    for number, (page, section, en, ru, anchor) in enumerate(eea_topics, 1):
        new.append(record(eea, number, en, ru, "Environment and technology", page=page,
                          section=section, anchor=anchor, category_basis="publisher_environmental_scan"))
    for number, (en, ru, category) in enumerate([
        ("Everything-to-grid energy", "Распределённая энергетика everything-to-grid", "Energy"),
        ("Direct lithium extraction", "Прямое извлечение лития", "Materials"),
        ("Passive radiative cooling", "Пассивное радиационное охлаждение", "Materials"),
        ("PFAS destruction", "Разрушение соединений PFAS", "Materials"),
        ("Precision fermentation", "Прецизионная ферментация", "Biotechnology"),
        ("Exosome drug delivery", "Доставка лекарств экзосомами", "Health"),
        ("Personalised mRNA cancer vaccines", "Персональные мРНК-вакцины против рака", "Health"),
        ("Quantum drug simulation", "Квантовое моделирование лекарств", "Health"),
        ("AI world models", "Модели мира в ИИ", "AI and Machine Learning"),
        ("Lattice cryptography", "Криптография на решётках", "Computing"),
    ], 1):
        item = record(wef, number, en, ru, category, section=f"Технология {number}")
        item["source_url"] += "#" + WEF_SECTIONS[number - 1]
        new.append(item)
    for number, (en, ru, category) in enumerate([
        ("Human immunome", "Иммуном человека", "Observation / Analysis"),
        ("Multi-cancer early detection", "Ранняя диагностика нескольких видов рака", "Observation / Analysis"),
        ("RNA structurome", "Структуром РНК", "Observation / Analysis"),
        ("AI-designed gene editors", "Генные редакторы, спроектированные ИИ", "Editing / Reprogramming"),
        ("Anti-aging antibodies", "Антитела против старения", "Editing / Reprogramming"),
        ("Molecular glue", "Молекулярные клеи", "Editing / Reprogramming"),
        ("Motile living biobots", "Подвижные живые биороботы", "Emulation / Synthesis"),
        ("Digital artificial organs", "Цифровые искусственные органы", "Emulation / Synthesis"),
        ("Bio foundation models", "Базовые модели для биологии", "Prediction / Simulation"),
        ("Healthcare digital twins", "Цифровые двойники для здоровья", "Prediction / Simulation"),
    ], 1):
        new.append(record(korea, number, en, ru, category, section=f"Таблица: технология {number}",
                          category_basis="publisher_category"))
    catalog["records"].extend(new)
    counts = Counter(row["source_id"] for row in catalog["records"])
    for document in [bf26, bf25, eea, wef, korea]:
        document.update({"extracted_total": counts[document["source_id"]], "active": True})
        catalog["source_documents"].append(document)
    catalog["link_only_sources"] = [
        {"title": "Sitra Weak Signals 2025 / IF*", "edition_year": 2025,
         "url": "https://www.sitra.fi/en/articles/weak-signals-2025-a-magazine-from-the-year-2046/",
         "reason_ru": "Ссылки доступны; содержание не перенесено из-за условий использования. Будущие сценарии журнала не являются наблюдёнными событиями."},
        *[{"title": f"WEF Top 10 Emerging Technologies {year}", "edition_year": year,
           "url": (f"https://www.weforum.org/publications/top-10-emerging-technologies-{year}/" if year == 2024
                   else f"https://www.weforum.org/publications/top-10-emerging-technologies-of-{year}/"),
           "reason_ru": "Дополнительный выпуск найден; пока только ссылка. В этой версии загружены темы выпуска 2026 года."}
          for year in (2025, 2024, 2023)],
    ]
    (DATA / "catalog.v6.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"new_records": len(new), "all_records_including_archive": len(catalog["records"]),
                      "active_records": sum(row["source_year"] >= 2023 for row in catalog["records"]),
                      "counts": counts}, ensure_ascii=False))


if __name__ == "__main__":
    main()
