#!/usr/bin/env python3
"""SAIA — загрузка сырых данных из OpenAlex и arXiv.

Запускается в обычном терминале macOS, где открыт интернет.
Никаких зависимостей: только стандартная библиотека Python 3.9+.

    python3 scripts/fetch.py missions/gnn-fraud.json

Что делает:
  1. Читает миссию (запрос, словарь синонимов, период, источники).
  2. Тянет OpenAlex постранично через cursor, arXiv — через Atom API.
  3. Кладёт СЫРЫЕ ответы как есть в data/raw/<mission>/, ничего не меняя.
  4. Пишет manifest.json: время загрузки, URL, http-код, число записей,
     sha256 каждого файла, версия коннектора.

Что НЕ делает намеренно:
  - не считает дату в текущей библиографии доказательством доступности
    исторического текста. Период поиска ограничивается миссией, а версии
    и пригодность для среза дополнительно проверяются при анализе.
  - не нормализует и не дедуплицирует. Сырое остаётся сырым.

Повторный запуск не перекачивает уже скачанные страницы. Чтобы перекачать
заново: --refetch
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

CONNECTOR_VERSION = "0.4.2"

OPENALEX_API = "https://api.openalex.org/works"
ARXIV_API = "https://export.arxiv.org/api/query"
# Канонический OAI endpoint без промежуточного redirect. Через
# export.arxiv.org urllib периодически получал ложный 406 после redirect,
# тогда как конечный endpoint стабильно возвращает ту же запись.
ARXIV_OAI = "https://oaipmh.arxiv.org/oai"
HF_DATASET_RESOLVE = "https://huggingface.co/datasets"

# Параметр select намеренно НЕ используется.
#
# Сначала поля перечислялись явно, ради размера ответа — и это оказалось
# неверно по двум причинам. Во-первых, select делает сохранённое не сырым
# ответом, а проекцией: мы бы фильтровали данные уже при загрузке, что
# противоречит требованию неизменности сырья. Во-вторых, список полей —
# внешний контракт, который меняется на стороне OpenAlex: первая же попытка
# споткнулась о поле is_oa, которое на верхнем уровне называется open_access.
#
# Поэтому сохраняем ответ целиком. Размер контролируется числом страниц, а не
# усечением записей. Важное следствие: в записи остаётся counts_by_year —
# погодовая разбивка цитирований. Без неё ретроспективный срез невозможен:
# cited_by_count это ТЕКУЩЕЕ число цитирований, то есть знание будущего.

# arXiv просит не чаще одного запроса в 3 секунды и указывать источник данных.
ARXIV_DELAY_SECONDS = 3.0
OPENALEX_DELAY_SECONDS = 0.2
OPENALEX_MIN_FREE_BYTES = 3 * 1024 ** 3
OPENALEX_MAX_NEW_BYTES = 1024 ** 3
ARXIV_ID_IN_URL = re.compile(
    r"arxiv(?:\.org/(?:abs|pdf)/|:|\.)(\d{4}\.\d{4,5}|[a-z-]+/\d{7})",
    re.IGNORECASE,
)
SNAPSHOT_SUBMITTED_DATE = re.compile(r"(?<!\d)(\d{1,2} [A-Z][a-z]{2} \d{4})(?!\d)")


class FetchError(RuntimeError):
    pass


class CredentialSafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            before, after = urllib.parse.urlsplit(req.full_url), urllib.parse.urlsplit(newurl)
            if after.scheme != 'https' or before.netloc != after.netloc:
                redirected.remove_header('Authorization')
        return redirected


HTTP_OPENER = urllib.request.build_opener(CredentialSafeRedirect())


def safe_http_text(value) -> str:
    text = str(value)
    secret = os.environ.get('OPENALEX_API_KEY')
    return text.replace(secret, '[REDACTED]') if secret else text


def log(message: str) -> None:
    print(message, flush=True)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def http_get(url: str, *, accept: str, attempts: int = 4) -> tuple[bytes, int]:
    """GET с повторами на временных ошибках.

    429 и 5xx — ждём и пробуем снова. 4xx кроме 429 — ошибка запроса,
    повторять бессмысленно, падаем сразу и показываем URL.
    """
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(
            url,
            headers={
                "Accept": accept,
                "User-Agent": f"SAIA/{CONNECTOR_VERSION} (weak signal research prototype)",
            },
        )
        target = urllib.parse.urlsplit(url)
        key = os.environ.get('OPENALEX_API_KEY')
        if target.scheme == 'https' and target.netloc == 'api.openalex.org' and key:
            request.add_header('Authorization', f'Bearer {key}')
        try:
            with HTTP_OPENER.open(request, timeout=60) as response:
                return response.read(), response.status
        except urllib.error.HTTPError as error:
            last_error = error
            # arXiv OAI иногда использует 406 как временный throttle даже
            # при корректном Accept. Для остальных 4xx повтор бессмысленен.
            if error.code in (406, 429) or error.code >= 500:
                wait = min(60, 2 ** attempt * 2)
                log(f"    HTTP {error.code}, повтор через {wait} с (попытка {attempt}/{attempts})")
                time.sleep(wait)
                continue
            # Ответ источника показывается целиком: усечённое сообщение об ошибке
            # прячет ровно ту часть, ради которой его читают.
            raise FetchError(
                safe_http_text(f"HTTP {error.code} от источника.\n  URL: {url}\n  Ответ: {error.read()[:4000].decode('utf-8', 'replace')}")
            ) from error
        except (urllib.error.URLError, TimeoutError) as error:
            last_error = error
            reason = safe_http_text(getattr(error, "reason", str(error)))
            wait = min(60, 2 ** attempt * 2)
            log(f"    Сеть недоступна ({reason}), повтор через {wait} с (попытка {attempt}/{attempts})")
            time.sleep(wait)

    raise FetchError(safe_http_text(f"не удалось получить {url} за {attempts} попыток: {last_error}"))


def build_openalex_filter(mission: dict) -> str:
    """Фильтр OpenAlex из словаря синонимов миссии.

    Термины объединяются через OR внутри одного поля поиска. Словарь лежит в
    файле миссии и версионируется вместе с ней — это и есть шаг расширения
    запроса из конвейера, а не скрытая эвристика внутри кода.
    """
    terms = mission["query"]["terms"]
    if not terms:
        raise FetchError("в миссии не задано ни одного термина (query.terms)")
    joined = " OR ".join(f'"{term}"' for term in terms)
    parts = [f"title_and_abstract.search:{joined}"]

    period = mission.get("period", {})
    if period.get("from"):
        parts.append(f"from_publication_date:{period['from']}")
    if period.get("to"):
        parts.append(f"to_publication_date:{period['to']}")

    for extra in mission["query"].get("openalex_filters", []):
        parts.append(extra)

    return ",".join(parts)


def openalex_cursor_pages(params: dict, out_dir: Path, prefix: str, max_pages: int,
                          refetch: bool, *, min_free_bytes: int = OPENALEX_MIN_FREE_BYTES,
                          max_new_bytes: int = OPENALEX_MAX_NEW_BYTES) -> list[dict]:
    """Full raw pages with explicit count/cursor audit; partial is never complete."""
    if max_pages < 1:
        raise FetchError('Лимит страниц OpenAlex должен быть положительным.')
    if 'api_key' in params:
        raise FetchError('Ключ OpenAlex передаётся только заголовком из окружения, не URL.')
    if min_free_bytes < 0 or max_new_bytes < 1:
        raise FetchError('Некорректный лимит свободного места для OpenAlex')
    records, seen_ids, seen_cursors = [], set(), set()
    new_bytes = 0
    cursor, expected = '*', None
    for page in range(1, max_pages + 1):
        if cursor in seen_cursors:
            records.append({'file': None, 'incomplete': True, 'reason': 'OpenAlex повторил cursor'})
            return records
        seen_cursors.add(cursor)
        public_params = {**params, 'per_page': '100', 'cursor': cursor}
        url = f'{OPENALEX_API}?{urllib.parse.urlencode(public_params)}'
        path = out_dir / f'{prefix}{page:04d}.json'
        reused = path.exists() and not refetch
        if reused:
            body, status = path.read_bytes(), None
        else:
            body, status = http_get(url, accept='application/json')
            free_bytes = shutil.disk_usage(out_dir).free
            if free_bytes - len(body) < min_free_bytes or new_bytes + len(body) > max_new_bytes:
                records.append({
                    'file': None, 'incomplete': True,
                    'reason': 'OpenAlex остановлен лимитом места; страница не сохранена',
                    'free_bytes_before_page': free_bytes,
                    'minimum_free_bytes': min_free_bytes,
                    'new_bytes_written': new_bytes,
                    'max_new_bytes': max_new_bytes,
                    'source_reported_count': expected,
                    'unique_records': len(seen_ids),
                })
                return records
            temporary = path.with_suffix(path.suffix + '.tmp')
            temporary.write_bytes(body)
            temporary.replace(path)
            new_bytes += len(body)
            time.sleep(OPENALEX_DELAY_SECONDS)
        try:
            payload = json.loads(body)
            meta, results = payload['meta'], payload['results']
            count, next_cursor = meta['count'], meta['next_cursor']
            if (not isinstance(count, int) or isinstance(count, bool) or count < 0
                    or not isinstance(results, list)
                    or (next_cursor is not None and (not isinstance(next_cursor, str) or not next_cursor))):
                raise ValueError('invalid count/results/cursor')
            ids = [row['id'] for row in results]
            if any(not isinstance(i, str) or not re.fullmatch(r'https://openalex.org/W\d+', i) for i in ids):
                raise ValueError('invalid OpenAlex work ID')
        except (KeyError, TypeError, ValueError) as error:
            raise FetchError(f'Некорректная OpenAlex страница {path.name}: {safe_http_text(error)}') from error
        records.append({'file': path.name, 'http_status': status, 'url': url,
                        'records': len(results), 'sha256': sha256_of(path), 'reused': reused,
                        'source_reported_count': count, 'cursor_requested': cursor,
                        'cursor_next': next_cursor})
        reason = None
        if expected is None:
            expected = count
        elif count != expected:
            reason = 'OpenAlex изменил meta.count во время выгрузки; стабильный срез не доказан'
        if len(ids) != len(set(ids)) or seen_ids.intersection(ids):
            reason = reason or 'OpenAlex повторил work ID между/внутри страниц; полнота не доказана'
        seen_ids.update(ids)
        if len(seen_ids) > expected:
            reason = reason or 'Число уникальных результатов превысило OpenAlex meta.count'
        if not results and next_cursor is not None:
            reason = reason or 'Пустая страница OpenAlex с продолжающимся cursor'
        if next_cursor is None and len(seen_ids) != expected:
            reason = reason or 'OpenAlex завершил cursor, но число уникальных результатов не совпало с meta.count'
        if reason:
            records.append({'file': None, 'incomplete': True, 'reason': reason,
                            'unique_records': len(seen_ids), 'source_reported_count': expected})
            return records
        if next_cursor is None:
            records[-1]['cursor_audit_complete'] = True
            records[-1]['unique_records_in_query'] = len(seen_ids)
            return records
        cursor = next_cursor
    records.append({'file': None, 'incomplete': True,
                    'reason': f'OpenAlex: исчерпан лимит страниц {max_pages}, cursor не завершён',
                    'unique_records': len(seen_ids), 'source_reported_count': expected})
    return records


def _month_windows(start: str, end: str) -> list[tuple[date, date]]:
    """Return inclusive calendar-month windows clipped to the requested period."""
    try:
        first = date.fromisoformat(start)
        last = date.fromisoformat(end)
    except (TypeError, ValueError) as error:
        raise FetchError(
            "openalex_monthly_bounded требует period.from/to в формате YYYY-MM-DD"
        ) from error
    if first > last:
        raise FetchError("period.from не может быть позже period.to")
    windows = []
    cursor = first
    while cursor <= last:
        next_month = (
            date(cursor.year + 1, 1, 1)
            if cursor.month == 12
            else date(cursor.year, cursor.month + 1, 1)
        )
        window_end = min(last, next_month - timedelta(days=1))
        windows.append((cursor, window_end))
        cursor = next_month
    return windows


def fetch_openalex_monthly_bounded(mission: dict, out_dir: Path, mailto: str | None,
                                    refetch: bool) -> list[dict]:
    """Collect one explicitly bounded relevance-ranked page per calendar month.

    This mode is an exploration sample, not a complete corpus. It deliberately
    trades completeness for temporal coverage and therefore always appends an
    ``incomplete`` manifest record explaining the sampling boundary.
    """
    config = mission["query"].get("openalex_monthly_bounded")
    if not isinstance(config, dict):
        raise FetchError("openalex_monthly_bounded должен быть объектом")
    per_month = config.get("per_month", 80)
    if (not isinstance(per_month, int) or isinstance(per_month, bool)
            or not 1 <= per_month <= 100):
        raise FetchError("openalex_monthly_bounded.per_month должен быть целым от 1 до 100")
    sort = config.get("sort", "relevance_score:desc")
    if sort not in ("relevance_score:desc", "publication_date:asc", "publication_date:desc"):
        raise FetchError(
            "openalex_monthly_bounded.sort допускает relevance_score:desc или publication_date:asc/desc"
        )
    period = mission.get("period") or {}
    windows = _month_windows(period.get("from"), period.get("to"))

    out_dir.mkdir(parents=True, exist_ok=True)
    if refetch:
        removed = clear_pages(out_dir, ".json")
        if removed:
            log(f"  удалено старых месячных страниц: {removed}")
    guard_query_hash(
        out_dir,
        {"mode": "openalex-monthly-bounded-v1", "query": mission["query"], "period": period},
        refetch,
    )

    records, seen_ids = [], set()
    source_total = 0
    for window_start, window_end in windows:
        monthly_mission = {
            **mission,
            "period": {"from": window_start.isoformat(), "to": window_end.isoformat()},
        }
        params = {
            "filter": build_openalex_filter(monthly_mission),
            "per_page": str(per_month),
            "page": "1",
            "sort": sort,
        }
        if mailto:
            params["mailto"] = mailto
        url = f"{OPENALEX_API}?{urllib.parse.urlencode(params)}"
        path = out_dir / f"month_{window_start:%Y_%m}.json"
        reused = path.exists() and not refetch
        if reused:
            body, status = path.read_bytes(), None
        else:
            body, status = http_get(url, accept="application/json")
            path.write_bytes(body)
            time.sleep(OPENALEX_DELAY_SECONDS)
        try:
            payload = json.loads(body)
            meta, results = payload["meta"], payload["results"]
            count = meta["count"]
            if (not isinstance(count, int) or isinstance(count, bool) or count < 0
                    or not isinstance(results, list) or len(results) > per_month):
                raise ValueError("invalid count/results")
            ids = [row["id"] for row in results]
            if any(not isinstance(item, str) or not re.fullmatch(r"https://openalex.org/W\d+", item)
                   for item in ids):
                raise ValueError("invalid OpenAlex work ID")
            if len(ids) != len(set(ids)) or seen_ids.intersection(ids):
                raise ValueError("duplicate OpenAlex work ID across monthly windows")
            for row in results:
                publication_date = date.fromisoformat(row["publication_date"])
                if not window_start <= publication_date <= window_end:
                    raise ValueError(
                        f"publication_date {publication_date} outside {window_start}..{window_end}"
                    )
        except (KeyError, TypeError, ValueError) as error:
            raise FetchError(
                f"Некорректная месячная OpenAlex страница {path.name}: {safe_http_text(error)}"
            ) from error
        seen_ids.update(ids)
        source_total += count
        records.append({
            "file": path.name,
            "http_status": status,
            "url": url,
            "records": len(results),
            "sha256": sha256_of(path),
            "reused": reused,
            "month_from": window_start.isoformat(),
            "month_to": window_end.isoformat(),
            "source_reported_count": count,
            "sampling_rank": sort,
            "per_month_cap": per_month,
        })
    records.append({
        "file": None,
        "incomplete": True,
        "reason": (
            "OpenAlex: помесячная ограниченная выборка по релевантности; "
            f"не более {per_month} работ на месяц, полный корпус не выгружен"
        ),
        "sampling_design": "one_ranked_page_per_calendar_month",
        "months": len(windows),
        "unique_records": len(seen_ids),
        "source_reported_count_disjoint_months": source_total,
    })
    return records


def fetch_openalex(mission: dict, out_dir: Path, mailto: str | None, refetch: bool) -> list[dict]:
    """Постраничная выгрузка через cursor. Каждая страница — отдельный файл."""
    if mission["query"].get("openalex_monthly_bounded") is not None:
        return fetch_openalex_monthly_bounded(mission, out_dir, mailto, refetch)
    out_dir.mkdir(parents=True, exist_ok=True)
    if refetch:
        removed = clear_pages(out_dir, ".json")
        if removed:
            log(f"  удалено старых страниц: {removed}")
    guard_query_hash(out_dir, {"mode": "openalex-cursor-count-audit-v2", "query": mission["query"],
                               "period": mission.get("period")}, refetch)
    # Предохранитель на первый прогон: широкий запрос может дать сотни страниц.
    # Поднимается в файле миссии, когда объём осознан.
    max_pages = int(mission["query"].get("openalex_max_pages", 40))

    params = {'filter': build_openalex_filter(mission)}
    sort = mission["query"].get("openalex_sort")
    if sort is not None:
        if sort not in ("publication_date:asc", "publication_date:desc"):
            raise FetchError(
                "openalex_sort для воспроизводимого корпуса допускает только "
                "publication_date:asc или publication_date:desc"
            )
        params["sort"] = sort
    if mailto:
        params['mailto'] = mailto
    return openalex_cursor_pages(params, out_dir, 'page_', max_pages, refetch)


def fetch_openalex_field_baseline(mission: dict, out_dir: Path, mailto: str | None) -> dict | None:
    """Знаменатель для relative volume: число работ родительской области по годам.

    Тянется агрегатом через group_by, а не выгрузкой корпуса: для доли темы
    внутри области нужны только счётчики, а не сами записи. Иначе пришлось бы
    скачивать весь машинный интеллект целиком.
    """
    parent = mission.get("parent_field")
    if not parent:
        return None

    out_dir.mkdir(parents=True, exist_ok=True)
    params = {
        "filter": parent["openalex_filter"],
        "group_by": "publication_year",
        "per_page": "100",
    }
    if mailto:
        params["mailto"] = mailto

    url = f"{OPENALEX_API}?{urllib.parse.urlencode(params)}"
    body, status = http_get(url, accept="application/json")
    path = out_dir / "field_baseline_by_year.json"
    path.write_bytes(body)

    payload = json.loads(body.decode("utf-8"))
    groups = payload.get("group_by", [])
    log(f"  база области: {len(groups)} лет")
    return {
        "file": path.name, "http_status": status, "url": url,
        "records": len(groups), "sha256": sha256_of(path), "reused": False,
    }


def fetch_arxiv_oai(mission: dict, out_dir: Path, refetch: bool) -> list[dict]:
    """Массовая выгрузка метаданных через OAI-PMH.

    Atom API предназначен для поиска и на больших объёмах отвечает 429.
    OAI-PMH — штатный интерфейс arXiv для выгрузки метаданных: тысяча
    записей за запрос, продолжение по resumptionToken.

    ВАЖНОЕ ОГРАНИЧЕНИЕ, которое нельзя забыть при разборе. Параметры from и
    until в OAI-PMH фильтруют по дате ИЗМЕНЕНИЯ записи, а не по дате подачи
    статьи. Работа 2012 года, обновлённая в 2019-м, имеет datestamp 2019.
    Поэтому окно запроса берётся с запасом, а отбор по дате подачи делается
    при разборе — по полю created. Следствие: работы нужного периода,
    последний раз изменённые после конца окна, в выборку не попадут. Это
    разрыв покрытия, и он идёт в coverage confidence, а не замалчивается.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    if refetch:
        removed = clear_pages(out_dir, ".xml")
        if removed:
            log(f"  удалено старых страниц: {removed}")

    oai = mission["query"].get("arxiv_oai", {})
    guard_query_hash(out_dir, {"mode": "oai", "oai": oai,
                               "categories": mission["query"].get("arxiv_categories"),
                               "period": mission.get("period")}, refetch)
    oai_set = oai.get("set", "cs")
    date_from = oai.get("datestamp_from") or mission.get("period", {}).get("from")
    date_until = oai.get("datestamp_until")

    params = {"verb": "ListRecords", "metadataPrefix": "arXiv", "set": oai_set}
    if date_from:
        params["from"] = date_from
    if date_until:
        params["until"] = date_until

    records: list[dict] = []
    page = 0
    token: str | None = None
    max_pages = int(oai.get("max_pages", 400))

    log(f"  набор: {oai_set}, окно datestamp: {date_from or '—'} .. {date_until or '—'}")

    while page < max_pages:
        page += 1
        path = out_dir / f"page_{page:04d}.xml"

        if path.exists() and not refetch:
            body = path.read_bytes()
            token = extract_resumption_token(body)
            records.append({
                "file": path.name, "http_status": None, "url": None,
                "records": body.count(b"<record>"), "sha256": sha256_of(path), "reused": True,
            })
            log(f"  страница {page}: уже скачана, пропускаю")
            if not token:
                break
            continue

        query = ({"verb": "ListRecords", "resumptionToken": token} if token else params)
        url = f"{ARXIV_OAI}?{urllib.parse.urlencode(query)}"
        body, status = http_get(url, accept="application/xml")
        path.write_bytes(body)

        count = body.count(b"<record>")
        records.append({
            "file": path.name, "http_status": status, "url": url,
            "records": count, "sha256": sha256_of(path), "reused": False,
        })
        total = extract_complete_size(body)
        log(f"  страница {page}: {count} записей"
            + (f" (всего в наборе {total})" if total and page == 1 else ""))

        token = extract_resumption_token(body)
        if not token or count == 0:
            break
        time.sleep(ARXIV_DELAY_SECONDS)

    if page >= max_pages and token:
        # Было просто сообщение в лог. Незавершённая выгрузка внешне
        # неотличима от завершённой, а разница в том, полон корпус или нет.
        log(f"  ВЫГРУЗКА НЕ ЗАВЕРШЕНА: достигнут предел max_pages={max_pages}, "
            f"сервер предлагает продолжение")
        records.append({"file": None, "incomplete": True,
                        "reason": f"max_pages={max_pages} исчерпан, resumptionToken не пуст"})

    return records


