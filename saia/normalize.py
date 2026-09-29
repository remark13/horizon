"""Нормализация и дедупликация: raw_record -> work.

    python -m saia.normalize gnn-fraud
    python -m saia.normalize gnn-fraud --stats

Порядок обработки важен: сначала OpenAlex, потом arXiv. Записи OpenAlex
богаче (аффилиации, цитирования, ссылки) и чаще содержат arXiv ID, поэтому
дают лучшую основу для канонической работы; записи arXiv затем подшиваются
к уже существующим как препринтные версии.

Новая политика 0.4.5 сначала проверяет все DOI assertions пакета:
спорные DOI сохраняются в raw/run notes, но исключаются из canonical identity.
Сильные source IDs проверяются перед DOI; для спорных assertions нет
обходной склейки по заголовку. Явный identity_mode='legacy' воспроизводит
прежний порядок DOI/arXiv/title-author. Поколения не переписываются.

Ни одно правило не удаляет запись: исходный raw_record остаётся, версия
подшивается к канонической работе, а причина объединения пишется в
dedup_decision.
"""

from __future__ import annotations

import re
import sys
import unicodedata
import json
import hashlib
from pathlib import Path
from datetime import timedelta
from typing import Any, Iterable

from psycopg.types.json import Jsonb

from saia import db, methodology, runs
from saia import canonical_text

PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
SPACES = re.compile(r"\s+")
ARXIV_IN_URL = re.compile(r"arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5})", re.I)
ARXIV_DOI = re.compile(r"^10\.48550/arxiv\.([0-9]{4}\.[0-9]{4,5})", re.I)
ARXIV_BARE = re.compile(r"^([0-9]{4}\.[0-9]{4,5})")


def identity_policy(mode: str) -> dict:
    if mode == 'legacy':
        return {'version': 'canonical-identity-legacy-through-0.4.4', 'mode': 'legacy'}
    if mode != 'conservative':
        raise ValueError('Неизвестная политика идентичности публикаций.')
    path = Path(__file__).resolve().parents[1] / 'config' / 'normalization.v0.4.5.json'
    policy = json.loads(path.read_text())
    if (policy.get('version') != 'canonical-identity-0.4.5'
            or policy.get('mode') != 'conservative_doi_assertions'
            or policy.get('collision_rules') != ['distinct_arxiv_ids', 'conflicting_titles_and_author_sets', 'inconsistent_arxiv_doi']
            or policy.get('lookup_priority') != ['arxiv', 'openalex', 'doi']
            or policy.get('ambiguous_doi') != 'withhold_for_all_claimants'
            or policy.get('ambiguous_record_fallback') != 'source_identity_only'
            or policy.get('canonical_unit') != 'conservative_source_identity_not_adjudicated_research'):
        raise ValueError('Неподдерживаемый паспорт normalization policy.')
    return policy


def doi_conflicts(parsed_records: list[dict]) -> dict[str, dict]:
    """Preflight every claimant: no first-arriving record owns a disputed DOI."""
    claims: dict[str, list[dict]] = {}
    for record in parsed_records:
        for doi in {v for k, v in record['identifiers'] if k == 'doi'}:
            claims.setdefault(doi, []).append(record)
    conflicts = {}
    for doi, records in sorted(claims.items()):
        arxiv_ids = {v for r in records for k, v in r['identifiers'] if k == 'arxiv'}
        reasons = []
        if len(arxiv_ids) > 1:
            reasons.append('distinct_arxiv_ids')
        # This detects disagreement, not incorrect science. Missing authors
        # cannot establish disagreement, nor can one changed title alone.
        title_authors: dict[str, list[set[str]]] = {}
        for record in records:
            authors = {name_key(a['display_name']) for a in record['authors'] if a.get('display_name')}
            key = title_key(record['title'])
            if key and authors:
                title_authors.setdefault(key, []).append(authors)
        keys = list(title_authors)
        if any(not (left & right) for i, key in enumerate(keys) for other in keys[i + 1:]
               for left in title_authors[key] for right in title_authors[other]):
            reasons.append('conflicting_titles_and_author_sets')
        if doi.startswith('10.48550/arxiv.') and arxiv_ids and doi.removeprefix('10.48550/arxiv.') not in arxiv_ids:
            reasons.append('inconsistent_arxiv_doi')
        if reasons:
            conflicts[doi] = {'asserted_doi': doi, 'arxiv_ids': sorted(arxiv_ids),
                              'reasons': reasons, 'resolution': 'unreviewed'}
    return conflicts


