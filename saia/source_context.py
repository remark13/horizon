"""Bounded external search context for an exact saved candidate, not a new score.

Automatic matches, analyst-selected links and expert validation remain three
different entities. Context queries are bound to the candidate composition and
persist through the existing append-only observation store.
"""
from __future__ import annotations

import importlib
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from saia import candidate_external_links, db, external_evidence_store, external_material_groups
from saia.external_sources import load_policy
from saia.hybrid import digest
from saia.source_registry import load_registry, validate_runtime_sources
from saia.news_publishers import publishers, connection_checks, reader_flags, USE_POLICY_RU

VERSION = "candidate-source-context-v1"
REGISTRY_PATH = Path(__file__).resolve().parents[1] / "config/source-adoption.v0.4.51.yaml"
DEFAULT_SOURCES = ("google_news_rss", "gdelt_doc_2_0", "dealroom_marketmaps", "dealroom_public_rounds", "epo_ops", "ukri_gtr")
MAX_SOURCES = 8
PER_REQUEST_TIMEOUT = 12.0
MAX_PARALLEL = 3
CACHE_HOURS = 24
ERROR_CACHE_MINUTES = 5
_COLLECTION_SLOTS = threading.BoundedSemaphore(2)

# Titles are user-facing. Taxonomy is about evidence type, not inferred impact.
SOURCE_INFO = {
    "gdelt_doc_2_0": ("GDELT — новости и СМИ", "commercial", "Агрегатор СМИ; дата обнаружения не равна дате публикации."),
    "mit_news_rss": ("MIT News", "commercial", "Сообщение пресс-службы университета; требует проверки первичного исследования."),
    "nasa_news_rss": ("NASA — технологии", "commercial", "Официальное сообщение организации; не независимое подтверждение рынка."),
    "jpl_news_rss": ("NASA JPL", "commercial", "Официальное сообщение JPL; NASA и JPL не независимые источники."),
    "epo_ops": ("EPO — патенты", "patents", "Патентная публикация; не доказательство внедрения или действующего права."),
    "nih_reporter": ("NIH RePORTER", "funding", "Исследовательский грант; не частная инвестиционная сделка."),
    "ukri_gtr": ("UKRI — исследовательские гранты", "funding", "Грант UKRI; не венчурная инвестиция и не подтверждение результата."),
    "eu_funding_tenders": ("Еврокомиссия — программы", "funding", "Программа или конкурс, не состоявшаяся инвестиционная сделка."),
    "usaspending": ("USAspending — госконтракты", "funding", "Федеральный контракт США; не частная инвестиция и не размер рынка."),
    "huggingface_hub": ("Hugging Face — модели и датасеты", "software", "Публикация артефакта; скачивания не подтверждают качество или внедрение."),
    "deps_dev": ("deps.dev — программные пакеты", "software", "Требуется точное имя пакета; название технологии не подменяет его."),
    "semantic_scholar": ("Semantic Scholar", "science_extra", "Библиографические метаданные; не дополнительное независимое исследование."),
    "europe_pmc": ("Europe PMC", "science_extra", "Библиографическая запись; первичность и рецензирование проверяются отдельно."),
    "nasa_ntrs": ("NASA NTRS — исследования", "science_extra", "Научно-техническая запись NASA; возможен отчёт, а не рецензируемая статья."),
    "clinicaltrials_gov": ("ClinicalTrials.gov", "clinical", "Регистрация исследования; не доказательство эффективности или одобрения препарата."),
    "nsf_awards": ("NSF — исследовательские гранты", "funding", "Выданный грант NSF; не частная инвестиция. Начало работ может быть запланировано на будущее."),
    "openaire_projects": ("OpenAIRE — финансируемые проекты", "funding", "Может повторять гранты других источников. Не дополнительное независимое подтверждение и не доказательство внедрения."),
    "datacite": ("DataCite — исследовательские данные и ПО", "software", "Опубликованный исследовательский артефакт, не доказательство внедрения. Версии одного набора не независимые события."),
    "osti_gov": ("OSTI — исследования по энергетике и материалам", "science_extra", "Научно-техническая запись; возможен отчёт или конференционный материал. Не независимое подтверждение и не доказательство внедрения."),
    "nist_news_rss": ("NIST — технологии, производство и стандарты", "commercial", "Официальное сообщение NIST. Стандарт, программа или исследование не означают промышленное внедрение."),
    "aist_press_rss": ("AIST — исследования Японии", "commercial", "Официальное сообщение об исследовании, не независимая проверка или внедрение. Личный просмотр; японский оригинал."),
    "dealroom_marketmaps": ("Dealroom — подборки компаний", "commercial", "Подборка компаний, не сделки и не размер рынка. Дата публикации не предоставлена."),
    "dealroom_public_rounds": ("Dealroom — инвестиционные сообщения", "investment", "Непроверенные сообщения из небольшой публичной выборки. Возможен устаревший ответ; первичная ссылка на сделку отсутствует."),
    "event_registry": ("Event Registry — агрегатор СМИ", "commercial", "Сообщение СМИ; событие и независимость источников требуют проверки."),
    "mediacloud_news": ("Media Cloud — архив СМИ", "commercial", "Выборка из выбранных коллекций СМИ, не полный массив и не число всех публикаций."),
    "lens_patents": ("Lens — патентный агрегатор", "patents", "Патентная публикация; не доказательство внедрения или действующего патентного права."),
    "google_news_rss": ("Google News — агрегатор СМИ", "commercial", "Новостная поисковая лента. Дата указана агрегатором; ссылка открывается через Google News, не проверенный первоисточник."),
}
ADAPTERS = {
    "gdelt_doc_2_0": ("news_evidence", "NewsQuery"),
    "epo_ops": ("patent_evidence", "PatentQuery"),
    "nih_reporter": ("funding_evidence", "FundingQuery"),
    "ukri_gtr": ("ukri_evidence", "UKRIQuery"),
    "eu_funding_tenders": ("eu_programme_evidence", "EUProgrammeQuery"),
    "usaspending": ("usaspending_evidence", "USAspendingQuery"),
    "huggingface_hub": ("huggingface_evidence", "HuggingFaceQuery"),
    "semantic_scholar": ("scholar_evidence", "ScholarQuery"),
    "europe_pmc": ("europe_pmc_evidence", "EuropePmcQuery"),
    "nasa_ntrs": ("nasa_ntrs_evidence", "NasaNtrsQuery"),
    "clinicaltrials_gov": ("clinical_trial_evidence", "ClinicalTrialQuery"),
    "nsf_awards": ("nsf_evidence", "NSFQuery"),
    "openaire_projects": ("openaire_project_evidence", "OpenAIREProjectQuery"),
    "datacite": ("datacite_evidence", "DataCiteQuery"),
    "osti_gov": ("osti_evidence", "OSTIQuery"),
    **{key: ("rss_evidence", "RSSQuery") for key in ("mit_news_rss", "nasa_news_rss", "jpl_news_rss", "nist_news_rss", "aist_press_rss")},
}
GROUP_TITLES = {"commercial": "Новости и коммерческие публикации", "patents": "Патенты",
                "funding": "Гранты, программы и госконтракты", "software": "Модели, данные и ПО",
                "science_extra": "Дополнительные научные источники", "clinical": "Клинические исследования",
                "investment": "Инвестиционные сообщения"}
