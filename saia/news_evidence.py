"""Bounded GDELT news metadata as external evidence, never scientific score."""
from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from saia.external_sources import VERSION, load_policy
from saia.hybrid import digest

MAX_RESPONSE_BYTES = 2_000_000
_REQUEST_LOCK = threading.Lock()
_NEXT_REQUEST_AT = 0.0


def _admit_request() -> int:
    """Bound process-wide GDELT rate without blocking the UI or retrying 429."""
    global _NEXT_REQUEST_AT
    with _REQUEST_LOCK:
        now = time.monotonic()
        remaining = _NEXT_REQUEST_AT - now
        if remaining > 0:
            return int(remaining) + 1
        _NEXT_REQUEST_AT = now + 6.0
        return 0


def _cooldown(value: str | None) -> None:
    global _NEXT_REQUEST_AT
    try:
        seconds = min(900, max(6, int(value or "300")))
    except ValueError:
        seconds = 300
    with _REQUEST_LOCK:
        _NEXT_REQUEST_AT = max(_NEXT_REQUEST_AT, time.monotonic() + seconds)


def _day(value: str | date) -> date:
    return value if isinstance(value, date) else date.fromisoformat(value)


@dataclass(frozen=True)
class NewsQuery:
    topic_id: str
    query: str
    start: str | date
    end: str | date
    as_of: str | date
    max_records: int = 25

    def validate(self) -> tuple[date, date, date]:
        policy = load_policy()["news"]
        query = self.query.strip()
        if not self.topic_id.strip() or not 2 <= len(query) <= 200:
            raise ValueError("Нужны topic ID и запрос длиной от 2 до 200 знаков.")
        if any(ord(char) < 32 for char in query):
            raise ValueError("Новостной запрос содержит управляющие символы.")
        start, end, as_of = _day(self.start), _day(self.end), _day(self.as_of)
        if end < start or end > as_of:
            raise ValueError("Некорректный интервал GDELT.")
        if start < as_of - timedelta(days=int(policy["max_lookback_days"])):
            raise ValueError("GDELT ArticleList разрешён только для свежего ограниченного окна.")
        if not 1 <= self.max_records <= int(policy["max_records"]):
            raise ValueError("Превышен лимит новостных записей.")
        return start, end, as_of


def request_url(query: NewsQuery) -> str:
    start, end, _ = query.validate()
    params = {
        "query": query.query.strip(), "mode": "artlist", "format": "json",
        "maxrecords": query.max_records, "sort": "datedesc",
        "startdatetime": f"{start:%Y%m%d}000000",
        "enddatetime": f"{end:%Y%m%d}235959",
    }
    return f"{load_policy()['news']['base_url']}?{urlencode(params)}"


def _seen_day(value: object) -> str:
    digits = "".join(char for char in str(value or "") if char.isdigit())
    if len(digits) < 8:
        raise ValueError("GDELT article has no valid seendate.")
    return datetime.strptime(digits[:8], "%Y%m%d").date().isoformat()


def story_fingerprint(title: str) -> str:
    normalized = " ".join(
        "".join(char if char.isalnum() else " " for char in title.casefold()).split()
    )
    if not normalized:
        raise ValueError("News title has no searchable characters.")
    return hashlib.sha256(normalized.encode()).hexdigest()