def canonical_identifiers(parsed: dict, conflicts: dict[str, dict]) -> dict:
    """Keep raw assertions untouched; filter only identifiers eligible for merge."""
    withheld = sorted({v for k, v in parsed['identifiers'] if k == 'doi' and v in conflicts})
    return {**parsed, 'identifiers': [(k, v) for k, v in parsed['identifiers']
                                     if not (k == 'doi' and v in conflicts)],
            '_withheld_dois': withheld}


def title_key(title: str) -> str:
    """Заголовок, приведённый к виду, пригодному для сравнения."""
    text = unicodedata.normalize("NFKD", title or "").lower()
    text = PUNCT.sub(" ", text)
    return SPACES.sub(" ", text).strip()


def name_key(name: str) -> str:
    """Фамилия + инициал: устойчиво к разным написаниям имени."""
    text = unicodedata.normalize("NFKD", name or "").lower()
    text = PUNCT.sub(" ", text)
    parts = [p for p in SPACES.sub(" ", text).strip().split(" ") if p]
    if not parts:
        return ""
    return f"{parts[-1]} {parts[0][0]}" if len(parts) > 1 else parts[0]


def restore_abstract(inverted: dict | None) -> str | None:
    if not inverted:
        return None
    spots: list[tuple[int, str]] = []
    for word, positions in inverted.items():
        for position in positions:
            spots.append((position, word))
    return " ".join(word for _, word in sorted(spots)) or None


def clean_doi(value: str | None) -> str | None:
    if not value:
        return None
    return value.lower().replace("https://doi.org/", "").replace("http://dx.doi.org/", "").strip()


def is_arxiv_doi(doi: str) -> bool:
    """DOI, который arXiv присваивает препринту сам.

    Такой DOI не обозначает отдельную публикацию: он указывает на тот же
    текст, что и издательский DOI рядом. Пара «10.48550/arxiv.1703.04826 +
    10.18653/v1/d17-1159» — это препринт и его конференционная версия, то
    есть одна исследовательская линия.
    """
    return doi.startswith("10.48550/")


def publisher_dois(identifiers: Iterable[tuple[str, str]]) -> set[str]:
    return {v for k, v in identifiers if k == "doi" and not is_arxiv_doi(v)}


def is_imprecise(date_text: str | None, source: str,
                 config: methodology.Methodology) -> bool:
    """Является ли дата заглушкой «известен только год».

    ИСПРАВЛЕНО ПОСЛЕ РЕВИЗИИ (дефект P1-6). Раньше признаком служила сама
    строка: любое 1 января считалось заглушкой независимо от источника.
    Для OpenAlex это верно — там 1 января подставляется, когда известен
    только год. Для arXiv это неверно: поле created протокола OAI-PMH
    содержит настоящую дату подачи с точностью до дня, и препринт, поданный
    1 января, сдвигался на 364 дня вперёд, то есть выбрасывался из своего
    собственного окна. В корпусе миссии ml-area-2017 arXiv — единственный
    источник, так что правило било ровно по тем данным, ради которых писалось.
    """
    if not date_text or not date_text.endswith("-01-01"):
        return False
    return config.imprecise_date_policy(source)


def effective_date(date_text: str | None, year: int | None, source: str,
                   config: methodology.Methodology) -> str | None:
    if not date_text:
        return f"{year}-12-31" if year else None
    if is_imprecise(date_text, source, config):
        return f"{date_text[:4]}-12-31"
    return date_text


# ---------------------------------------------------------------------------
# Извлечение полей из записей источников
# ---------------------------------------------------------------------------