for _source in ("dealroom_marketmaps", "dealroom_public_rounds", "event_registry", "mediacloud_news", "lens_patents", "google_news_rss"):
    ADAPTERS[_source] = ("aggregator_evidence", "AggregatorQuery")
for _identifier, _publisher in publishers().items():
    SOURCE_INFO[_identifier] = (_publisher["name"], "commercial", _publisher["trust_comment_ru"])
    ADAPTERS[_identifier] = ("aggregator_evidence", "AggregatorQuery")

# Human-reviewed Russian display text. Original registry statements remain in
# the API for audit; this is not a model-generated licence interpretation.
SOURCE_PASSPORT_RU = {
    "gdelt_doc_2_0": ("Международные СМИ; полнота зависит от языка и состава индексируемых сайтов.", "Сохраняем заголовки, даты обнаружения и ссылки. Права на тексты СМИ остаются у издателей."),
    "mit_news_rss": ("Новости MIT, преимущественно США; не география всех участников исследования.", "Сохраняем заголовки, авторство, даты и исходные ссылки с указанием MIT News. Полные тексты и изображения не копируем."),
    "nasa_news_rss": ("Сообщения NASA, США; тематика может быть международной.", "Используем метаданные официальной ленты и ссылки. Чужие тексты, изображения и права на использование бренда не включены."),
    "jpl_news_rss": ("Сообщения NASA JPL, США; JPL и NASA относятся к одной организации-источнику.", "Только метаданные и ссылки. Не предполагаем свободного права на полные тексты и изображения JPL, Caltech или третьих лиц."),
    "epo_ops": ("Патентные библиографические записи из разных стран; покрытие и задержки зависят от ведомства.", "Доступ по условиям EPO OPS и ключу учётной записи. Сохраняем ограниченные метаданные и ссылки, не патентные массивы целиком."),
    "nih_reporter": ("NIH и участвующие федеральные агентства США; не мировой рынок финансирования.", "Открытые метаданные исследовательских грантов. Права на связанные сторонние тексты не расширяются."),
    "ukri_gtr": ("Исследовательское финансирование Великобритании; не глобальное покрытие.", "Данные Gateway to Research доступны по Open Government Licence. Указываем источник и сохраняем исходные ссылки."),
    "eu_funding_tenders": ("Еврокомиссия и участвующие программы ЕС.", "Открытые метаданные программ; связанные документы имеют собственные условия использования."),
    "usaspending": ("Федеральные контракты США; не частные инвестиции.", "Сохраняем публичные метаданные API и ссылки. Право на массовую перепубликацию сторонних описаний отдельно не подтверждено."),
    "huggingface_hub": ("Международное открытое сообщество ИИ; есть языковое и платформенное смещение.", "Лицензии зависят от репозитория. Сохраняем только метаданные и ссылки, не веса моделей и не содержимое датасетов."),
    "deps_dev": ("Международные открытые реестры программных пакетов.", "Данные deps.dev — CC BY 4.0; исходные пакеты и репозитории сохраняют собственные условия."),
    "semantic_scholar": ("Международные публикации; дисциплины и языки представлены неравномерно.", "Применяются условия Semantic Scholar. Коннектор не копирует полные тексты статей."),
    "europe_pmc": ("Международные исследования в медицине и биологии; не все научные области.", "Метаданные общедоступны; лицензии полных текстов различаются. Коннектор не копирует полные статьи."),
    "nasa_ntrs": ("Исследования, созданные или профинансированные NASA; выраженный уклон в авиацию и космос.", "Метаданные публичны. Авторские права на полные документы различаются; полные тексты не копируем."),
    "clinicaltrials_gov": ("Реестр США с международными исследованиями; не все клинические испытания мира.", "Используем ограниченные публичные метаданные, идентификаторы и ссылки по правилам реестра."),
    "nsf_awards": ("Исследовательские гранты NSF, преимущественно США. Страна получателя не равна распространению технологии.", "Только публичные метаданные грантов с указанием NSF и исходными ссылками. Контакты, аннотации, тексты и изображения не сохраняем."),
    "openaire_projects": ("Международные финансируемые исследования, с сильным покрытием Европы. Страны участников не означают промышленное внедрение.", "Метаданные OpenAIRE — по условиям CC-BY с указанием источника. Сохраняем идентификаторы грантов и финансирующие организации; повторные записи не считаем независимыми свидетельствами."),
    "datacite": ("Международные исследовательские репозитории. Страна издателя не выводится автоматически из названия платформы.", "Метаданные DataCite доступны по CC0. Файлы наборов данных и программ не скачиваем; их собственные лицензии проверяются отдельно."),
    "osti_gov": ("Исследования, связанные с финансированием DOE; сильное покрытие США. Страна публикации не равна стране авторов или внедрения.", "Только ограниченные библиографические сведения и ссылки с указанием OSTI. Полные тексты, аннотации и файлы не копируем. Использование корпуса для ИИ/ML/LLM и массовый сбор требуют отдельного согласования; этот канал не подаёт данные модели и не меняет рейтинг."),
    "nist_news_rss": ("Официальные сообщения NIST, США; происхождение пресс-релиза не означает географию всех участников или потребителей технологии.", "Сохраняем заголовок, авторство, дату, категории и ссылку с указанием NIST. Полные тексты и изображения не копируем; материалы с отдельными авторскими правами не считаем свободно лицензированными."),
    "aist_press_rss": ("Официальные исследования AIST, Япония; не вся азиатская наука и не распространение технологии.", "Личный локальный RSS-просмотр: оригинальные заголовки, даты и ссылки с указанием AIST. Полные статьи и изображения не копируем, в модель не передаём. Для публичного или коммерческого сервиса отдельно проверить условия и уведомление AIST."),
    "dealroom_marketmaps": ("Международные технологические подборки; покрытие неравномерное.", "Личные метаданные и ссылки с указанием Dealroom. Заявленное число компаний не означает, что их записи загружены. Права на публичную перепубликацию не установлены."),
    "dealroom_public_rounds": ("Небольшая публичная международная выборка, не все мировые сделки.", "Сохраняем сообщения с точностью даты до месяца и суммой в исходном виде. Первичная ссылка и проверка сделки отсутствуют. Не используем для суммы инвестиций или научного рейтинга."),
    "event_registry": ("Международные многоязычные СМИ; зависит от доступа учётной записи.", "Только заголовки, даты и ссылки по условиям API и издателей. Нужен ключ Event Registry; это не NewsAPI.org. Полные статьи не копируем."),
    "mediacloud_news": ("География и языки явно выбранных коллекций Media Cloud.", "Ключ и номера коллекций обязательны. Загружаем ограниченную выборку метаданных, не полные статьи и не весь архив."),
    "lens_patents": ("Международные патентные библиографические записи; полнота зависит от ведомства.", "Нужны токен Lens и разрешённый доступ к Patent API. Ограниченные метаданные, без формул изобретения и полного текста. Семейство не считаем несколькими независимыми технологиями."),
    "google_news_rss": ("Международные СМИ в выбранном поисковом языке и регионе Google News; не полный новостной корпус.", "Личный просмотр заголовков, дат и ссылок поисковой RSS-ленты. Ссылка идёт через Google News. Полные статьи и изображения не копируем; публичная перепубликация не разрешается этим коннектором."),
}
SOURCE_PASSPORT_RU.update({identifier: (row["regional_coverage_ru"], USE_POLICY_RU)
                          for identifier, row in publishers().items()})


