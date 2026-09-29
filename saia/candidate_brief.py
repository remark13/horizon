"""Offline-readable card export. Read saved data only; never enrich on export."""
from __future__ import annotations

import html
import math
from datetime import datetime, timezone
from urllib.parse import urlsplit

from saia import candidate_analysis, candidate_external_links, card_presentation, score_expert, source_context, triage
from saia import scout_public_signals


DATE_NAMES = {"publication": "Публикация", "observed_by_aggregator": "Обнаружено в СМИ",
              "project_start": "Начало проекта", "contract_date": "Дата контракта",
              "patent_publication": "Публикация патента", "repository_creation": "Создание репозитория",
              "registry_first_posted": "Первая запись в реестре", "grant_award": "Грант выдан",
              "resource_publication": "Публикация набора данных или ПО"}
DATE_NAMES.update(investment_announcement_month="Месяц инвестиционного сообщения", unknown_publication_date="Дата публикации не предоставлена")
STATUS_NAMES = {"complete": "Материалы получены", "empty_observed_response": "Нет совпадений в ответе",
                "not_collected": "Источник не опрошен", "credentials_required": "Нужен ключ доступа",
                "rate_limited": "Лимит запросов", "http_error": "Ошибка доступа", "unavailable": "Недоступен"}
STATUS_NAMES.update(source_data_stale="Источник вернул устаревшую выборку", collection_ids_required="Нужно выбрать коллекции СМИ", request_deferred="Запрос отложен из-за лимита")


def esc(value: object) -> str:
    return html.escape(str(value if value is not None else "Не установлено"), quote=True)


def link(url: object, label: object) -> str:
    target = urlsplit(url) if isinstance(url, str) else None
    if target is None or target.scheme not in {"http", "https"} or not target.hostname or target.username is not None or target.password is not None:
        return esc(label)
    return f'<a href="{esc(url)}" rel="noopener noreferrer">{esc(label)}</a>'


def number(value: object, scale: float = 1, digits: int = 2) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        return "Не установлено"
    return f"{value * scale:.{digits}f}".replace(".", ",")


def percent(value: object, scale: float = 1, digits: int = 2) -> str:
    result = number(value, scale, digits)
    return result if result == "Не установлено" else result + "%"


def _hypothesis(row: dict, refs: list[dict]) -> str:
    selected = [r for r in refs if r["id"] in row["evidence_ids"]]
    support = " · ".join(link(r["url"], r["title"]) for r in selected) or "Без источников — гипотеза автора"
    return (f'<p><span class="tag">{esc(candidate_analysis.EFFECTS[row["effect"]])}</span> '
            f'{esc(candidate_analysis.HORIZONS[row["horizon"]])}</p><p>{esc(row["text"])}</p>'
            f'<p class="small">Условия реализации: {esc(row["dependencies"] or "Не описаны")}</p>'
            f'<p class="small">Основания: {support}</p>')