def parse_openalex(payload: dict) -> dict:
    from saia.source_identity import openalex_arxiv_identity
    ids = payload.get("ids") or {}
    identifiers: list[tuple[str, str]] = []

    doi = clean_doi(payload.get("doi") or ids.get("doi"))
    if doi:
        identifiers.append(("doi", doi))
    if payload.get("id"):
        identifiers.append(("openalex", payload["id"].rsplit("/", 1)[-1]))
    if ids.get("pmid"):
        identifiers.append(("pmid", str(ids["pmid"]).rsplit("/", 1)[-1]))

    arxiv_id = openalex_arxiv_identity(payload)['canonical_arxiv_id']
    if arxiv_id:
        identifiers.append(("arxiv", arxiv_id))

    authors = []
    for position, authorship in enumerate(payload.get("authorships") or []):
        author = authorship.get("author") or {}
        display = author.get("display_name")
        if not display:
            continue
        institutions = authorship.get("institutions") or []
        authors.append({
            "external_id": (author.get("id") or "").rsplit("/", 1)[-1] or None,
            "display_name": display,
            "position": position,
            "organisations": [
                {
                    "ror": (inst.get("ror") or "").rsplit("/", 1)[-1] or None,
                    "display_name": inst.get("display_name"),
                    "country_code": inst.get("country_code"),
                }
                for inst in institutions
                if inst.get("display_name")
            ],
        })

    # Тематическая разметка источника. Заполнена практически всегда и
    # отделяет случайно попавшие работы других областей. Границы применения
    # описаны в миграции 005: фильтровать по field можно, опираться на topic
    # при ретроспективном прогоне нельзя.
    primary = payload.get("primary_topic") or {}
    topic = {
        "primary_topic": primary.get("display_name"),
        "primary_topic_id": (primary.get("id") or "").rsplit("/", 1)[-1] or None,
        "primary_field": (primary.get("field") or {}).get("display_name"),
        "primary_subfield": (primary.get("subfield") or {}).get("display_name"),
    }

    date_text = payload.get("publication_date")
    return {
        **topic,
        "title": payload.get("display_name") or payload.get("title") or "",
        "abstract": restore_abstract(payload.get("abstract_inverted_index")),
        "type": payload.get("type"),
        "language": payload.get("language"),
        "publication_date": date_text,
        "publication_year": payload.get("publication_year"),
        "cited_by_count": payload.get("cited_by_count"),
        "counts_by_year": payload.get("counts_by_year"),
        "is_retracted": bool(payload.get("is_retracted")),
        "identifiers": identifiers,
        "authors": authors,
        "version_kind": "preprint" if payload.get("type") == "preprint" else "published",
    }


def parse_arxiv(payload: dict) -> dict:
    raw_id = payload.get("id") or ""
    bare = raw_id.rsplit("/", 1)[-1]
    arxiv_id = bare.split("v")[0]

    identifiers: list[tuple[str, str]] = []
    if ARXIV_BARE.match(arxiv_id):
        identifiers.append(("arxiv", arxiv_id))
    doi = clean_doi(payload.get("arxiv_doi"))
    if doi:
        identifiers.append(("doi", doi))

    authors = []
    for position, author in enumerate(payload.get("author") or []):
        display = (author or {}).get("name")
        if display:
            # arXiv не отдаёт аффилиации — организаций здесь нет, и это
            # честно отражается пустым списком, а не выдуманной записью.
            authors.append({
                "external_id": None,
                "display_name": display,
                "position": position,
                "organisations": [],
            })

    # Atom отдаёт дату подачи в published, OAI — в created. Первая версия
    # работы в обоих случаях, поэтому поле взаимозаменяемо.
    published = (payload.get("published") or payload.get("created") or "")[:10]
    return {
        # arXiv не даёт тематику в терминах OpenAlex. Пустые поля честнее
        # выведенных из категорий: cs.LG это не то же самое, что field.
        "primary_topic": None,
        "primary_topic_id": None,
        "primary_field": None,
        "primary_subfield": None,
        "title": SPACES.sub(" ", payload.get("title") or "").strip(),
        "abstract": SPACES.sub(" ", payload.get("summary") or "").strip() or None,
        "type": "preprint",
        "language": "en",
        "publication_date": published or None,
        "publication_year": int(published[:4]) if published else None,
        "cited_by_count": None,
        "counts_by_year": None,
        "is_retracted": False,
        "identifiers": identifiers,
        "authors": authors,
        "version_kind": "preprint",
    }


PARSERS = {"openalex": parse_openalex, "arxiv": parse_arxiv}


# ---------------------------------------------------------------------------
# Поиск существующей работы
# ---------------------------------------------------------------------------