def default_query(card: dict) -> str:
    """Reuse visible label terms; no LLM, hidden topic assertion or guessed name."""
    # Keep the qualifier: dropping "manipulation" from a reinforcement-learning
    # card retrieves unrelated news and makes the later relevance check futile.
    label = str(card.get("label") or "")
    parts = re.findall(r"[\w-]+", label, re.UNICODE)
    terms = [word for word in parts if word.casefold() not in {"and", "or", "the", "тема", "по", "запросу"}]
    return " ".join(terms[:6])[:160].strip()


def validate_query(text: str) -> str:
    # Plain search words only: connectors have differing operator languages.
    if not isinstance(text, str) or not 2 <= len(text.strip()) <= 160:
        raise ValueError("Укажите поисковую фразу от 2 до 160 символов.")
    if any(ord(char) < 32 for char in text) or not re.fullmatch(r"[\w\s\-]+", text, re.UNICODE):
        raise ValueError("В поисковой фразе допустимы слова, пробелы и дефисы.")
    return " ".join(text.split())


def validate_sources(sources: list[str] | tuple[str, ...]) -> list[str]:
    if not isinstance(sources, (list, tuple)) or not 1 <= len(sources) <= MAX_SOURCES:
        raise ValueError(f"Выберите от 1 до {MAX_SOURCES} источников.")
    if any(not isinstance(value, str) or value not in ADAPTERS for value in sources):
        raise ValueError("Источник недоступен для поиска по технологической фразе.")
    if len(sources) != len(set(sources)):
        raise ValueError("Источники не должны повторяться.")
    return list(sources)