def _month_range(date_from: str, date_to: str):
    """Все календарные месяцы, пересекающие закрытый интервал дат."""
    start_year, start_month = map(int, date_from[:7].split("-"))
    end_year, end_month = map(int, date_to[:7].split("-"))
    year, month = start_year, start_month
    while (year, month) <= (end_year, end_month):
        yield year, month
        month += 1
        if month == 13:
            year, month = year + 1, 1


def fetch_arxiv_snapshot(mission: dict, out_dir: Path, refetch: bool) -> list[dict]:
    """Скачать закреплённые помесячные Parquet-срезы метаданных arXiv.

    OAI-PMH фильтрует массовую выдачу по дате последнего изменения записи,
    а поисковый Atom API ограничивает большие постраничные запросы. Для
    слепого ретротеста нужен корпус по дате первой подачи. Этот режим не
    гарантирует полный срез категорий: разделы зеркала могут соответствовать
    только основной категории и пропускать межкатегорийные работы.
    Коннектор использует только метаданные arXiv из публичного
    зеркала, закреплённого на конкретном git revision. Сырые Parquet-файлы
    сохраняются без преобразования; найденные кандидаты затем подтверждаются
    официальными страницами arXiv и OpenAlex.
    """
    snapshot = mission["query"].get("arxiv_snapshot") or {}
    dataset = snapshot.get("dataset")
    revision = snapshot.get("revision")
    categories = mission["query"].get("arxiv_categories") or []
    period = mission.get("period") or {}
    date_from, date_to = period.get("from"), period.get("to")
    if not dataset or not revision:
        raise FetchError("arxiv_snapshot требует dataset и закреплённый revision")
    if not categories or not date_from or not date_to:
        raise FetchError("arxiv_snapshot требует категории и полный период миссии")

    out_dir.mkdir(parents=True, exist_ok=True)
    if refetch:
        removed = clear_pages(out_dir, ".parquet")
        if removed:
            log(f"  удалено старых файлов: {removed}")
    guard_query_hash(out_dir, {
        "mode": "hf-arxiv-parquet-snapshot", "dataset": dataset,
        "revision": revision, "categories": categories, "period": period,
    }, refetch)

    expected = [
        (category, year, month)
        for category in categories
        for year, month in _month_range(date_from, date_to)
    ]
    max_files = int(snapshot.get("max_files", 500))
    if len(expected) > max_files:
        raise FetchError(
            f"ожидается {len(expected)} файлов, выше предохранителя max_files={max_files}"
        )

    records: list[dict] = []
    try:
        import pyarrow.parquet as pq
    except ImportError as error:
        raise FetchError(
            "для Parquet-среза установите optional dependency: pip install -e '.[bulk]'"
        ) from error
    for number, (category, year, month) in enumerate(expected, 1):
        remote = f"data/{category}/{year:04d}/{month:02d}/00000000.parquet"
        local = out_dir / f"{category}_{year:04d}_{month:02d}.parquet"
        url = (
            f"{HF_DATASET_RESOLVE}/{dataset}/resolve/{revision}/{remote}"
            "?download=true"
        )
        if local.exists() and not refetch:
            status = None
            reused = True
        else:
            body, status = http_get(url, accept="application/octet-stream")
            local.write_bytes(body)
            reused = False
        records.append({
            "file": local.name, "http_status": status, "url": url,
            "records": pq.ParquetFile(local).metadata.num_rows,
            "sha256": sha256_of(local), "reused": reused,
            "upstream_path": remote,
        })
        if number == 1 or number % 24 == 0 or number == len(expected):
            log(f"  файлов {number}/{len(expected)}")
    return records