def find_existing(cur, run_id: int, parsed: dict, *, conservative: bool = False) -> tuple[int | None, str | None, str | None]:
    """Вернуть (work_id, правило, совпавшее значение) либо (None, None, None).

    Поиск идёт ВНУТРИ прогона. После перехода на поколения (дефект P0-3)
    область поиска по mission_id означала бы склейку с работами прошлого
    поколения, построенного другими правилами, — то самое смешение решений
    от разных версий дедупликации, ради которого прежний код всё стирал.
    """
    identifiers = parsed['identifiers']
    if conservative:
        identifiers = sorted(identifiers, key=lambda item: {'arxiv': 0, 'openalex': 1, 'doi': 2}.get(item[0], 3))
    for kind, value in identifiers:
        if kind not in (("arxiv", "openalex", "doi") if conservative else ("doi", "arxiv")):
            continue
        cur.execute(
            "SELECT work_id FROM identifier WHERE run_id = %s AND kind = %s AND value = %s",
            (run_id, kind, value),
        )
        row = cur.fetchone()
        if row:
            if kind == 'openalex':
                return row[0], 'openalex_source_identity_match', f'openalex:{value}'
            rule = "doi_match" if kind == "doi" else "arxiv_id_match"
            return row[0], rule, f"{kind}:{value}"

    if conservative and parsed.get('_withheld_dois'):
        # No title-only back door after rejecting the DOI assertion.
        return None, None, None

    key = title_key(parsed["title"])
    if not key:
        return None, None, None

    cur.execute(
        "SELECT work_id FROM work WHERE run_id = %s AND title_key = %s",
        (run_id, key),
    )
    candidates = [row[0] for row in cur.fetchall()]
    if not candidates:
        return None, None, None

    # Совпадение заголовка само по себе слабое: обзоры и tutorial-статьи
    # нередко называются одинаково. Требуем пересечение авторов.
    incoming = {name_key(a["display_name"]) for a in parsed["authors"] if a["display_name"]}
    if not incoming:
        return None, None, None

    incoming_dois = publisher_dois(parsed["identifiers"])

    for work_id in candidates:
        cur.execute(
            """
            SELECT a.name_key FROM work_author wa
            JOIN author a ON a.author_id = wa.author_id
            WHERE wa.work_id = %s
            """,
            (work_id,),
        )
        existing = {row[0] for row in cur.fetchall()}
        if not (existing & incoming):
            continue

        # Разные издательские DOI — это разные публикации, как бы одинаково
        # они ни назывались. Так выглядят тома одного сборника трудов и
        # повторные депозиты: заголовок совпадает, редакторы пересекаются,
        # но объединять их нельзя. arXiv-овский DOI в счёт не идёт: он
        # указывает на тот же текст, что и издательский.
        cur.execute(
            "SELECT value FROM identifier WHERE work_id = %s AND kind = 'doi'",
            (work_id,),
        )
        existing_dois = {v for (v,) in cur.fetchall() if not is_arxiv_doi(v)}
        if existing_dois and incoming_dois and not (existing_dois & incoming_dois):
            # Регистрант DOI не доказывает ни идентичность, ни различие.
            # Без явного общего DOI/arXiv-ID выше сохраняем разные работы.
            # Совпадение названия и упрощённых имён — лишь повод для проверки.
            return None, "blocked_different_doi", sorted(incoming_dois)[0]

        incoming_arxiv = {v for k, v in parsed['identifiers'] if k == 'arxiv'}
        if incoming_arxiv:
            cur.execute("SELECT value FROM identifier WHERE work_id = %s AND kind = 'arxiv'",
                        (work_id,))
            existing_arxiv = {v for (v,) in cur.fetchall()}
            if existing_arxiv and not (existing_arxiv & incoming_arxiv):
                # Two deposits may be related, but title + one shared name
                # cannot prove they are the same work. Preserve both until an
                # explicit shared identifier or a reviewed identity decision.
                return None, 'blocked_conflicting_arxiv_ids', sorted(incoming_arxiv)[0]

        return work_id, "title_author_match", key[:80]

    return None, None, None


# ---------------------------------------------------------------------------
# Запись
# ---------------------------------------------------------------------------

def upsert_author(cur, mission_id: str, run_id: int, author: dict) -> int:
    key = name_key(author["display_name"])
    cur.execute(
        """
        INSERT INTO author (mission_id, run_id, external_id, display_name, name_key)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (run_id, name_key, external_id) DO UPDATE
            SET display_name = EXCLUDED.display_name
        RETURNING author_id
        """,
        (mission_id, run_id, author["external_id"], author["display_name"], key),
    )
    return cur.fetchone()[0]


def upsert_organisation(cur, mission_id: str, run_id: int, org: dict) -> int:
    cur.execute(
        """
        INSERT INTO organisation (mission_id, run_id, ror, display_name, country_code)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (run_id, display_name) DO UPDATE
            SET ror = COALESCE(organisation.ror, EXCLUDED.ror),
                country_code = COALESCE(organisation.country_code, EXCLUDED.country_code)
        RETURNING organisation_id
        """,
        (mission_id, run_id, org["ror"], org["display_name"], org["country_code"]),
    )
    return cur.fetchone()[0]


def create_work(cur, mission_id: str, run_id: int, parsed: dict, source: str,
                config: methodology.Methodology) -> int:
    date_text = parsed["publication_date"]
    cur.execute(
        """
        INSERT INTO work (mission_id, run_id, canonical_title, title_key, abstract, type,
                          language, publication_date, publication_year,
                          date_is_imprecise, effective_date, cited_by_count,
                          counts_by_year, is_retracted,
                          primary_topic, primary_topic_id, primary_field, primary_subfield)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING work_id
        """,
        (
            mission_id, run_id, parsed["title"], title_key(parsed["title"]), parsed["abstract"],
            parsed["type"], parsed["language"], date_text, parsed["publication_year"],
            is_imprecise(date_text, source, config),
            effective_date(date_text, parsed["publication_year"], source, config),
            parsed["cited_by_count"], Jsonb(parsed["counts_by_year"]) if parsed["counts_by_year"] else None,
            parsed["is_retracted"],
            parsed["primary_topic"], parsed["primary_topic_id"],
            parsed["primary_field"], parsed["primary_subfield"],
        ),
    )
    return cur.fetchone()[0]