def _external_material(material: dict) -> str:
    content = [f'<article><p>{link(material["url"], material["title_original"])}</p>'
               f'<p class="small">{esc(material.get("source_name", "Источник"))} · '
               f'{esc(DATE_NAMES.get(material["date_kind"], "Дата источника"))}: {esc(material["record_date"])} · '
               f'Получено: {esc(material["retrieved_at"])} · Найдено автоматически, связь не проверена</p>']
    if material.get("author"):
        content.append(f'<p class="small">Авторы: {esc(material["author"])}</p>')
    content.append(f'<p class="small">Тип: {esc(material.get("record_type"))} · Оригинальный язык: {esc(material.get("language_original"))}. {esc(material.get("trust_comment", ""))}</p>')
    if material.get("original_record_url_available") is False:
        content.append('<p class="small">Ссылка ведёт на агрегатор, не на первичную запись сделки. Сообщение не проверено.</p>')
    if material.get("reported_amount_text"):
        content.append(f'<p class="small">Сумма в ответе источника: {esc(material["reported_amount_text"])}. Не проверена и не суммируется.</p>')
    publication_type = {"journal_article": "Журнальная статья", "research_report": "Научно-технический отчёт",
                        "conference_publication": "Конференционный материал", "research_thesis": "Диссертация",
                        "research_book": "Научная книга"}.get(material.get("record_type"))
    if publication_type:
        content.append(f'<p class="small">{esc(publication_type)}. Рецензирование этой записью не подтверждено.</p>')
    if material.get("usage_scope") == "personal_research":
        content.append('<p class="small">Личный исследовательский просмотр. Оригинальный японский заголовок; не разрешение на публичное переиздание.</p>')
        terms = material.get("query_match_terms")
        if isinstance(terms, list):
            aliases = list(dict.fromkeys(value for item in terms if isinstance(item, dict)
                          and isinstance(item.get("alternatives"), list)
                          for value in item["alternatives"] if isinstance(value, str)))
            content.append(f'<p class="small">Слова поиска: {esc(", ".join(aliases[:12]))}. Смысловая связь не подтверждена.</p>')
    basis = {"reported_obligation_not_paid_expenditure": "Обязательства по гранту",
             "reported_project_funding_not_paid_expenditure": "Заявленное финансирование проекта"}.get(material.get("funding_amount_basis"))
    value = number(material.get("funding_amount"))
    currency = material.get("funding_currency")
    if basis and value != "Не установлено" and isinstance(currency, str) and len(currency) == 3 and currency.isascii() and currency.isalpha() and currency.isupper():
        content.append(f'<p class="small">{esc(basis)}: {value} {esc(currency)}. Не выплаченные расходы и не частная инвестиция.</p>')
    if material.get("date_precision") == "year":
        content.append('<p class="small">Источник указал только год; точная дата не известна.</p>')
    if material.get("project_start_planned") is True:
        content.append(f'<p class="small">Начало работ запланировано: {esc(material.get("project_start_date"))}.</p>')
    content.append('</article>')
    return "".join(content)


def _merge_reason(proof: dict) -> str:
    if proof.get("kind") == "declared_doi_relation":
        relations = {"IsVersionOf": "версия ресурса", "HasVersion": "имеет версию", "IsNewVersionOf": "новая версия",
                     "IsPreviousVersionOf": "предыдущая версия", "IsIdenticalTo": "идентичный ресурс"}
        return f'В метаданных DataCite указано: {relations.get(proof.get("relation"), "связь версий")} · {proof.get("target")}.'
    if proof.get("kind") == "exact_identifier":
        agency = {"grant:US:NSF": "NSF", "grant:GB:UKRI": "UKRI", "grant:EU:EC": "Еврокомиссия"}.get(proof.get("scheme"))
        return f'Совпадает номер гранта и финансирующая организация: {agency} {proof.get("value")}.' if agency else 'Совпадает точный идентификатор записи.'
    return 'Совпадает точная исходная ссылка.'