def arxiv_ids_from_parquet(paths: list[Path], date_from: str | None = None,
                           date_to: str | None = None) -> list[str]:
    try:
        import pyarrow.parquet as pq
    except ImportError as error:
        raise FetchError(
            "для чтения Parquet нужен optional dependency: pip install -e '.[bulk]'"
        ) from error
    identifiers: set[str] = set()
    for path in paths:
        names = set(pq.ParquetFile(path).schema_arrow.names)
        if {"arxiv_id", "submission_date"} <= names:
            schema_kind = "legacy_adapter"
            table = pq.read_table(path, columns=["arxiv_id", "submission_date"])
        elif {"id", "versions"} <= names:
            schema_kind = "native_snapshot"
            table = pq.read_table(path, columns=["id", "versions"])
        else:
            raise FetchError(f"неподдерживаемая схема arXiv Parquet: {path.name}")
        for row in table.to_pylist():
            if schema_kind == "legacy_adapter":
                identifier = row.get("arxiv_id")
                submitted_text = str(row.get("submission_date") or "")
            else:
                identifier = row.get("id")
                versions = row.get("versions") or []
                v1 = next((item for item in versions if item.get("version") == "v1"), None)
                submitted_text = str((v1 or {}).get("created") or "")
            match = SNAPSHOT_SUBMITTED_DATE.search(submitted_text)
            submitted = (
                datetime.strptime(match.group(1), "%d %b %Y").date().isoformat()
                if match else ""
            )
            if date_from and (not submitted or submitted < date_from):
                continue
            if date_to and (not submitted or submitted > date_to):
                continue
            if identifier:
                identifiers.add(str(identifier))
    return sorted(identifiers)