def adopt_earlier_date(cur, work_id: int, parsed: dict, source: str,
                       config: methodology.Methodology) -> bool:
    """Понизить дату работы, если версия из другого источника раньше.

    ИСПРАВЛЕНО ПОСЛЕ РЕВИЗИИ (дефект P0-2). Каноническая работа брала дату
    первого встреченного источника и больше её не меняла, а порядок обхода
    ставил OpenAlex перед arXiv. То есть работа, впервые появившаяся
    препринтом в марте и вышедшая в журнале в декабре, получала декабрь.
    Для системы, которая ищет РАННИЕ сигналы, это сдвиг ровно в ту сторону,
    которая убивает задачу: тема, видимая раньше, искусственно переносилась
    к журнальной дате и теряла месяцы форы.

    Дата работы — минимум по её версиям, а не дата той, что пришла первой.
    """
    date_text = parsed["publication_date"]
    if not date_text and not parsed["publication_year"]:
        return False

    candidate = effective_date(date_text, parsed["publication_year"], source, config)
    if not candidate:
        return False

    cur.execute("SELECT effective_date FROM work WHERE work_id = %s", (work_id,))
    current = cur.fetchone()[0]
    if current is not None and str(current) <= candidate:
        return False

    cur.execute(
        """
        UPDATE work SET publication_date = %s, publication_year = %s,
                        date_is_imprecise = %s, effective_date = %s
        WHERE work_id = %s
        """,
        (date_text, parsed["publication_year"],
         is_imprecise(date_text, source, config), candidate, work_id),
    )
    return True


def enrich_work(cur, work_id: int, parsed: dict) -> None:
    """Дополнить каноническую работу тем, чего в ней ещё нет.

    Объединение не должно терять данные: если журнальная запись принесла
    аннотацию, а препринтная её не имела, аннотация появляется.
    """
    cur.execute(
        """
        UPDATE work SET
            abstract = COALESCE(work.abstract, %s),
            cited_by_count = COALESCE(work.cited_by_count, %s),
            counts_by_year = COALESCE(work.counts_by_year, %s),
            language = COALESCE(work.language, %s),
            -- Тематика приходит только от OpenAlex: препринт arXiv,
            -- подшитый к работе, приносит её из журнальной версии.
            primary_topic = COALESCE(work.primary_topic, %s),
            primary_topic_id = COALESCE(work.primary_topic_id, %s),
            primary_field = COALESCE(work.primary_field, %s),
            primary_subfield = COALESCE(work.primary_subfield, %s)
        WHERE work_id = %s
        """,
        (
            parsed["abstract"], parsed["cited_by_count"],
            Jsonb(parsed["counts_by_year"]) if parsed["counts_by_year"] else None,
            parsed["language"],
            parsed["primary_topic"], parsed["primary_topic_id"],
            parsed["primary_field"], parsed["primary_subfield"],
            work_id,
        ),
    )


