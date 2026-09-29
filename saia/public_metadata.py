"""Common bounds for keyless metadata APIs; not an unrestricted URL fetcher."""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import ClassVar
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from saia.external_sources import VERSION, load_policy
from saia.hybrid import digest


@dataclass(frozen=True)
class MetadataQuery:
    topic_id: str
    query: str
    start: str | date
    end: str | date
    as_of: str | date
    max_records: int = 10
    section: ClassVar[str]

    def validate(self) -> tuple[date, date, date]:
        if not isinstance(self.topic_id, str) or not self.topic_id.strip():
            raise ValueError("Укажите идентификатор темы.")
        if not isinstance(self.query, str) or not 2 <= len(self.query.strip()) <= 160:
            raise ValueError("Нужна поисковая фраза от 2 до 160 символов.")
        if any(ord(c) < 32 for c in self.query) or not re.fullmatch(r"[\w\s-]+", self.query, re.UNICODE):
            raise ValueError("В запросе допустимы только слова, пробелы и дефисы.")
        if not terms(self.query):
            raise ValueError("Нужны содержательные слова в запросе.")
        if any(type(v) is not date and not isinstance(v, str) for v in (self.start, self.end, self.as_of)):
            raise ValueError("Даты должны быть днями в формате ГГГГ-ММ-ДД.")
        days = tuple(v if type(v) is date else date.fromisoformat(v) for v in (self.start, self.end, self.as_of))
        start, end, cutoff = days
        if end < start or end > cutoff:
            raise ValueError("Некорректный период дополнительных материалов.")
        if type(self.max_records) is not int or not 1 <= self.max_records <= int(load_policy()[self.section]["max_records"]):
            raise ValueError("Превышен лимит дополнительных материалов.")
        return start, end, cutoff


def terms(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"\w+", text.casefold(), re.UNICODE)))


def all_terms_match(query: str, text: str) -> bool:
    return set(terms(query)) <= set(terms(text))


def quoted_and(text: str) -> str:
    # Inputs have already passed plain-text validation; operators are generated,
    # never accepted as caller-supplied query syntax.
    return " AND ".join('"' + word + '"' for word in terms(text))


def iso_day(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:T[^\s]+)?", text):
        return None
    try:
        return (datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
                if "T" in text else date.fromisoformat(text).isoformat())
    except ValueError:
        return None


def amount(value: object, *, allow_zero: bool = True) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        number = float(value)
    except (ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and (number >= 0 if allow_zero else number > 0) else None


def json_payload(payload: bytes, section: str) -> dict:
    if len(payload) > int(load_policy()[section]["max_response_bytes"]):
        raise ValueError("Ответ источника превысил ограниченный размер.")
    def reject_constant(value):
        raise ValueError("JSON источника содержит нечисловую константу.")
    result = json.loads(payload, parse_constant=reject_constant)
    if not isinstance(result, dict):
        raise ValueError("Ответ источника должен быть объектом JSON.")
    return result


def base_report(query: MetadataQuery, retrieved_at: str, url: str,
                payload: bytes = b"", http_status: int | None = None) -> dict:
    start, end, cutoff = query.validate()
    policy = load_policy()[query.section]
    return {
        "version": VERSION, "source": policy["source"], "role": policy["role"],
        "topic_id": query.topic_id.strip(),
        "query": {"text": " ".join(query.query.split()), "start": start.isoformat(),
                  "end": end.isoformat(), "as_of": cutoff.isoformat(), "max_records": query.max_records},
        "request": {"url": url, "http_status": http_status, "page_limit": 1,
                    "source_page_size": int(policy["source_page_size"])},
        "retrieved_at": retrieved_at,
        "raw_response_bytes_sha256": hashlib.sha256(payload).hexdigest() if payload else None,
        "observations": None, "observed_record_count": None,
        "reported_total_results": None, "source_result_is_exhaustive": False,
        "search_relevance_requires_review": True, "retrospective_safe": False,
        "scientific_score_modified": False, "missing_is_zero": False,
        "stores_full_text": False, "stores_files": False, "stores_contacts": False,
        "limitations": [policy["limitation"]],
    }


def finish(result: dict) -> dict:
    result["report_payload_sha256"] = digest(result)
    return result


class SameHostRedirects(HTTPRedirectHandler):
    def __init__(self, host: str):
        super().__init__()
        self.host = host

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlsplit(newurl)
        if target.scheme != "https" or target.hostname != self.host or target.username is not None or target.password is not None or target.port not in (None, 443):
            raise ValueError("Переадресация вне официального API запрещена.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_bounded(query: MetadataQuery, url: str, parser, *, timeout: float = 12.0) -> dict:
    result = base_report(query, datetime.now(timezone.utc).isoformat(), url)
    policy = load_policy()[query.section]
    official = urlsplit(policy["search_url"])
    target = urlsplit(url)
    if target.scheme != "https" or target.hostname != official.hostname or target.path != official.path or target.username is not None or target.password is not None or target.port not in (None, 443):
        raise ValueError("Для коннектора разрешён только официальный адрес API.")
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not math.isfinite(timeout) or not 0 < timeout <= 30:
        raise ValueError("Тайм-аут должен быть положительным и не более 30 секунд.")
    maximum = int(policy["max_response_bytes"])
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "SAIA-research-prototype/0.4.51"})
    try:
        with build_opener(SameHostRedirects(official.hostname)).open(request, timeout=timeout) as response:
            raw = response.read(maximum + 1)
            result["raw_response_bytes_sha256"] = hashlib.sha256(raw).hexdigest()
            result["request"]["http_status"] = response.status
            return parser(query, raw, result["retrieved_at"], url, response.status)
    except HTTPError as error:
        result.update(status="rate_limited" if error.code == 429 else "source_http_error", status_reason=f"http_{error.code}")
        result["request"].update(http_status=error.code, retry_after=error.headers.get("Retry-After") if error.headers else None)
    except (URLError, TimeoutError, OSError) as error:
        result.update(status="source_unavailable", status_reason=type(error).__name__)
    except (ValueError, TypeError, KeyError, RecursionError) as error:
        result.update(status="invalid_source_response", status_reason=type(error).__name__)
    return finish(result)