def binding(mission: str, score: int, candidate: int, card: dict, query: str,
            today: date | None = None) -> dict:
    current = today or datetime.now(timezone.utc).date()
    return {"version": VERSION, "mission_id": mission, "score_run_id": score,
            "candidate_id": candidate, "composition_sha256": card["composition_sha256"],
            "query": validate_query(query), "retrieval_date": current.isoformat(),
            "context_role": "automatic_search_match_not_verified_signal_evidence",
            "changes_scientific_metrics": False}


def topic_key(scope: dict) -> str:
    return "candidate-context:" + digest(scope)


def _last_observations() -> dict:
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT ON(source) source,status,created_at FROM external_evidence_observation "
                    "ORDER BY source,created_at DESC,observation_id DESC")
        return {source: {"status": status, "retrieved_at": created.isoformat()} for source, status, created in cur.fetchall()}


def catalog() -> dict:
    policy = load_policy()
    registry = load_registry(REGISTRY_PATH)
    gate = validate_runtime_sources(registry, policy)
    registered = {item["id"]: item for item in registry["sources"]}
    try:
        last = _last_observations()
        history_available = True
    except (db.DatabaseError, OSError):
        last, history_available = {}, False
    checks = connection_checks()
    entries = []
    for source, (name, group, trust) in SOURCE_INFO.items():
        item = registered[source]
        needs_key = source == "epo_ops" and not all(
            os.environ.get(key) for key in ("EPO_OPS_CONSUMER_KEY", "EPO_OPS_CONSUMER_SECRET"))
        from saia.aggregator_evidence import CHANNELS, configuration_status
        config_status = configuration_status(source) if source in CHANNELS else "credentials_required" if needs_key else "exact_package_required" if source == "deps_dev" else "requestable"
        publisher = publishers().get(source)
        entries.append({"source": source, "name": name, "group": group,
                        "group_title": GROUP_TITLES[group], "query_supported": source in ADAPTERS,
                        "configuration_status": config_status,
                        "default_selected": source in DEFAULT_SOURCES, "last_observation": last.get(source),
                        "last_connection_check": checks.get(source),
                        "publisher_domain": publisher["domain"] if publisher else None,
                        "publisher_backend": "google_news_rss" if publisher else None,
                        "publisher_category": publisher["category"] if publisher else None,
                        "publisher_category_title": publisher["category_label_ru"] if publisher else None,
                        "source_kind": publisher["source_kind"] if publisher else None,
                        "trust_comment": trust, "access": item["access"],
                        "license_status": item["license_status"], "credentials": item["credentials"],
                        "regional_coverage": item["regional_coverage"], "role": item["role"],
                        "regional_coverage_ru": SOURCE_PASSPORT_RU[source][0],
                        "use_policy_ru": SOURCE_PASSPORT_RU[source][1],
                        "evidence_urls": item.get("evidence_urls", []), "scientific_score_modified": False})
    return {"version": VERSION, "sources": entries, "max_selected": MAX_SOURCES,
            "publisher_channels": len(publishers()), "independent_aggregators_added": 0,
            "scientific_sources": [{"source": "openalex", "name": "OpenAlex", "role": "scientific_corpus"},
                                   {"source": "arxiv", "name": "arXiv и локальный архив", "role": "scientific_corpus"}],
            "runtime_gate": gate, "history_available": history_available,
            "investment_deals": {"status": "limited_unverified_sample", "comment": "Подключена небольшая публичная выборка сообщений Dealroom, не проверенная база сделок. Гранты и контракты остаются отдельно."},
            "scientific_score_modified": False}