def attach(cur, mission_id: str, run_id: int, work_id: int, parsed: dict,
           raw_record_id: int, source: str, record_id: str) -> None:
    for kind, value in parsed["identifiers"]:
        cur.execute(
            "INSERT INTO identifier (mission_id, run_id, work_id, kind, value) "
            "VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (run_id, kind, value) DO NOTHING",
            (mission_id, run_id, work_id, kind, value),
        )

    cur.execute(
        """
        INSERT INTO work_version (work_id, run_id, raw_record_id, source,
                                  source_record_id, version_kind, version_date)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (run_id, source, source_record_id) DO NOTHING
        """,
        (work_id, run_id, raw_record_id, source, record_id, parsed["version_kind"],
         parsed["publication_date"]),
    )

    for author in parsed["authors"]:
        author_id = upsert_author(cur, mission_id, run_id, author)
        org_ids = [upsert_organisation(cur, mission_id, run_id, org)
                   for org in author["organisations"]]
        if not org_ids:
            org_ids = [None]
        for org_id in org_ids:
            cur.execute(
                """
                INSERT INTO work_author (work_id, author_id, organisation_id, author_position)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (work_id, author_id, org_id, author["position"]),
            )


def normalize(mission_id: str, verbose: bool = True, *,
              identity_mode: str = 'conservative',
              collection_batch_id: int | None = None,
              text_policy_mode: str = canonical_text.VERSION) -> dict:
    """Построить новое поколение нормализованного корпуса.

    ИСПРАВЛЕНО ПОСЛЕ РЕВИЗИИ (дефект P0-3). Здесь стояла функция
    reset_mission с тремя DELETE по mission_id, и её обоснование звучало
    убедительно: нормализованный корпус производен от неизменного сырья,
    значит его всегда можно построить заново. Рассуждение верное, а вывод
    неверный. Восстановимость не равна сохранности: чтобы доказать, ЧТО
    система отвечала на прежнем срезе, прошлый ответ должен существовать,
    а не подлежать восстановлению при условии, что правила не менялись.
    Ровно правила и меняются — иначе пересчёт был бы не нужен.

    Теперь каждый пересчёт создаёт прогон, работы принадлежат прогону, а
    текущим поколением считается последний завершённый.
    """
    summary = {"processed": 0, "created": 0, "merged": 0, "redated": 0, "by_rule": {}}
    config = methodology.load_default()
    policy = identity_policy(identity_mode)
    policy_hash = hashlib.sha256(json.dumps(policy, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    text_policy = canonical_text.policy(text_policy_mode)

    with db.connect() as conn:
        with conn.cursor() as cur:
            if collection_batch_id is not None:
                if (not isinstance(collection_batch_id, int)
                        or isinstance(collection_batch_id, bool)
                        or collection_batch_id < 1):
                    raise ValueError('Номер пакета должен быть положительным целым.')
                cur.execute(
                    'SELECT batch_id, query_version_id, seal_status FROM collection_batch '
                    "WHERE mission_id = %s AND batch_id = %s AND status = 'complete' "
                    "AND seal_status IN ('sealed', 'legacy_frozen')",
                    (mission_id, collection_batch_id),
                )
            else:
                cur.execute('SELECT batch_id, query_version_id, seal_status FROM collection_batch '
                            "WHERE mission_id = %s AND status = 'complete' "
                            "AND seal_status IN ('sealed', 'legacy_frozen') "
                            'ORDER BY created_at DESC, batch_id DESC LIMIT 1', (mission_id,))
            batch = cur.fetchone()
            if collection_batch_id is not None and batch is None:
                raise ValueError('Указанный завершённый sealed-пакет этой миссии не найден.')
            legacy_query_id = None
            if batch is None:
                cur.execute('SELECT 1 FROM collection_batch WHERE mission_id = %s LIMIT 1',
                            (mission_id,))
                if cur.fetchone():
                    raise ValueError('Есть пакеты сбора, но нет завершённого; нормализация не запущена.')
                cur.execute('SELECT DISTINCT query_version_id FROM source_snapshot WHERE mission_id = %s',
                            (mission_id,))
                legacy_queries = cur.fetchall()
                if len(legacy_queries) != 1:
                    raise ValueError('Legacy-снимки пусты или относятся к разным запросам; нужен завершённый пакет сбора.')
                legacy_query_id = legacy_queries[0][0]
            run_id = runs.start_run(cur, mission_id, "normalize", config,
                                   query_version_id=batch[1] if batch else legacy_query_id,
                                   notes={'collection_batch_id': batch[0] if batch else None,
                                          'collection_batch_seal_status': batch[2] if batch else None,
                                          'normalization_policy': policy,
                                          'normalization_policy_sha256': policy_hash,
                                          'canonical_text_policy': text_policy,
                                          'effective_config': config.raw})
            summary["run_id"] = run_id
            cur.execute("UPDATE analysis_run SET status='normalizing' WHERE run_id=%s", (run_id,))
            period_start, period_end, period_origin = runs.analysis_period(cur, run_id)
            cur.execute("UPDATE analysis_run SET notes = notes || %s WHERE run_id = %s",
                        (Jsonb({'input_period': {'from': period_start.isoformat() if period_start else None,
                                                'to': (period_end - timedelta(days=1)).isoformat()},
                                'input_period_origin': period_origin}), run_id))
            # OpenAlex первым: его записи богаче и чаще содержат arXiv ID,
            # поэтому дают лучшую основу канонической работы.
            cur.execute(
                """
                SELECT r.raw_record_id, r.source, r.source_record_id, r.payload, s.fetched_at
                FROM raw_record r
                JOIN source_snapshot s ON s.snapshot_id = r.snapshot_id
                LEFT JOIN collection_batch_snapshot bs ON bs.snapshot_id = s.snapshot_id
                WHERE s.mission_id = %s AND (
                    -- Совместимость с тестовыми/старыми базами до миграции
                    -- 010. Как только у миссии появился хотя бы один пакет,
                    -- разрешён только последний ЗАВЕРШЁННЫЙ пакет.
                    NOT EXISTS (
                        SELECT 1 FROM collection_batch WHERE mission_id = %s
                    )
                    OR bs.batch_id = %s
                )
                ORDER BY CASE r.source WHEN 'openalex' THEN 0 ELSE 1 END,
                         r.raw_record_id
                """,
                (mission_id, mission_id, batch[0] if batch else None),
            )
            rows = cur.fetchall()

            parsed_rows = [(raw_id, source, record_id, PARSERS[source](payload))
                           for raw_id, source, record_id, payload, _ in rows if source in PARSERS]
            conflicts = doi_conflicts([parsed for _, _, _, parsed in parsed_rows]) if identity_mode == 'conservative' else {}
            assertion_queue = []
            source_identity_decisions = []
            text_candidates = {}
            raw_payloads = {raw_id: payload for raw_id, _, _, payload, _ in rows}
            raw_observed = {raw_id: observed.isoformat() for raw_id, _, _, _, observed in rows}
            summary['identity_policy_version'] = policy['version']
            summary['ambiguous_doi_count'] = len(conflicts)

            for raw_record_id, source, record_id, raw_parsed in parsed_rows:
                parsed = canonical_identifiers(raw_parsed, conflicts) if identity_mode == 'conservative' else raw_parsed
                if not parsed["title"]:
                    continue

                summary["processed"] += 1
                work_id, rule, matched = find_existing(cur, run_id, parsed, conservative=identity_mode == 'conservative')

                # Отказ от склейки — тоже решение, и оно попадает в журнал
                # отдельной записью рядом с new_work: иначе нельзя объяснить,
                # почему две одинаково названные работы остались разными.
                blocked_rule = rule if rule in ('blocked_different_doi', 'blocked_conflicting_arxiv_ids') else None
                blocked = matched if blocked_rule else None
                if blocked:
                    work_id, rule, matched = None, None, None
                    summary["blocked"] = summary.get("blocked", 0) + 1

                if work_id is None:
                    work_id = create_work(cur, mission_id, run_id, parsed, source, config)
                    rule, matched = "new_work", None
                    summary["created"] += 1
                else:
                    enrich_work(cur, work_id, parsed)
                    if adopt_earlier_date(cur, work_id, parsed, source, config):
                        summary["redated"] += 1
                    summary["merged"] += 1

                summary["by_rule"][rule] = summary["by_rule"].get(rule, 0) + 1
                attach(cur, mission_id, run_id, work_id, parsed, raw_record_id,
                       source, record_id)
                payload = raw_payloads[raw_record_id]
                text_candidates.setdefault(work_id, []).append({
                    'raw_record_id': raw_record_id, 'source': source, 'source_record_id': record_id,
                    'title_key': title_key(parsed['title']), 'abstract': parsed['abstract'],
                    'arxiv_ids': [value for kind, value in parsed['identifiers'] if kind == 'arxiv'],
                    'observed_at': raw_observed[raw_record_id],
                    'metadata': {key: payload[key] for key in
                                 ('id', 'created', 'published', 'updated', '_arxiv_versions',
                                  '_source_format', '_submission_date_raw') if key in payload}})
                for doi in parsed.get('_withheld_dois', []):
                    assertion_queue.append({**conflicts[doi], 'raw_record_id': raw_record_id,
                                            'source': source, 'source_record_id': record_id,
                                            'work_id': work_id})
                # Existing dedup schema predates the OA-ID rule. Preserve its
                # exact decision in immutable run notes, not under a false DOI
                # or title rule; no migration or rewrite of closed runs.
                if rule == 'openalex_source_identity_match':
                    source_identity_decisions.append({'work_id': work_id, 'raw_record_id': raw_record_id,
                                                      'source': source, 'source_record_id': record_id,
                                                      'rule': rule, 'matched_value': matched})
                else:
                    cur.execute(
                        """
                        INSERT INTO dedup_decision (work_id, source, record_id, rule, matched_value)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (work_id, source, record_id, rule, matched),
                    )
                if blocked:
                    cur.execute(
                        """
                        INSERT INTO dedup_decision (work_id, source, record_id, rule, matched_value)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (work_id, source, record_id, blocked_rule, blocked),
                    )

            cur.execute('SELECT work_id,canonical_title,abstract FROM work WHERE run_id=%s ORDER BY work_id',
                        (run_id,))
            canonical_rows = cur.fetchall()
            text_counts = {'works': len(canonical_rows), 'native_preferred': 0,
                           'abstracts_changed': 0, 'different_source_texts': 0}
            for work_id, canonical_title, old_abstract in canonical_rows:
                selected = canonical_text.select_abstract(text_candidates[work_id],
                    canonical_title_key=title_key(canonical_title), cutoff=period_end.isoformat(),
                    mode=text_policy_mode)
                if selected['abstract'] != old_abstract:
                    cur.execute('UPDATE work SET abstract=%s WHERE work_id=%s', (selected['abstract'], work_id))
                    text_counts['abstracts_changed'] += 1
                text_counts['native_preferred'] += selected['basis'] == 'native_same_identity_title_and_eligible_text_date'
                text_counts['different_source_texts'] += selected['different_abstract_values']
                audit = {key: value for key, value in selected.items() if key != 'abstract'}
                cur.execute('INSERT INTO work_text_provenance '
                            '(work_id,run_id,policy_version,abstract_raw_record_id,abstract_sha256,selection) '
                            'VALUES (%s,%s,%s,%s,%s,%s)',
                            (work_id, run_id, text_policy_mode, selected['chosen_raw_record_id'],
                             selected['abstract_sha256'], Jsonb(audit)))
            summary['canonical_text_selection'] = text_counts
            cur.execute('UPDATE analysis_run SET notes = notes || %s WHERE run_id = %s',
                        (Jsonb({'identity_conflict_queue': assertion_queue,
                                'identity_conflict_assertions': len(assertion_queue),
                                'source_identity_decisions': source_identity_decisions,
                                'canonical_text_selection': text_counts}), run_id))
            summary['identity_conflict_assertions'] = len(assertion_queue)
            runs.finish_run(cur, run_id, "done")
        conn.commit()

    return summary


def stats(mission_id: str) -> str:
    lines = []
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM work_current WHERE mission_id = %s", (mission_id,))
        works = cur.fetchone()[0]
        cur.execute(
            "SELECT count(*) FROM work_version v JOIN work_current w USING (work_id) WHERE w.mission_id = %s",
            (mission_id,),
        )
        versions = cur.fetchone()[0]

        lines.append(f"  канонических работ      {works}")
        lines.append(f"  версий                  {versions}")
        lines.append(f"  свёрнуто дублей         {versions - works}")

        cur.execute(
            """
            SELECT rule, count(*) FROM dedup_decision d
            JOIN work_current w USING (work_id) WHERE w.mission_id = %s
            GROUP BY rule ORDER BY count(*) DESC
            """,
            (mission_id,),
        )
        lines.append("\n  решения о дедупликации:")
        for rule, count in cur.fetchall():
            lines.append(f"    {rule:<24} {count}")

        cur.execute(
            """
            SELECT count(*) FILTER (WHERE date_is_imprecise),
                   count(*) FILTER (WHERE abstract IS NOT NULL),
                   count(*)
            FROM work_current WHERE mission_id = %s
            """,
            (mission_id,),
        )
        imprecise, with_abstract, total = cur.fetchone()
        lines.append("\n  качество корпуса:")
        lines.append(f"    дат-заглушек           {imprecise} из {total}")
        lines.append(f"    с аннотацией           {with_abstract} из {total}")

        cur.execute(
            """
            SELECT count(DISTINCT wa.organisation_id)
            FROM work_author wa JOIN work_current w USING (work_id)
            WHERE w.mission_id = %s AND wa.organisation_id IS NOT NULL
            """,
            (mission_id,),
        )
        lines.append(f"    организаций            {cur.fetchone()[0]}")

        cur.execute(
            """
            SELECT count(*) FROM work_current w WHERE w.mission_id = %s
              AND EXISTS (SELECT 1 FROM work_version v WHERE v.work_id = w.work_id AND v.source = 'arxiv')
              AND EXISTS (SELECT 1 FROM work_version v WHERE v.work_id = w.work_id AND v.source = 'openalex')
            """,
            (mission_id,),
        )
        both = cur.fetchone()[0]
        lines.append(f"\n  работ, видимых в обоих источниках: {both}")
        lines.append("    (это одна исследовательская линия, а не два независимых доказательства)")

    return "\n".join(lines)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Нормализация и дедупликация корпуса Horizon")
    parser.add_argument("mission_id")
    parser.add_argument("--stats", action="store_true", help="только показать состояние")
    parser.add_argument('--identity-policy', choices=['conservative', 'legacy'], default='conservative',
                        help='новая политика спорных DOI либо явный исторический comparator')
    args = parser.parse_args()

    if args.stats:
        print(stats(args.mission_id))
        return 0

    print(f"Нормализую корпус миссии {args.mission_id} ...")
    summary = normalize(args.mission_id, identity_mode=args.identity_policy)
    print(
        f"\nОбработано записей: {summary['processed']},"
        f" создано работ: {summary['created']}, объединено: {summary['merged']}"
    )
    print("\nСостояние корпуса:")
    print(stats(args.mission_id))
    return 0


if __name__ == "__main__":
    sys.exit(main())