def fetch_openalex_arxiv_enrichment(mission: dict, arxiv_dir: Path,
                                    out_dir: Path, mailto: str | None,
                                    refetch: bool) -> list[dict]:
    """Обогатить широкий arXiv-корпус точными DOI-запросами в OpenAlex.

    Это не независимое обнаружение: OpenAlex получает уже известные arXiv ID.
    Режим нужен для аффилиаций и погодовых цитирований, причём при scoring
    учитываются только значения до ``as_of_date``.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    if refetch:
        removed = clear_pages(out_dir, ".json")
        if removed:
            log(f"  удалено старых страниц обогащения: {removed}")
    period = mission.get("period") or {}
    ids = arxiv_ids_from_parquet(
        sorted(arxiv_dir.glob("*.parquet")), period.get("from"), period.get("to")
    )
    enrich = mission["query"].get("openalex_enrich_arxiv_ids") or {}
    max_ids = int(enrich.get("max_ids", 30000))
    if len(ids) > max_ids:
        raise FetchError(f"arXiv ID {len(ids)}, выше предохранителя max_ids={max_ids}")
    batch_size = int(enrich.get("batch_size", 50))
    max_pages = int(enrich.get('max_pages_per_batch', 10))
    max_batches = enrich.get('max_batches')
    if not 1 <= batch_size <= 100 or max_pages < 1 or (max_batches is not None and
            (not isinstance(max_batches, int) or isinstance(max_batches, bool) or max_batches < 1)):
        raise FetchError('Некорректные batch_size/max_pages_per_batch/max_batches OpenAlex.')
    records: list[dict] = []
    guard_query_hash(out_dir, {
        "mode": "openalex-arxiv-doi-enrichment-v2-cursor-audit", "arxiv_ids_sha256": hashlib.sha256(
            "\n".join(ids).encode("utf-8")
        ).hexdigest(), "batch_size": batch_size,
    }, refetch)
    total_batches = (len(ids) + batch_size - 1) // batch_size
    for start in range(0, len(ids), batch_size):
        batch = ids[start:start + batch_size]
        page = start // batch_size + 1
        if max_batches is not None and page > max_batches:
            records.append({'file': None, 'incomplete': True,
                            'reason': f'OpenAlex enrichment: пилотный предел max_batches={max_batches}; остальные ID не запрошены',
                            'total_arxiv_ids': len(ids), 'requested_arxiv_ids': start})
            return records
        dois = "|".join(f"10.48550/arxiv.{identifier}" for identifier in batch)
        params = {"filter": f"doi:{dois}"}
        if mailto:
            params["mailto"] = mailto
        pages = openalex_cursor_pages(params, out_dir, f'batch_{page:04d}_page_', max_pages, refetch)
        first_file = True
        requested_dois = {f'10.48550/arxiv.{identifier}'.lower() for identifier in batch}
        for entry in pages:
            if not entry.get('file'):
                continue
            entry['requested_ids'] = len(batch) if first_file else 0
            entry['request_batch'] = page
            first_file = False
            rows = json.loads((out_dir / entry['file']).read_bytes())['results']
            returned = {str(row.get('doi') or '').lower().removeprefix('https://doi.org/').removeprefix('http://doi.org/') for row in rows}
            if not returned <= requested_dois:
                pages.append({'file': None, 'incomplete': True,
                              'reason': 'OpenAlex вернул DOI вне запрошенного пакета: результаты не приняты как точные совпадения'})
                break
        records.extend(pages)
        if any(f.get('incomplete') for f in pages):
            return records
        if page == 1 or page % 25 == 0 or page == total_batches:
            log(f"  OpenAlex-пакетов {page}/{total_batches}")
    return records


def openalex_linked_arxiv_ids(mission: dict, openalex_dir: Path) -> list[str]:
    """Найти arXiv ID, которые сам OpenAlex связал с работами корпуса.

    Это не самостоятельное обнаружение темы в arXiv. Режим служит для
    подтверждения и дедупликации уже найденных OpenAlex работ и поэтому
    явно записывается в manifest как ``openalex-linked-id``. Для
    ретроспективного среза используются только записи OpenAlex, датированные
    раньше ``as_of_date``; публикации из будущего не могут подсказать ID.
    """
    cutoff = mission.get("as_of_date")
    found: set[str] = set()
    for path in sorted(openalex_dir.glob("page_*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for work in payload.get("results", []):
            published = work.get("publication_date") or ""
            if cutoff and published and published >= cutoff:
                continue
            searchable = json.dumps(
                {"ids": work.get("ids"), "locations": work.get("locations")},
                ensure_ascii=False,
            )
            found.update(ARXIV_ID_IN_URL.findall(searchable))
    return sorted(found)


def fetch_arxiv_linked(mission: dict, openalex_dir: Path, out_dir: Path,
                       refetch: bool) -> list[dict]:
    """Получить официальные страницы arXiv по ID из OpenAlex.

    Поисковый Atom API arXiv часто отвечает 429/503. OAI ``GetRecord``
    в наблюдаемой выгрузке вернул дату последней повторной подачи в поле
    ``created`` для старых ID, что неприемлемо для ретротеста. HTML-страница
    ``/abs/<id>`` содержит отдельно ``citation_date`` первой подачи и
    ``citation_online_date`` новой версии. Сохраняем HTML байт в байт и
    используем первую дату, не представляя этот режим независимым каналом
    поиска кандидатов.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    ids = openalex_linked_arxiv_ids(mission, openalex_dir)
    max_records = int(
        mission["query"].get("arxiv_linked_from_openalex", {}).get("max_records", 100)
    )
    records: list[dict] = []
    for position, arxiv_id in enumerate(ids[:max_records], start=1):
        safe_id = re.sub(r"[^A-Za-z0-9._-]+", "_", arxiv_id)
        path = out_dir / f"record_{safe_id}.html"
        url = f"https://arxiv.org/abs/{urllib.parse.quote(arxiv_id)}"
        if path.exists() and not refetch:
            body = path.read_bytes()
            records.append({
                "file": path.name, "http_status": None, "url": url,
                "records": 1, "sha256": sha256_of(path),
                "reused": True,
            })
            if position < min(len(ids), max_records):
                time.sleep(ARXIV_DELAY_SECONDS)
            continue
        try:
            body, status = http_get(url, accept="text/html", attempts=2)
        except FetchError as error:
            records.append({
                "file": None,
                "incomplete": True,
                "reason": f"не получен arXiv {arxiv_id}: {error}",
            })
            break
        path.write_bytes(body)
        records.append({
            "file": path.name, "http_status": status, "url": url,
            "records": 1, "sha256": sha256_of(path),
            "reused": False,
        })
        log(f"  запись {position}/{min(len(ids), max_records)}: {arxiv_id}")
        if position < min(len(ids), max_records):
            time.sleep(ARXIV_DELAY_SECONDS)
    return records


