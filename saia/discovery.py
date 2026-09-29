"""Неблокирующий предпросмотр публикационного корпуса OpenAlex + arXiv.

Модуль не называет результаты слабыми сигналами: поисковая выдача является
только входом для нормализации, временных тем и scoring pipeline.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urlencode

import httpx


OPENALEX_URL = "https://api.openalex.org/works"
ARXIV_URL = "https://export.arxiv.org/api/query"
ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"


@dataclass(frozen=True)
class Publication:
    canonical_key: str
    title: str
    abstract: str | None
    published_at: str
    sources: tuple[str, ...]
    source_ids: tuple[str, ...]
    urls: tuple[str, ...]
    doi: str | None
    authors: tuple[str, ...]
    openalex_type: str | None = None


@dataclass(frozen=True)
class DiscoveryResult:
    query: str
    date_from: str
    as_of_date: str
    query_hash: str
    fetched_at: str
    works: tuple[Publication, ...]
    source_counts: dict[str, int]
    errors: dict[str, str]
    search_plan: dict | None = None
    collection_policy: str = "preview-date-and-version-v2"
    limitations: tuple[str, ...] = (
        "Ограниченный предпросмотр не доказывает полноту и не задаёт знаменатель области.",
        "Редакции arXiv после даты среза исключены; ранний текст v1 здесь не восстанавливается.",
        "OpenAlex отдаёт современные метаданные: исторический поиск является реконструкцией.",
    )
    warning: str = (
        "Это корпус-кандидат. Публикации ещё не являются слабыми сигналами "
        "до кластеризации, временного анализа и прохождения шлюзов."
    )

    def to_dict(self) -> dict:
        return asdict(self)


def _clean_title(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _title_key(value: str) -> str:
    return re.sub(r"[^a-zа-яё0-9]+", " ", _clean_title(value).casefold()).strip()


def _doi_key(value: str | None) -> str | None:
    if not value:
        return None
    return value.casefold().removeprefix("https://doi.org/").removeprefix("doi:").strip()


def _abstract_from_inverted(index: dict | None) -> str | None:
    if not index:
        return None
    positions = [(int(position), word) for word, values in index.items() for position in values]
    return " ".join(word for _, word in sorted(positions)) or None


def _within_cutoff(value: str, as_of: date) -> bool:
    try:
        return date.fromisoformat(value[:10]) < as_of
    except (TypeError, ValueError):
        return False


def _source_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 429:
            return "Источник временно ограничил частоту запросов (HTTP 429)."
        return f"Источник ответил ошибкой HTTP {status}."
    if isinstance(exc, httpx.TimeoutException):
        return "Источник не ответил за отведённое время."
    return f"Не удалось получить источник: {type(exc).__name__}."


def collect_openalex(client: httpx.Client, query: str, date_from: date,
                     as_of: date, limit: int, query_expression: str | None = None) -> list[dict]:
    params = {
        "search": query_expression if query_expression is not None else query,
        "filter": (
            f"from_publication_date:{date_from.isoformat()},"
            f"to_publication_date:{(as_of - timedelta(days=1)).isoformat()}"
        ),
        "per_page": min(limit, 100),
        "select": (
            "id,doi,display_name,publication_date,abstract_inverted_index,"
            "authorships,primary_location,type"
        ),
    }
    mailto = os.environ.get('OPENALEX_MAILTO') or os.environ.get('SAIA_MAILTO')
    if mailto:
        params['mailto'] = mailto
    key = os.environ.get("OPENALEX_API_KEY")
    # Request-scoped header: no credential in URL/history, no shared client
    # Authorization that could reach the following arXiv request.
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    for attempt in range(3):
        response = client.get(OPENALEX_URL, params=params, headers=headers)
        try:
            response.raise_for_status()
            break
        except httpx.HTTPStatusError:
            if response.status_code != 429 or attempt == 2:
                raise
            retry_after = response.headers.get("Retry-After")
            try:
                delay = float(retry_after) if retry_after is not None else 0.0
            except ValueError:
                delay = 0.0
            if not math.isfinite(delay) or delay < 0:
                delay = 0.0
            # Bounded delay keeps a multi-branch job responsive even if the
            # source is throttling. A failed branch remains visibly partial.
            time.sleep(min(10.0, max(delay, 1.5 * (2 ** attempt))))
    return list(response.json().get("results", []))


def collect_arxiv(client: httpx.Client, query: str, date_from: date,
                  as_of: date, limit: int, query_expression: str | None = None) -> list[dict]:
    # Ограничение времени должно действовать ДО сортировки и лимита.
    # Иначе современный TOP-N уничтожает полноту исторического поиска.
    # Упрощённый предпросмотр всё ещё ограничен N и не является корпусом области.
    literal = re.sub(r'["\\\x00-\x1f]', ' ', query).strip()
    date_range = (
        f"submittedDate:[{date_from.strftime('%Y%m%d')}0000 TO "
        f"{(as_of - timedelta(days=1)).strftime('%Y%m%d')}2359]"
    )
    params = {
        "search_query": f'({query_expression}) AND {date_range}' if query_expression is not None else f'(all:"{literal}") AND {date_range}',
        "start": 0,
        "max_results": min(limit, 100),
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    # export.arxiv.org currently returns 406 for an otherwise identical URL
    # when percent-escape hex letters are upper-case (httpx's params default),
    # while curl's lower-case escapes succeed. Percent escapes are
    # case-insensitive by RFC; lower only the escape triplets, never query text.
    encoded = urlencode(params)
    encoded = re.sub(r"%[0-9A-F]{2}", lambda match: match.group(0).lower(), encoded)
    response = client.get(f"{ARXIV_URL}?{encoded}")
    response.raise_for_status()
    root = ET.fromstring(response.content)
    rows = []
    for entry in root.findall(f"{ATOM}entry"):
        published = entry.findtext(f"{ATOM}published") or ""
        if not _within_cutoff(published, as_of) or published[:10] < date_from.isoformat():
            continue
        updated = entry.findtext(f"{ATOM}updated") or published
        # published относится к v1, но summary/title могут быть от поздней
        # редакции. Такой текст не используем как историческое доказательство.
        # Получение самой v1 будет отдельным этапом; здесь не подменяем её.
        if not _within_cutoff(updated, as_of):
            continue
        rows.append({
            "id": entry.findtext(f"{ATOM}id"),
            "title": entry.findtext(f"{ATOM}title"),
            "summary": entry.findtext(f"{ATOM}summary"),
            "published": published,
            "doi": entry.findtext(f"{ARXIV}doi"),
            "authors": [
                author.findtext(f"{ATOM}name") or ""
                for author in entry.findall(f"{ATOM}author")
            ],
        })
    return rows


def _openalex_publications(rows: Iterable[dict], as_of: date) -> list[Publication]:
    result = []
    for row in rows:
        published = row.get("publication_date") or ""
        if not _within_cutoff(published, as_of):
            continue
        doi = _doi_key(row.get("doi"))
        title = _clean_title(row.get("display_name") or "")
        source_id = str(row.get("id") or "")
        landing = ((row.get("primary_location") or {}).get("landing_page_url") or source_id)
        key = f"doi:{doi}" if doi else f"title:{_title_key(title)}"
        result.append(Publication(
            canonical_key=key, title=title,
            abstract=_abstract_from_inverted(row.get("abstract_inverted_index")),
            published_at=published, sources=("openalex",), source_ids=(source_id,),
            urls=tuple(value for value in (landing,) if value), doi=doi,
            authors=tuple(
                str((authorship.get("author") or {}).get("display_name") or "").strip()
                for authorship in row.get("authorships", [])
                if (authorship.get("author") or {}).get("display_name")
            ),
            openalex_type=str(row.get("type") or "").strip() or None,
        ))
    return result


def _arxiv_publications(rows: Iterable[dict]) -> list[Publication]:
    result = []
    for row in rows:
        title = _clean_title(row.get("title") or "")
        doi = _doi_key(row.get("doi"))
        source_id = str(row.get("id") or "")
        key = f"doi:{doi}" if doi else f"title:{_title_key(title)}"
        result.append(Publication(
            canonical_key=key, title=title,
            abstract=_clean_title(row.get("summary") or "") or None,
            published_at=str(row.get("published") or "")[:10],
            sources=("arxiv",), source_ids=(source_id,), urls=(source_id,), doi=doi,
            authors=tuple(name for name in row.get("authors", []) if name),
        ))
    return result


def deduplicate(publications: Iterable[Publication]) -> tuple[Publication, ...]:
    from saia.preview_identity import same_publication

    grouped: list[Publication] = []
    for item in publications:
        match_index = next((index for index, current in enumerate(grouped)
                            if same_publication(current, item)), None)
        if match_index is None:
            grouped.append(item)
            continue
        current = grouped[match_index]
        grouped[match_index] = Publication(
            canonical_key=current.canonical_key if current.doi else item.canonical_key,
            title=current.title if len(current.title) >= len(item.title) else item.title,
            abstract=current.abstract or item.abstract,
            published_at=min(current.published_at, item.published_at),
            sources=tuple(sorted(set(current.sources + item.sources))),
            source_ids=tuple(dict.fromkeys(current.source_ids + item.source_ids)),
            urls=tuple(dict.fromkeys(current.urls + item.urls)),
            doi=current.doi or item.doi,
            authors=tuple(dict.fromkeys(current.authors + item.authors)),
            openalex_type=current.openalex_type or item.openalex_type,
        )
    return tuple(sorted(grouped, key=lambda work: (work.published_at, work.title)))


def discover(query: str, date_from: date, as_of: date, limit_per_source: int = 50,
             client: httpx.Client | None = None, search_plan: dict | None = None) -> DiscoveryResult:
    if not query.strip():
        raise ValueError("query не может быть пустым")
    if date_from >= as_of:
        raise ValueError("date_from должна быть раньше as_of_date")
    if not 1 <= limit_per_source <= 100:
        raise ValueError('Лимит предпросмотра должен быть от 1 до 100 на источник.')
    if search_plan is not None:
        from saia.query_expansion import compile_plan
        checked = compile_plan(search_plan['original_query'], search_plan['included_terms'][1:],
                               search_plan['exclusions'], date_from, as_of, search_plan['effective_policy'])
        if 'matching_version' not in search_plan:
            # Historical saved plans predate orthographic matching.
            checked.pop('matching_version')
        if checked != search_plan or query != search_plan['original_query']:
            raise ValueError('План поиска не совпадает с сохранёнными фразами и периодом.')
    own_client = client is None
    client = client or httpx.Client(
        timeout=30,
        follow_redirects=True,
        headers={"User-Agent": "SAIA/0.4.42 research prototype"},
    )
    errors = {}
    openalex_rows = []
    arxiv_rows = []
    try:
        try:
            if search_plan is None:
                openalex_rows = collect_openalex(client, query, date_from, as_of, limit_per_source)
            else:
                openalex_rows = collect_openalex(client, query, date_from, as_of, limit_per_source,
                                                search_plan['logical_expressions']['openalex'])
        except Exception as exc:  # один источник не должен уничтожать весь запрос
            errors["openalex"] = _source_error(exc)
        try:
            if search_plan is None:
                arxiv_rows = collect_arxiv(client, query, date_from, as_of, limit_per_source)
            else:
                arxiv_rows = collect_arxiv(client, query, date_from, as_of, limit_per_source,
                                          search_plan['logical_expressions']['arxiv'])
        except Exception as exc:
            # The public endpoint has been observed to return 406 for
            # max_results > 1 while the identical one-record request works.
            # Keep a visibly partial preview instead of turning the source into
            # zero coverage. Full analysis uses the pinned local arXiv mirror.
            response = getattr(exc, "response", None)
            if isinstance(exc, httpx.HTTPStatusError) and response is not None \
                    and response.status_code == 406 and limit_per_source > 1:
                try:
                    # arXiv asks clients to leave roughly three seconds between
                    # requests. An immediate retry is itself likely to be
                    # rejected and would make the graceful fallback unreliable.
                    time.sleep(3.1)
                    if search_plan is None:
                        arxiv_rows = collect_arxiv(client, query, date_from, as_of, 1)
                    else:
                        arxiv_rows = collect_arxiv(
                            client, query, date_from, as_of, 1,
                            search_plan['logical_expressions']['arxiv'],
                        )
                    errors["arxiv"] = (
                        "Публичный arXiv API отклонил запрошенный объём HTTP 406; "
                        "получена только одна контрольная запись. Это частичное "
                        "покрытие, а не нулевой результат."
                    )
                except Exception as fallback_exc:
                    errors["arxiv"] = _source_error(fallback_exc)
            else:
                errors["arxiv"] = _source_error(exc)
    finally:
        if own_client:
            client.close()
    works = deduplicate(
        _openalex_publications(openalex_rows, as_of) + _arxiv_publications(arxiv_rows)
    )
    query_payload = {
        "query": query.strip(), "date_from": date_from.isoformat(),
        "as_of_date": as_of.isoformat(), "limit_per_source": limit_per_source,
        "collection_policy": "preview-controlled-query-v3" if search_plan is not None else "preview-date-and-version-v2",
    }
    if search_plan is not None:
        query_payload['search_plan'] = search_plan
    query_hash = hashlib.sha256(
        json.dumps(query_payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]
    return DiscoveryResult(
        query=query.strip(), date_from=date_from.isoformat(), as_of_date=as_of.isoformat(),
        query_hash=query_hash, fetched_at=datetime.now(timezone.utc).isoformat(),
        works=works,
        source_counts={"openalex": len(openalex_rows), "arxiv": len(arxiv_rows)},
        errors=errors,
        search_plan=search_plan,
        collection_policy=query_payload['collection_policy'],
    )


def discover_openalex_only(
    query: str, date_from: date, as_of: date, limit_per_source: int = 50,
    client: httpx.Client | None = None,
) -> DiscoveryResult:
    """Collect OpenAlex when arXiv is intentionally supplied by a pinned local scan."""
    if not query.strip():
        raise ValueError("query не может быть пустым")
    if date_from >= as_of:
        raise ValueError("date_from должна быть раньше as_of_date")
    if not 1 <= limit_per_source <= 100:
        raise ValueError("Лимит предпросмотра должен быть от 1 до 100 на источник.")
    own_client = client is None
    client = client or httpx.Client(
        timeout=30, follow_redirects=True,
        headers={"User-Agent": "SAIA/0.4.42 research prototype"},
    )
    rows, errors = [], {}
    try:
        try:
            rows = collect_openalex(client, query, date_from, as_of, limit_per_source)
        except Exception as exc:
            errors["openalex"] = _source_error(exc)
    finally:
        if own_client:
            client.close()
    works = deduplicate(_openalex_publications(rows, as_of))
    query_payload = {
        "query": query.strip(), "date_from": date_from.isoformat(),
        "as_of_date": as_of.isoformat(), "limit_per_source": limit_per_source,
        "collection_policy": "openalex-live-with-pinned-local-arxiv-v1",
    }
    query_hash = hashlib.sha256(
        json.dumps(query_payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]
    return DiscoveryResult(
        query=query.strip(), date_from=date_from.isoformat(),
        as_of_date=as_of.isoformat(), query_hash=query_hash,
        fetched_at=datetime.now(timezone.utc).isoformat(), works=works,
        source_counts={"openalex": len(rows), "arxiv": 0}, errors=errors,
        collection_policy=query_payload["collection_policy"],
        limitations=(
            "OpenAlex — ограниченный текущий API preview; полнота не доказана.",
            "Публичный arXiv намеренно не запрашивался: ветвь обслуживается закреплённым локальным снимком.",
            "Результаты являются корпусом-кандидатом, а не слабыми сигналами.",
        ),
    )


def save_snapshot(result: DiscoveryResult, directory: str | Path) -> Path:
    destination = Path(directory) / result.query_hash
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / "discovery.json"
    path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path