def _latest(scope: dict, source: str) -> dict | None:
    rows = external_evidence_store.history(topic_key(scope), source, limit=10)["observations"]
    for row in rows:
        saved = external_evidence_store.read(row["observation_id"])
        if saved["payload"].get("candidate_context_binding") == scope:
            return saved
    return None


def _fresh(saved: dict | None, now: datetime) -> bool:
    if saved is None:
        return False
    stamp = datetime.fromisoformat(saved["created_at"])
    ttl = timedelta(hours=CACHE_HOURS) if saved["status"] in {"complete", "empty_observed_response"} else timedelta(minutes=ERROR_CACHE_MINUTES)
    return timedelta(0) <= now - stamp <= ttl


def _date_field(source: str, record: dict) -> tuple[str | None, str]:
    fields = {
        "gdelt_doc_2_0": (("seen_date",), "observed_by_aggregator"),
        "epo_ops": (("publication_date", "published_at"), "patent_publication"),
        "nih_reporter": (("project_start", "project_start_date"), "project_start"),
        "ukri_gtr": (("project_start_date",), "project_start"),
        "usaspending": (("start_date", "action_date", "award_start_date"), "contract_date"),
        "huggingface_hub": (("created_at",), "repository_creation"),
        "clinicaltrials_gov": (("first_posted_date",), "registry_first_posted"),
        "nsf_awards": (("award_date",), "grant_award"),
        "openaire_projects": (("project_start_date",), "project_start"),
        "datacite": (("resource_publication_date",), "resource_publication"),
        "dealroom_public_rounds": (("announced_month",), "investment_announcement_month"),
        "dealroom_marketmaps": (("publication_date",), "unknown_publication_date"),
    }
    keys, kind = fields.get(source, (("published_at", "publication_date", "first_publication_date", "year"), "publication"))
    return next((str(record[key]) for key in keys if record.get(key)), None), kind