def render(packet: dict) -> str:
    card, screening = packet["card"], packet["screening"]
    presentation = packet.get("presentation")
    note = packet.get("analysis_note")
    title = presentation["content"]["title_ru"] if presentation else card["label"]
    parts = [f'<header><div class="brand">Газпромбанк · Horizon</div><h1>{esc(title)}</h1>'
             f'<p>{esc(screening.get("label", "Сохранённая исследовательская тема"))}</p>'
             f'<p class="small">Выгрузка: {esc(packet["exported_at"])} · Карточка {card["candidate_id"]}</p></header>',
             '<p class="warning">Автоматический кандидат, не прогноз успеха и не экспертно подтверждённый сигнал. '
             'Материалы из разных источников и авторские гипотезы приведены раздельно.</p>',
             f'<section><h2>Научные основания</h2><p>{esc(screening.get("explanation", ""))}</p>'
             f'<dl><dt>Полнота оснований, не вероятность</dt><dd>{percent(card.get("evidence_confidence"), digits=0)}</dd>'
             f'<dt>Первая работа в выборке, не дата рождения технологии</dt><dd>{esc(card.get("research_birth") or card.get("first_found"))}</dd></dl></section>']
    if presentation:
        parts.append('<section><h2>Русское машинное пояснение</h2><p class="warning">Машинный черновик: термины и выводы могут быть неточными.</p>')
        evidence = {e["id"]: e for e in presentation["evidence"]}
        for key, label in (("description", "Суть"), ("problem", "Какую проблему решает"), ("advantage", "В чём преимущество"), ("case", "Пример исследования")):
            claim = presentation["content"][key]
            refs = []
            for identifier in claim["evidence_ids"] if claim else []:
                source = evidence[identifier]
                urls = source.get("sources") or []
                refs.append(link(urls[0].get("url") if urls else None, f"[{identifier}] {source['title']}"))
            parts.append(f'<h3>{label}</h3><p>{esc(claim["text"] if claim else "В выбранных фрагментах не установлено")}</p><p class="small">{" · ".join(refs)}</p>')
        parts.append(f'<p class="small">Модель: {esc(presentation["model"])}. Смысловая верность пересказа не проверена.</p></section>')
    else:
        parts.append('<section><h2>Русское пояснение</h2><p>Пока не подготовлено. Выгрузка не запускает модель автоматически.</p></section>')
    if packet.get("assessment"):
        parts.append('<section><h2>Объяснимая оценка и динамика</h2>' + packet["assessment"]["visualization_html"] + '</section>')
    if packet.get('published_references'):
        parts.append('<section><h2>Также опубликовано во внешних подборках</h2><p class="warning">Из публичного источника. Совпало название; это не добавляет баллы и не подтверждает актуальный статус слабого сигнала.</p>')
        for reference in packet['published_references']:
            for source in reference['references']:
                parts.append('<article><p>' + link(source['source_url'], source['source_label']) + '</p><p class="small">Год подборки: ' + esc(source.get('source_year')) + ' · ' + esc(source.get('type_label', 'Публичная подборка')) + ' · ' + esc(('стр. ' + str(source['source_page'])) if source.get('source_page') is not None else source.get('source_section', 'Раздел на сайте')) + '</p></article>')
        parts.append('</section>')
    series = (card.get("metrics") or {}).get("publication_series") or {}
    comparable = series.get("coverage_comparable") is True and series.get("window_scale_comparable") is True
    slope = number(series.get("share_slope_per_window"), 100) + " п.п./период" if comparable and series.get("share_slope_per_window") is not None else "Не установлено"
    parts.append(f'<section><h2>Динамика публикаций</h2><p>Изменение доли: {slope}.</p><p class="small">Доли относятся только к собранной выборке по запросу, не ко всей мировой науке или рынку. Неполные периоды не используются для сравнения.</p><table><thead><tr><th>Период</th><th>Работ по теме</th><th>Всего в выборке</th><th>Доля</th><th>Полный период</th></tr></thead><tbody>')
    for row in (series.get("points") or [])[-12:]:
        parts.append(f'<tr><td>{esc(row.get("start"))} — {esc(row.get("end"))}</td><td>{esc(row.get("topic_works"))}</td><td>{esc(row.get("corpus_works"))}</td><td>{percent(row.get("share"),100)}</td><td>{"Да" if row.get("complete") is True else "Нет"}</td></tr>')
    parts.append('</tbody></table><p class="small">Показаны последние 12 доступных периодов; неизвестные значения не заменены нулём.</p></section>')
    parts.append('<section><h2>Исходные научные публикации</h2><p class="small">Опорные статьи карточки, не полный список темы. Первичность и рецензирование отдельно не подтверждены.</p>')
    for item in card.get("evidence") or []:
        sources = " · ".join(link(s.get("url"), s.get("type") or "Источник") for s in item.get("sources") or [])
        languages = sorted({str(s["language"]) for s in item.get("sources") or [] if s.get("language")})
        parts.append(f'<article><h3>{esc(item["title"])}</h3><p class="small">{esc(item.get("published_at"))} · {sources}</p>'
                     f'<p class="small">Оригинальный язык: {esc(", ".join(languages) or "не указан в сохранённых метаданных")}. '
                     'Тип: библиографическая запись; первичность исследования и рецензирование отдельно не подтверждены.</p></article>')
    parts.append('</section>')
    context = packet["context"]
    grouping = context.get("material_grouping")
    parts.append(f'<section><h2>Дополнительные источники</h2><p class="warning">Текущий контекст на {esc(context["binding"]["retrieval_date"])}. Это поисковые совпадения; они не подтверждают связь с сигналом и не входят в научный рейтинг.</p><p>Фраза: {esc(context["binding"]["query"])}</p>')
    for report in context["reports"]:
        parts.append(f'<h3>{esc(report["name"])}</h3><p class="small">{esc(STATUS_NAMES.get(report["status"], "Ответ с ограничением"))}. {esc(report.get("trust_comment", ""))}</p>')
        if not report["materials"]:
            parts.append('<p class="small">Материалов в этом ответе нет. Это не доказательство отсутствия активности.</p>')
        if not grouping:
            parts.extend(_external_material(m) for m in report["materials"])
    if grouping:
        parts.append(f'<p>После объединения повторов: {grouping["display_group_count"]} материалов из {grouping["raw_record_count"]} исходных записей. '
                     'Это не число независимых подтверждений. Даты и суммы не складываются.</p>')
        for group_name, label in source_context.GROUP_TITLES.items():
            groups = [g for g in grouping["groups"] if g["group"] == group_name]
            if not groups:
                continue
            parts.append(f'<h3>{esc(label)}</h3>')
            for group in groups:
                parts.append(_external_material(group["primary"]))
                if group["status"] == "identity_conflict_not_merged":
                    parts.append('<p class="small">Идентификаторы или типы противоречат друг другу; записи не объединены.</p>')
                if group["record_count"] > 1:
                    parts.append(f'<details><summary>Другие записи и основание объединения ({group["record_count"] - 1})</summary>'
                                 '<p class="small">Это версии или повторные сведения, не независимые подтверждения сигнала.</p>')
                    parts.extend(f'<p class="small">{esc(_merge_reason(proof))}</p>' for proof in group["merge_evidence"])
                    parts.extend(_external_material(m) for m in group["records"][1:])
                    parts.append('</details>')
    if not any(r.get("group") == "investment" for r in context["reports"]):
        parts.append('<h3>Частные инвестиционные сделки</h3><p>Проверенный источник пока не подключён. Гранты и госконтракты не являются частными инвестициями.</p>')
    parts.append('</section>')
    content = note["content"] if note else {"summary": "", "pestle": [], "industry_impacts": [], "references": []}
    parts.append('<section><h2>PESTLE: возможные последствия</h2><p class="warning">Гипотезы скаута, не установленные последствия. Наличие ссылки не доказывает верность интерпретации.</p>')
    if note:
        parts.append(f'<p class="small">Автор: {esc(content["author"])} · Версия {note["revision"]} · {esc(note["created_at"])}. Имя указано пользователем, идентификация не подтверждена.</p>')
    for key, label in candidate_analysis.DIMENSIONS.items():
        row = next((r for r in content["pestle"] if r["dimension"] == key), None)
        parts.append(f'<h3>{esc(label)}</h3>' + (_hypothesis(row, content["references"]) if row else '<p class="small">Не проработано.</p>'))
    parts.append('</section><section><h2>Возможное влияние на отрасли</h2>')
    for row in content["industry_impacts"]:
        parts.append(f'<h3>{esc(row["industry"])}</h3>' + _hypothesis(row, content["references"]))
    if not content["industry_impacts"]:
        parts.append('<p>Не проработано. Влияние не выведено автоматически из количества публикаций.</p>')
    parts.append(f'</section><section><h2>Вывод скаута</h2><p>{esc(content["summary"] or "Не подготовлен.")}</p></section>')
    reviews = packet.get("reviews", {}).get("reviews", [])
    parts.append('<section><h2>Экспертные мнения</h2><p class="small">Экспертиза необязательна. Приведённые мнения не означают консенсус; личности и квалификация не подтверждены системой.</p>')
    for review in reviews:
        decision = {"research_line_supported": "Исследовательская линия подтверждается", "noise": "Шум", "possible_duplicate": "Возможный дубль", "insufficient_evidence": "Недостаточно данных", "needs_review": "Нужна проверка"}.get(review["decision"], review["decision"])
        parts.append(f'<article><h3>{esc(decision)}</h3><p>{esc(review["rationale"])}</p><p class="small">{esc(review["reviewed_by"])} · {esc(review["created_at"])} · {"Совпадает с текущей карточкой" if review["matches_current_card"] else "Относится к тому же составу, но другой версии карточки"}</p></article>')
    if not reviews:
        parts.append('<p>Мнения пока не сохранены.</p>')
    parts.append(f'</section><footer>Миссия: {esc(packet["mission_id"])} · Прогон: {packet["score_run_id"]}<br>Состав: {esc(card["composition_sha256"])}<br>Выгрузка не пересчитывает ядро и не запрашивает внешние источники. Для PDF используйте печать браузера.</footer>')
    css = """body{font:15px/1.55 Arial,sans-serif;color:#202b43;background:#f5f7fb;margin:0}main{max-width:1000px;margin:32px auto;padding:32px;background:#fff;border-radius:16px}.brand{color:#3156ad;font-weight:bold}h1{line-height:1.25;font-size:28px}h2{font-size:21px;color:#233f89}h3{font-size:16px}section{border-top:1px solid #dfe5ef;margin-top:25px;padding-top:10px}a{color:#3156ad;overflow-wrap:anywhere}.warning{padding:12px 16px;background:#f3f6fc;border-left:3px solid #3156ad}.small,footer{font-size:12px;color:#68758d}article{margin:12px 0;padding-bottom:8px;border-bottom:1px solid #eee}table{border-collapse:collapse;width:100%;font-size:12px}td,th{padding:7px;text-align:left;border:1px solid #dfe5ef}dt{font-size:12px;color:#68758d}dd{margin:0 0 12px;font-weight:bold}.tag{background:#edf2fc;padding:3px 7px;border-radius:7px}footer{margin-top:30px;overflow-wrap:anywhere}@media(max-width:650px){main{margin:0;padding:18px}table{font-size:10px}td,th{padding:4px}}@media print{body{background:#fff}main{margin:0;padding:0;max-width:none}h2,h3{break-after:avoid}article,tr{break-inside:avoid}a{color:#233f89}}"""
    from saia.scout_assessment_web import ASSESSMENT_STYLE
    tokens = ':root{--blue-dark:#233f89;--muted:#68758d;--ink:#202b43;--soft:#f3f6fc;--line:#dfe5ef}'
    return f'<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Horizon — {esc(title)}</title><style>{tokens}{css}{ASSESSMENT_STYLE}</style></head><body><main>{"".join(parts)}</main></body></html>'


def build(mission: str, score: int, candidate: int, query: str | None = None, sources: list[str] | None = None) -> str:
    card = candidate_external_links._candidate(mission, score, candidate)
    queue = triage.build_queue({"cards": [card]}, 1)["queue"]
    screening = queue[0]["screening"] if queue else {}
    # A display filter must never silently recompute a different exported score.
    if query is not None or sources is not None:
        source_context.read(mission, score, candidate, query, sources)
    context = source_context.read_saved_all(mission, score, candidate, card)
    from saia.candidate_assessment import calculate
    assessment = calculate(card, context, candidate_external_links.for_candidate(mission, score, candidate))
    return render({"mission_id": mission, "score_run_id": score, "card": card, "screening": screening, "assessment": assessment,
                   "presentation": card_presentation.read(mission, score, candidate)["presentation"],
                   "context": context,
                   "published_references": scout_public_signals.for_card(mission, score, card),
                   "analysis_note": candidate_analysis.read(mission, score, candidate)["note"],
                   "reviews": score_expert.history(mission, candidate, score, 20),
                   "exported_at": datetime.now(timezone.utc).isoformat()})