def extract_resumption_token(body: bytes) -> str | None:
    match = re.search(rb"<resumptionToken[^>]*>([^<]+)</resumptionToken>", body)
    return match.group(1).decode("utf-8") if match else None


def extract_complete_size(body: bytes) -> str | None:
    match = re.search(rb'completeListSize="(\d+)"', body)
    return match.group(1).decode("utf-8") if match else None


class StaleCacheError(RuntimeError):
    """Ранее скачанные страницы не соответствуют текущему запросу."""


def guard_query_hash(out_dir: Path, query: dict, refetch: bool) -> None:
    """Запретить переиспользование страниц, скачанных другим запросом.

    ИСПРАВЛЕНО ПОСЛЕ РЕВИЗИИ (дефект P1-2). Страницы переиспользовались по
    одному лишь факту существования файла, без привязки к запросу. Если
    миссию правили — меняли период, набор, категории, — старые страницы
    молча шли в дело, а манифест получал свежее время загрузки и новый хеш
    файла миссии. Неполный или устаревший корпус выглядел актуальным и
    полным, и отличить это от нормального прогона было нечем.
    """
    stamp = out_dir / ".query_hash"
    digest = hashlib.sha256(
        json.dumps(query, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]

    if not stamp.exists():
        stamp.write_text(digest, encoding="utf-8")
        return

    previous = stamp.read_text(encoding="utf-8").strip()
    if previous == digest:
        return

    if not refetch:
        raise StaleCacheError(
            f"в {out_dir} лежат страницы запроса {previous}, а текущий запрос {digest}. "
            "Переиспользовать их нельзя: они отвечают на другой вопрос. "
            "Запустите с --refetch, чтобы выгрузить заново."
        )
    stamp.write_text(digest, encoding="utf-8")


def clear_pages(out_dir: Path, suffix: str) -> int:
    """Удалить ранее скачанные страницы перед полной перезагрузкой.

    Нужно именно при --refetch: если новый запрос даёт меньше страниц, чем
    старый, хвост старых файлов остался бы лежать и попал бы в разбор как
    часть выгрузки. Тихо смешать две разные выгрузки хуже, чем перекачать.
    """
    if not out_dir.exists():
        return 0
    removed = 0
    for path in sorted(out_dir.glob(f"page_*{suffix}")):
        path.unlink()
        removed += 1
    return removed


def fetch_arxiv(mission: dict, out_dir: Path, refetch: bool) -> list[dict]:
    """Atom API постранично. Сырой XML сохраняется как есть."""
    out_dir.mkdir(parents=True, exist_ok=True)
    if refetch:
        removed = clear_pages(out_dir, ".xml")
        if removed:
            log(f"  удалено старых страниц: {removed}")

    # Два режима запроса. По терминам — когда тема известна заранее и нужен
    # именно её корпус. По категориям — когда нужен срез области целиком,
    # внутри которого тема должна найтись кластеризацией, а не поиском.
    # Второй режим и есть постановка из методики: на входе область, на
    # выходе кластеры, один из которых оказывается сигналом.
    categories = mission["query"].get("arxiv_categories")
    if categories:
        query = " OR ".join(f"cat:{c}" for c in categories)
    else:
        terms = mission["query"]["terms"]
        query = " OR ".join(f'all:"{term}"' for term in terms)

    # Период миссии обязан попасть в сам запрос, а не отсекаться потом.
    # Без этого arXiv отдаёт записи за всё время, и 2000 «самых ранних» по
    # широкой теме оказываются в основном за пределами периода: выгрузка
    # выглядит полной, а нужный ранний отрезок покрыт единицами записей.
    period = mission.get("period", {})
    if period.get("from") and period.get("to"):
        start = period["from"].replace("-", "") + "0000"
        end = period["to"].replace("-", "") + "2359"
        query = f"({query}) AND submittedDate:[{start} TO {end}]"

    page_size = int(mission["query"].get("arxiv_page_size", 200))
    max_records = int(mission["query"].get("arxiv_max_records", 3000))

    records: list[dict] = []
    start = 0
    page = 0

    while start < max_records:
        page += 1
        path = out_dir / f"page_{page:04d}.xml"

        if path.exists() and not refetch:
            body = path.read_bytes()
            count = body.count(b"<entry>")
            records.append({
                "file": path.name, "http_status": None, "url": None,
                "records": count, "sha256": sha256_of(path), "reused": True,
            })
            log(f"  страница {page}: уже скачана, пропускаю")
            start += page_size
            if count < page_size:
                break
            continue

        params = {
            "search_query": query,
            "start": str(start),
            "max_results": str(min(page_size, max_records - start)),
            "sortBy": "submittedDate",
            "sortOrder": "ascending",
        }
        url = f"{ARXIV_API}?{urllib.parse.urlencode(params)}"
        body, status = http_get(url, accept="application/atom+xml")
        path.write_bytes(body)

        count = body.count(b"<entry>")
        records.append({
            "file": path.name, "http_status": status, "url": url,
            "records": count, "sha256": sha256_of(path), "reused": False,
        })
        log(f"  страница {page}: {count} записей")

        if count == 0:
            break
        start += page_size
        time.sleep(ARXIV_DELAY_SECONDS)  # arXiv просит не чаще одного запроса в 3 секунды

    return records


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Загрузка сырых данных SAIA из OpenAlex и arXiv",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Пример:\n  python3 scripts/fetch.py missions/gnn-fraud.json --mailto you@example.com",
    )
    parser.add_argument("mission", help="файл миссии (JSON)")
    parser.add_argument(
        "--mailto",
        default=None,
        help="e-mail для polite pool OpenAlex: выше лимиты и стабильнее ответы. Необязателен.",
    )
    parser.add_argument("--refetch", action="store_true", help="перекачать уже скачанные страницы")
    parser.add_argument(
        "--out",
        default=None,
        help="куда складывать сырые данные (по умолчанию data/raw/<mission_id>)",
    )
    args = parser.parse_args()

    mission_path = Path(args.mission)
    if not mission_path.exists():
        log(f"ОШИБКА: файл миссии не найден: {mission_path}")
        return 1

    mission_bytes = mission_path.read_bytes()
    mission = json.loads(mission_bytes.decode('utf-8'))
    mission_id = mission["mission_id"]
    project_root = Path(__file__).resolve().parents[1]
    out_root = Path(args.out) if args.out else project_root / "data" / "raw" / mission_id
    out_root.mkdir(parents=True, exist_ok=True)

    started = now_utc()
    frozen_mission = out_root / 'mission.json'
    previous_manifest = out_root / 'manifest.json'
    mission_sha = hashlib.sha256(mission_bytes).hexdigest()
    if previous_manifest.exists():
        if args.refetch:
            log('Пакет с manifest не перезаписываем. Для новой загрузки используйте новый каталог --out.')
            return 3
        old = json.loads(previous_manifest.read_text())
        if old.get('mission_file_sha256') != mission_sha:
            log('Настройки отличаются от прежнего пакета. Используйте новый каталог --out; прежний пакет сохранён.')
            return 3
    if frozen_mission.exists() and sha256_of(frozen_mission) != mission_sha:
        log('Сохранённые настройки отличаются. Используйте новый каталог --out.')
        return 3
    if not frozen_mission.exists():
        frozen_mission.write_bytes(mission_bytes)
    log(f"Миссия:      {mission_id} — {mission.get('title', '')}")
    log(f"Запрос:      {', '.join(mission['query']['terms'])}")
    log(f"Период:      {mission.get('period', {}).get('from', '—')} .. {mission.get('period', {}).get('to', '—')}")
    log(f"Версия запроса: {mission.get('query_version', '—')}")
    log(f"Каталог:     {out_root}")
    log('OpenAlex: ключ из окружения задан' if os.environ.get('OPENALEX_API_KEY') else
        'OpenAlex: ключ не задан; доступен только ограниченный анонимный бюджет')
    log("")

    manifest: dict = {
        "mission_id": mission_id,
        "query_version": mission.get("query_version"),
        "connector_version": CONNECTOR_VERSION,
        "fetch_started_utc": started,
        "mission_file_sha256": mission_sha,
        "mission_snapshot_file": frozen_mission.name,
        "sources": {},
    }

    source_errors: dict[str, str] = {}
    try:
        enrich_openalex = bool(mission["query"].get("openalex_enrich_arxiv_ids"))
        if "openalex" in mission["sources"] and not enrich_openalex:
            log("OpenAlex:")
            try:
                files = fetch_openalex(mission, out_root / "openalex", args.mailto, args.refetch)
                manifest["sources"]["openalex"] = {
                    "api": OPENALEX_API,
                    "access_mode": (
                        "monthly-bounded-ranked-sample"
                        if mission["query"].get("openalex_monthly_bounded") is not None
                        else "cursor-paged-query"
                    ),
                    "independent_discovery": True,
                    "historical_text_scope": "current_metadata_not_recovered_versions",
                    "record_shape": "full",  # select не применяется, ответ сохраняется целиком
                    "files": files,
                    "total_records": sum(f.get("records") or 0 for f in files),
                }
            except FetchError as error:
                source_errors["openalex"] = str(error)
                log(f"  источник недоступен: {error}")
            log("")

        # Знаменатель тянется, если задана родительская область, независимо
        # от того, входит ли OpenAlex в источники корпуса: это агрегат по
        # годам, а не выгрузка работ. Широкой миссии сам корпус OpenAlex не
        # нужен — она строится на arXiv, — но доля темы внутри области
        # нужна всё равно.
        if mission.get("parent_field"):
            log("Знаменатель области:")
            baseline = fetch_openalex_field_baseline(
                mission, out_root / "openalex", args.mailto
            )
            if baseline:
                manifest["sources"].setdefault("openalex", {
                    "api": OPENALEX_API, "files": [], "total_records": 0,
                })
                manifest["sources"]["openalex"]["files"].append(baseline)
                manifest["sources"]["openalex"]["total_records"] = sum(
                    f.get("records") or 0 for f in manifest["sources"]["openalex"]["files"]
                )
                manifest["sources"]["openalex"]["field_baseline_only"] = (
                    "openalex" not in mission["sources"]
                )
            log("")

        if "arxiv" in mission["sources"]:
            # Два интерфейса под две разные задачи. Atom API — поиск по
            # терминам, подходит для узкой темы. OAI-PMH — штатная массовая
            # выгрузка метаданных, единственный пригодный вариант для среза
            # области: Atom на таких объёмах отвечает 429.
            linked = bool(mission["query"].get("arxiv_linked_from_openalex"))
            use_oai = bool(mission["query"].get("arxiv_oai"))
            use_snapshot = bool(mission["query"].get("arxiv_snapshot"))
            mode_label = (
                "arXiv (OAI-PMH, ID из OpenAlex):" if linked else
                "arXiv (закреплённый Parquet-срез):" if use_snapshot else
                "arXiv (OAI-PMH):" if use_oai else
                "arXiv (Atom API):"
            )
            log(mode_label)
            try:
                files = (
                    fetch_arxiv_linked(
                        mission, out_root / "openalex", out_root / "arxiv", args.refetch
                    )
                    if linked else
                    fetch_arxiv_snapshot(mission, out_root / "arxiv", args.refetch)
                    if use_snapshot else
                    fetch_arxiv_oai(mission, out_root / "arxiv", args.refetch)
                    if use_oai else
                    fetch_arxiv(mission, out_root / "arxiv", args.refetch)
                )
                manifest["sources"]["arxiv"] = {
                    "api": (
                        HF_DATASET_RESOLVE if use_snapshot else
                        ARXIV_OAI if (use_oai or linked) else ARXIV_API
                    ),
                    "access_mode": (
                        "abs-html-openalex-linked-id" if linked else
                        "hf-arxiv-parquet-snapshot" if use_snapshot else
                        "oai-pmh" if use_oai else "atom-api"
                    ),
                    "independent_discovery": not linked,
                    # File partitions of a third-party mirror are not the same
                    # contract as an official arXiv category query (crosslists).
                    "category_scope": "file_partitions_not_official_category_query" if use_snapshot else None,
                    "crosslist_completeness": "not_proven" if use_snapshot else "unknown",
                    "field_coverage": "unknown",
                    "historical_text_scope": "current_metadata_not_recovered_versions",
                    "dataset_revision": mission["query"]["arxiv_snapshot"].get("revision") if use_snapshot else None,
                    "date_filter_applied_at": "linked_openalex_as_of" if linked else
                                              "parse" if use_oai else "query",
                    "files": files,
                    "total_records": sum(f.get("records") or 0 for f in files),
                    "attribution": "Thank you to arXiv for use of its open access interoperability.",
                }
            except FetchError as error:
                source_errors["arxiv"] = str(error)
                log(f"  источник недоступен: {error}")
            log("")

        if enrich_openalex:
            log("OpenAlex (обогащение по arXiv DOI):")
            try:
                files = fetch_openalex_arxiv_enrichment(
                    mission, out_root / "arxiv", out_root / "openalex",
                    args.mailto, args.refetch,
                )
                existing = manifest["sources"].setdefault("openalex", {
                    "api": OPENALEX_API, "files": [], "total_records": 0,
                })
                existing["files"].extend(files)
                existing["total_records"] = sum(
                    item.get("records") or 0 for item in existing["files"]
                )
                existing["access_mode"] = "arxiv-doi-enrichment"
                existing["independent_discovery"] = False
                existing["role"] = "affiliations_and_historical_citations"
                existing['historical_affiliation_scope'] = 'current_bibliography_not_as_of_reconstructed'
                existing['query_pagination_audit'] = 'cursor_count_unique_ids'
            except FetchError as error:
                source_errors["openalex_enrichment"] = str(error)
                log(f"  источник недоступен: {error}")
            log("")

    except StaleCacheError as error:
        log(f"\nУСТАРЕВШИЙ КЭШ:\n{error}")
        return 3
    except FetchError as error:
        # Остался как страховка для будущих источников, которые ещё не
        # изолированы своим блоком. Уже собранные страницы не теряются.
        source_errors["pipeline"] = str(error)

    manifest["fetch_finished_utc"] = now_utc()
    manifest["source_errors"] = source_errors or None
    manifest_path = out_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Незавершённая выгрузка не должна выглядеть как успешная: манифест
    # помечается, и код возврата ненулевой. Раньше об обрыве сообщала одна
    # строчка в логе, которую легко пролистать.
    incomplete = {
        name: [f["reason"] for f in source["files"] if f.get("incomplete")]
        for name, source in manifest["sources"].items()
    }
    incomplete = {name: reasons for name, reasons in incomplete.items() if reasons}
    manifest["incomplete"] = incomplete or None
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    for name, source in manifest["sources"].items():
        files = [f for f in source["files"] if f.get("file")]
        log(f"  {name:<10} {source['total_records']:>6} записей в {len(files)} файлах")
    log(f"\nManifest: {manifest_path}")

    if incomplete:
        log("\nВЫГРУЗКА НЕПОЛНАЯ — корпус нельзя считать срезом области:")
        for name, reasons in incomplete.items():
            for reason in reasons:
                log(f"  {name}: {reason}")
        log("Поднимите предел страниц в миссии и запустите снова.")
        return 4

    if source_errors:
        log("\nЧАСТИЧНЫЙ РЕЗУЛЬТАТ — один или несколько источников недоступны:")
        for name, error in source_errors.items():
            log(f"  {name}: {error}")
        log("Успешно полученные источники сохранены и могут быть обработаны отдельно.")
        return 2

    log("Готово.")
    log("Следующий этап: импортировать manifest и нормализовать корпус.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