def passport(source: str, record: dict, saved: dict) -> dict | None:
    original = str(record.get("url") or "")
    if not original and source == "epo_ops" and re.fullmatch(r"[A-Z]{2}[0-9A-Za-z]+", str(record.get("publication_id") or "")):
        # OPS supplies bibliographic identifiers, not record URLs. Resolve only
        # the exact publication number, never a guessed technology search.
        original = "https://worldwide.espacenet.com/patent/search?q=pn%3D" + record["publication_id"]
    parsed = urlsplit(original)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None or parsed.password is not None:
        return None
    published, date_kind = _date_field(source, record)
    name, group, trust = SOURCE_INFO[source]
    return {"source": source, "source_name": name, "group": group,
            "title_original": str(record.get("title") or record.get("repo_id") or original),
            "url": original, "record_date": published, "date_kind": date_kind,
            "retrieved_at": saved["payload"].get("retrieved_at") or saved["created_at"],
            "language_original": record.get("language") or None,
            "publisher": record.get("publisher") or record.get("domain") or record.get("lead_organisation") or None,
            "publisher_organisation": record.get("publisher_organisation"),
            "publisher_homepage": record.get("publisher_homepage"),
            "author": record.get("author") or None,
            "region": record.get("source_country") or None,
            "region_scope": record.get("country_scope") or "not_verified",
            "trust_comment": trust, "observation_id": saved["observation_id"],
            "record_type": record.get("record_type") or record.get("artifact_type") or group,
            "match_status": "automatic_search_match", "expert_validated": False,
            "date_precision": record.get("date_precision"),
            "project_start_date": record.get("project_start_date"),
            "project_start_planned": record.get("project_start_planned"),
            "recipient_organisation": record.get("recipient_organisation"),
            "funding_amount": record.get("funding_amount"), "funding_currency": record.get("funding_currency"),
            "funding_amount_basis": record.get("funding_amount_basis"),
            "participant_country_codes": record.get("participant_country_codes"),
            "metadata_licence": record.get("metadata_licence"),
            "model_input_allowed": record.get("model_input_allowed"),
            "usage_scope": record.get("usage_scope"),
            "publisher_filter_domain": saved["payload"].get("publisher_filter_domain"),
            "publisher_backend": saved["payload"].get("publisher_backend"),
            "source_kind": saved["payload"].get("source_kind"),
            "query_match_terms": record.get("query_match_terms"),
            "record_id": record.get("record_id"),
            "original_record_url_available": record.get("original_record_url_available"),
            "reported_amount_text": record.get("reported_amount_text"),
            "reported_context": record.get("reported_context"),
            "reported_company_count": record.get("reported_company_count"),
            "company_records_downloaded": record.get("company_records_downloaded"),
            "aggregator_redirect_url": record.get("aggregator_redirect_url"),
            "original_article_url_available": record.get("original_article_url_available"),
            "provider_cache_status": saved["payload"].get("provider_cache_status"),
            "provider_fetched_at": saved["payload"].get("provider_fetched_at"),
            "family_id": record.get("family_id"), "families": record.get("families"),
            "publication_number": record.get("publication_number"), "jurisdiction": record.get("jurisdiction") or record.get("country"), "kind": record.get("kind"),
            "applicants": record.get("applicants") or [party.get("name") for party in ((record.get("biblio") or {}).get("parties") or {}).get("applicants", []) if isinstance(party, dict) and party.get("name")],
            "event_uri": record.get("event_uri"), "provider_duplicate_flag": record.get("provider_duplicate_flag"),
            "identity_metadata": external_material_groups.identity_metadata(source, record),
            "scientific_score_modified": False}