def parse_response(query: NewsQuery, payload_bytes: bytes, retrieved_at: str,
                   url: str, final_url: str | None = None,
                   http_status: int = 200) -> dict:
    start, end, as_of = query.validate()
    if len(payload_bytes) > MAX_RESPONSE_BYTES:
        raise ValueError("GDELT response exceeds the bounded connector limit.")
    payload = json.loads(payload_bytes)
    articles = payload.get("articles")
    if not isinstance(articles, list):
        raise ValueError("GDELT response has no articles list.")
    observations, seen_urls = [], set()
    for article in articles:
        if not isinstance(article, dict):
            raise ValueError("Invalid GDELT article record.")
        article_url, title = str(article.get("url") or "").strip(), str(article.get("title") or "").strip()
        if not article_url.startswith(("http://", "https://")) or not title:
            raise ValueError("GDELT article lacks URL or title.")
        observed = _seen_day(article.get("seendate"))
        if not start.isoformat() <= observed <= end.isoformat():
            raise ValueError("GDELT article is outside the requested interval.")
        if article_url in seen_urls:
            continue
        seen_urls.add(article_url)
        observations.append({
            "url": article_url, "title": title, "seen_date": observed,
            "domain": article.get("domain"), "language": article.get("language"),
            "source_country": article.get("sourcecountry"),
            "story_fingerprint": story_fingerprint(title),
        })
    observations.sort(key=lambda item: (item["seen_date"], item["url"]), reverse=True)
    domain_counts = Counter(item["domain"] for item in observations if item["domain"])
    story_groups = {}
    for item in observations:
        group = story_groups.setdefault(item["story_fingerprint"], {
            "story_fingerprint": item["story_fingerprint"], "title": item["title"],
            "article_count": 0, "domains": [],
        })
        group["article_count"] += 1
        if item["domain"] and item["domain"] not in group["domains"]:
            group["domains"].append(item["domain"])
    for group in story_groups.values():
        group["domains"].sort()
    result = {
        "version": VERSION, "source": "gdelt_doc_2_0", "role": "news_attention_only",
        "topic_id": query.topic_id.strip(),
        "query": {"text": query.query.strip(), "start": start.isoformat(),
                  "end": end.isoformat(), "as_of": as_of.isoformat(),
                  "max_records": query.max_records},
        "request": {"url": url, "final_url": final_url, "http_status": http_status},
        "retrieved_at": retrieved_at,
        "raw_response_bytes_sha256": hashlib.sha256(payload_bytes).hexdigest(),
        "status": "complete" if observations else "empty_observed_response",
        "observations": observations, "observed_article_count": len(observations),
        "observed_domain_count": len(domain_counts),
        "domain_counts": dict(sorted(domain_counts.items())),
        "unique_story_count": len(story_groups),
        "syndicated_story_groups": sorted(
            story_groups.values(), key=lambda item: (-item["article_count"], item["title"])
        ),
        "independent_publishers_established": False,
        "result_cap_reached": len(observations) >= query.max_records,
        "source_result_is_exhaustive": False,
        "missing_is_zero": False, "stores_article_text": False,
        "scientific_score_modified": False,
        "limitations": [load_policy()["news"]["limitation"]],
    }
    result["report_payload_sha256"] = digest(result)
    return result


def unavailable(query: NewsQuery, status: str, url: str, retrieved_at: str,
                http_status: int | None = None, payload_bytes: bytes = b"",
                retry_after: str | None = None, reason: str | None = None) -> dict:
    start, end, as_of = query.validate()
    result = {
        "version": VERSION, "source": "gdelt_doc_2_0", "role": "news_attention_only",
        "topic_id": query.topic_id.strip(),
        "query": {"text": query.query.strip(), "start": start.isoformat(),
                  "end": end.isoformat(), "as_of": as_of.isoformat(),
                  "max_records": query.max_records},
        "request": {"url": url, "final_url": None, "http_status": http_status,
                    "retry_after": retry_after},
        "retrieved_at": retrieved_at,
        "raw_response_bytes_sha256": hashlib.sha256(payload_bytes).hexdigest() if payload_bytes else None,
        "status": status, "status_reason": reason,
        "observations": None, "observed_article_count": None,
        "observed_domain_count": None, "domain_counts": None,
        "unique_story_count": None, "syndicated_story_groups": None,
        "independent_publishers_established": False,
        "result_cap_reached": None, "missing_is_zero": False,
        "source_result_is_exhaustive": False,
        "stores_article_text": False, "scientific_score_modified": False,
        "limitations": [load_policy()["news"]["limitation"]],
    }
    result["report_payload_sha256"] = digest(result)
    return result


def fetch(query: NewsQuery, timeout: float = 30.0) -> dict:
    url, retrieved = request_url(query), datetime.now(timezone.utc).isoformat()
    wait = _admit_request()
    if wait:
        return unavailable(query, "request_deferred", url, retrieved, retry_after=str(wait), reason="local_rate_guard")
    request = Request(url, headers={"User-Agent": "SAIA-research-prototype/0.4.30"})
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = response.read(MAX_RESPONSE_BYTES + 1)
            return parse_response(query, payload, retrieved, url, response.geturl(), response.status)
    except HTTPError as error:
        payload = error.read(MAX_RESPONSE_BYTES + 1)
        status = "rate_limited" if error.code == 429 else "source_http_error"
        if error.code == 429:
            _cooldown(error.headers.get("Retry-After"))
        return unavailable(query, status, url, retrieved, error.code, payload,
                           error.headers.get("Retry-After"), f"http_{error.code}")
    except (URLError, TimeoutError) as error:
        return unavailable(query, "source_unavailable", url, retrieved,
                           reason=type(error).__name__)