def _packet(scope: dict, sources: list[str], saved_by_source: dict,
            now: datetime | None = None) -> dict:
    checked_at = now or datetime.now(timezone.utc)
    reports = []
    for source in sources:
        saved = saved_by_source.get(source)
        payload = saved["payload"] if saved else {}
        rows = payload.get("observations")
        materials = [passport(source, row, saved) for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
        reports.append({"source": source, "name": SOURCE_INFO[source][0], "group": SOURCE_INFO[source][1],
                        "status": saved["status"] if saved else "not_collected", "materials": [m for m in materials if m],
                        "observed_count": len(rows) if isinstance(rows, list) else None,
                        "retrieved_at": payload.get("retrieved_at"),
                        "observation_id": saved["observation_id"] if saved else None,
                        "cache_fresh": _fresh(saved, checked_at),
                        "provider_cache_status": payload.get("provider_cache_status"),
                        "provider_fetched_at": payload.get("provider_fetched_at"),
                        "limitations": payload.get("limitations", []),
                        "query_method": payload.get("query_method"),
                        "coverage_exhaustive": False, "trust_comment": SOURCE_INFO[source][2]})
    material_grouping = external_material_groups.group_materials([m for r in reports for m in r["materials"]])
    return {"binding": scope, "reports": reports, "material_grouping": material_grouping,
            "context_role": "automatic_search_match_not_verified_signal_evidence",
            "scientific_score_modified": False, "missing_is_zero": False,
            "ready_count": sum(r["status"] in {"complete", "empty_observed_response"} for r in reports),
            "material_count": sum(len(r["materials"]) for r in reports)}


def read(mission: str, score: int, candidate: int, query: str | None = None,
         sources: list[str] | None = None) -> dict:
    card = candidate_external_links._candidate(mission, score, candidate)
    scope = binding(mission, score, candidate, card, query or default_query(card))
    selected = validate_sources(sources if sources is not None else DEFAULT_SOURCES)
    return _packet(scope, selected, {source: _latest(scope, source) for source in selected})


def read_saved_all(mission: str, score: int, candidate: int, card: dict | None = None) -> dict:
    """Export saved contexts across query/date changes, without network access.

    Retain the latest observation per source/search phrase for this exact saved
    composition. Explicitly flag the 50-context safety cap, never silently claim
    that a capped export is complete. Old query bindings remain attached.
    """
    card = card or candidate_external_links._candidate(mission, score, candidate)
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT observation_id,source,role,topic_id,status,report_payload_sha256,payload,created_at FROM ("
                    "SELECT DISTINCT ON(source,payload->'candidate_context_binding'->>'query') * "
                    "FROM external_evidence_observation WHERE payload ? 'candidate_context_binding' "
                    "AND payload->'candidate_context_binding'->>'mission_id'=%s "
                    "AND payload->'candidate_context_binding'->>'score_run_id'=%s "
                    "AND payload->'candidate_context_binding'->>'candidate_id'=%s "
                    "AND payload->'candidate_context_binding'->>'composition_sha256'=%s "
                    "ORDER BY source,payload->'candidate_context_binding'->>'query',created_at DESC,observation_id DESC"
                    ") latest ORDER BY created_at DESC,observation_id DESC LIMIT 51",
                    (mission, str(score), str(candidate), card["composition_sha256"]))
        rows = cur.fetchall()
    reports, scopes = [], []
    for identifier, source, role, topic, status, checksum, payload, created in rows[:50]:
        external_evidence_store.verify(payload)
        if (source, role, topic, status, checksum) != (payload["source"], payload["role"], payload["topic_id"], payload["status"], payload["report_payload_sha256"]):
            raise ValueError("Сохранённые столбцы внешнего контекста не совпадают с пакетом.")
        saved_scope = payload["candidate_context_binding"]
        if source not in SOURCE_INFO:
            raise ValueError("В сохранённой выгрузке неизвестный источник.")
        saved = {"observation_id": str(identifier), "source": source, "status": status,
                 "created_at": created.isoformat(), "payload": payload}
        packet = _packet(saved_scope, [source], {source: saved})
        for report in packet["reports"]:
            report["binding"] = saved_scope
            reports.append(report)
        if saved_scope not in scopes:
            scopes.append(saved_scope)
    materials = [m for r in reports for m in r["materials"]]
    return {"binding": binding(mission, score, candidate, card, default_query(card)),
            "saved_query_bindings": scopes, "reports": reports,
            "material_grouping": external_material_groups.group_materials(materials),
            "material_count": len(materials), "context_role": "automatic_search_match_not_verified_signal_evidence",
            "scientific_score_modified": False, "missing_is_zero": False,
            "export_scope": "latest_saved_observation_per_source_and_query_exact_composition",
            "context_limit": 50, "contexts_truncated": len(rows) > 50,
            "network_requested": False}


def read_visible(mission: str, score: int, candidate: int,
                 sources: list[str] | None = None) -> dict:
    """Show scoring inputs as well as the scout's configured extra channels."""
    packet = read_saved_all(mission, score, candidate)
    configured = read(mission, score, candidate, sources=sources)
    seen = {(r["source"], (r.get("binding") or packet["binding"])["query"]) for r in packet["reports"]}
    for report in configured["reports"]:
        key = (report["source"], configured["binding"]["query"])
        if key not in seen:
            report["binding"] = configured["binding"]
            packet["reports"].append(report)
    materials = [m for r in packet["reports"] for m in r["materials"]]
    packet["material_grouping"] = external_material_groups.group_materials(materials)
    packet["material_count"] = len(materials)
    packet["ready_count"] = sum(r["status"] in {"complete", "empty_observed_response"} for r in packet["reports"])
    return packet


def _fetch_one(source: str, scope: dict) -> dict:
    name, class_name = ADAPTERS[source]
    module = importlib.import_module("saia." + name)
    current = date.fromisoformat(scope["retrieval_date"])
    start = current - timedelta(days=5 * 366)
    if source == "gdelt_doc_2_0":
        start = current - timedelta(days=min(60, int(load_policy()["news"]["max_lookback_days"])))
    if source == "usaspending":
        start = max(start, date(2007, 10, 1))
    args = {"topic_id": topic_key(scope), "query": scope["query"], "start": start,
            "end": current, "as_of": current, "max_records": 5}
    if source == "epo_ops":
        args["phrase"] = args.pop("query")
    if class_name in {"RSSQuery", "AggregatorQuery"}:
        args["source"] = source
    if source in {"event_registry", "mediacloud_news", "google_news_rss"} or source in publishers():
        args["start"] = current - timedelta(days=29)
    try:
        result = module.fetch(getattr(module, class_name)(**args), timeout=PER_REQUEST_TIMEOUT)
    except (ValueError, TypeError, KeyError, AttributeError, OSError) as error:
        result = {"version": VERSION, "source": source,
                  "role": external_evidence_store.SOURCES[source], "topic_id": topic_key(scope),
                  "query": {"text": scope["query"]}, "status": "invalid_source_response",
                  "status_reason": type(error).__name__, "observations": None,
                  "retrieved_at": datetime.now(timezone.utc).isoformat(),
                  "scientific_score_modified": False, "missing_is_zero": False}
    result = dict(result)
    if source == "osti_gov":
        # The source drawer is a bibliography, not implicit permission for AI use.
        result.update(model_input_allowed=False, model_training_allowed=False,
                      bulk_reuse_approved=False, model_inputs_modified=False)
    if source == "aist_press_rss":
        from saia.rss_evidence import personal_reader_flags
        result.update(personal_reader_flags())
    if source in publishers():
        result.update(reader_flags(source))
    result.pop("report_payload_sha256", None)
    result["candidate_context_binding"] = scope
    result["report_payload_sha256"] = digest(result)
    saved = external_evidence_store.record(result)
    return {**saved, "payload": result}


class ContextBusy(ValueError):
    pass


def _collect(mission: str, score: int, candidate: int, query: str | None = None,
            sources: list[str] | None = None) -> dict:
    card = candidate_external_links._candidate(mission, score, candidate)
    scope = binding(mission, score, candidate, card, query or default_query(card))
    selected = validate_sources(sources if sources is not None else DEFAULT_SOURCES)
    # Apply the same registry gate as individual connectors before any network use.
    validate_runtime_sources(load_registry(REGISTRY_PATH), load_policy())
    lock = int(digest(scope)[:15], 16)
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_xact_lock(%s)", (lock,))
        if not cur.fetchone()[0]:
            raise ContextBusy("Материалы этой карточки уже собираются. Повторите обновление через несколько секунд.")
        saved = {source: _latest(scope, source) for source in selected}
        now = datetime.now(timezone.utc)
        missing = [source for source in selected if not _fresh(saved[source], now)]
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as executor:
            fetched = executor.map(lambda source: (source, _fetch_one(source, scope)), missing)
            for source, observation in fetched:
                saved[source] = observation
        result = _packet(scope, selected, saved)
        result["fetched_sources"] = missing
        result["cache_reused_sources"] = [source for source in selected if source not in missing]
        return result


def collect(mission: str, score: int, candidate: int, query: str | None = None,
            sources: list[str] | None = None) -> dict:
    # Also bound different cards, not only concurrent repeats of one card.
    if not _COLLECTION_SLOTS.acquire(blocking=False):
        raise ContextBusy("Собираются материалы других карточек. Повторите через несколько секунд.")
    try:
        return _collect(mission, score, candidate, query, sources)
    finally:
        _COLLECTION_SLOTS.release()
