"""Allowlisted official RSS/Atom metadata. Never store story bodies or media."""
from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from saia.external_sources import VERSION, load_policy
from saia.hybrid import digest

MAX_RESPONSE_BYTES = 2_000_000
FEEDS = {
    "mit_news_rss": ("mit_news", "MIT News", "MIT", "news.mit.edu"),
    "nasa_news_rss": ("nasa_news", "NASA Technology", "NASA", "www.nasa.gov"),
    "jpl_news_rss": ("jpl_news", "NASA JPL", "NASA", "www.jpl.nasa.gov"),
    "nist_news_rss": ("nist_news", "NIST News", "NIST", "www.nist.gov"),
    "aist_press_rss": ("aist_news", "AIST research releases", "AIST", "www.aist.go.jp"),
}
STOP = {"and", "or", "the", "a", "an", "of", "in", "for", "with", "to"}
AIST_QUERY_STOP = STOP | {"и", "или", "для", "в", "на", "по", "с", "из"}
# A small reviewed search bridge, not machine translation or topic inference.
# Original source titles are preserved; the applied alternatives are disclosed.
AIST_ALIASES = {
    "material": ("材料",), "materials": ("材料",), "материал": ("材料",), "материалы": ("材料",),
    "материала": ("材料",), "материалов": ("材料",),
    "imaging": ("イメージング",), "визуализация": ("イメージング",), "визуализации": ("イメージング",),
    "battery": ("電池",), "batteries": ("電池",), "батареи": ("電池",),
    "hydrogen": ("水素",), "водород": ("水素",),
    "quantum": ("量子",), "квантовые": ("量子",),
    "robot": ("ロボット",), "robotics": ("ロボット",), "роботы": ("ロボット",),
    "artificial intelligence": ("人工知能",), "machine learning": ("機械学習",), "ии": ("人工知能",),
    "искусственный интеллект": ("人工知能",), "машинное обучение": ("機械学習",),
    "polymer": ("高分子", "ポリマー"), "catalyst": ("触媒",), "катализатор": ("触媒",),
    "gene": ("遺伝子",), "genome": ("ゲノム",),
}


def personal_reader_flags() -> dict:
    return {"usage_scope": "personal_research", "public_republication_allowed": False,
            "model_input_allowed": False, "model_training_allowed": False,
            "bulk_reuse_approved": False, "model_inputs_modified": False}


def _literal_present(term: str, title: str) -> bool:
    # CJK does not delimit each noun with spaces. ASCII boundaries still
    # prevent AI matching arbitrary Latin words (e.g. train).
    if re.fullmatch(r"[a-z0-9]+", term):
        return bool(re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", title))
    return term in title


def aist_search_terms(text: str) -> list[dict]:
    tokens = [word for word in re.findall(r"\w+", text.casefold()) if word not in AIST_QUERY_STOP]
    terms, offset = [], 0
    while offset < len(tokens):
        phrase, width = tokens[offset], 1
        if offset + 1 < len(tokens):
            pair = " ".join(tokens[offset:offset + 2])
            if pair in AIST_ALIASES:
                phrase, width = pair, 2
        terms.append({"query_term": phrase, "alternatives": [phrase, *AIST_ALIASES.get(phrase, ())]})
        offset += width
    return terms


def _plain(value: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]*>", " ", value)).split())


def _day(value: str | date) -> date:
    return value if isinstance(value, date) else date.fromisoformat(value)


def _published(value: str) -> str | None:
    if not value.strip():
        return None
    try:
        return parsedate_to_datetime(value).date().isoformat()
    except (ValueError, TypeError, OverflowError):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            return None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text(element: ET.Element, name: str) -> str:
    return next(("".join(child.itertext()) for child in element
                 if _local(child.tag) == name), "")


@dataclass(frozen=True)
class RSSQuery:
    topic_id: str
    query: str
    start: str | date
    end: str | date
    as_of: str | date
    source: str = "mit_news_rss"
    max_records: int = 10

    def validate(self) -> tuple[date, date, date]:
        if self.source not in FEEDS:
            raise ValueError("Выберите официальный источник из списка.")
        if not self.topic_id.strip() or not 2 <= len(self.query.strip()) <= 160:
            raise ValueError("Нужны тема и запрос от 2 до 160 символов.")
        if any(ord(c) < 32 for c in self.query):
            raise ValueError("Запрос содержит управляющие символы.")
        words = [word for word in re.findall(r"\w+", self.query.casefold()) if word not in STOP]
        if not words or self.source == "aist_press_rss" and not aist_search_terms(self.query):
            raise ValueError("Нужен содержательный запрос.")
        start, end, cutoff = map(_day, (self.start, self.end, self.as_of))
        if end < start or end > cutoff:
            raise ValueError("Некорректный период новостной ленты.")
        if not 1 <= self.max_records <= int(load_policy()[FEEDS[self.source][0]]["max_records"]):
            raise ValueError("Превышен лимит материалов ленты.")
        return start, end, cutoff


class _OfficialRedirects(HTTPRedirectHandler):
    def __init__(self, host: str):
        super().__init__()
        self.host = host

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlsplit(newurl)
        if target.scheme != "https" or target.hostname != self.host or target.username or target.port not in (None, 443):
            raise ValueError("Переадресация за пределы официального источника запрещена.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _base(query: RSSQuery, retrieved_at: str) -> dict:
    start, end, cutoff = query.validate()
    section, name, organisation, _ = FEEDS[query.source]
    feed_url, matched_terms = selected_feed(query)
    result = {
        "version": VERSION, "source": query.source, "role": "news_attention_only",
        "topic_id": query.topic_id, "publisher": name, "publisher_organisation": organisation,
        "query": {"text": query.query.strip(), "start": start.isoformat(),
                  "end": end.isoformat(), "as_of": cutoff.isoformat(),
                  "max_records": query.max_records},
        "retrieved_at": retrieved_at,
        "request": {"url": feed_url, "feed_choice_terms": matched_terms,
                    "feed_choice_basis": "allowlisted_topical_feed_or_general_fallback"},
        "source_result_is_exhaustive": False, "retrospective_safe": False,
        "missing_is_zero": False, "scientific_score_modified": False,
        "stores_article_text": False, "stores_images": False,
        "limitations": [load_policy()[section]["limitation"]],
    }
    if query.source == "aist_press_rss":
        result.update(personal_reader_flags())
        result["query_match_terms"] = aist_search_terms(query.query)
        result["query_expansion_basis"] = "explicit_curated_literal_japanese_aliases"
    return result


def selected_feed(query: RSSQuery) -> tuple[str, list[str]]:
    section, _, _, host = FEEDS[query.source]
    policy = load_policy()[section]
    words = set(re.findall(r"\w+", query.query.casefold()))
    for feed in policy.get("topical_feeds", []):
        matches = sorted(words & set(feed["terms"]))
        if matches:
            target = urlsplit(feed["url"])
            if target.scheme != "https" or target.hostname != host or target.username:
                raise ValueError("Некорректный официальный адрес тематической ленты.")
            return feed["url"], matches
    return policy["url"], []


def parse_response(query: RSSQuery, payload: bytes, retrieved_at: str) -> dict:
    start, end, _ = query.validate()
    if len(payload) > MAX_RESPONSE_BYTES:
        raise ValueError("Ответ RSS превысил ограниченный размер.")
    if b"\x00" in payload or b"<!DOCTYPE" in payload.upper() or b"<!ENTITY" in payload.upper():
        raise ValueError("Внешние определения и сущности XML запрещены.")
    root = ET.fromstring(payload)
    if _local(root.tag) not in {"rss", "feed", "RDF"}:
        raise ValueError("Ответ не является RSS или Atom.")
    words = [v for v in re.findall(r"\w+", query.query.casefold()) if v not in STOP]
    if not words:
        raise ValueError("Запрос состоит только из служебных слов.")
    section, publisher, organisation, host = FEEDS[query.source]
    policy = load_policy()[section]
    applied = aist_search_terms(query.query) if query.source == "aist_press_rss" else []
    observations, seen, unknown_dates, examined = [], set(), 0, 0
    outside_dates, wrong_paths = 0, 0
    channel = next((n for n in root if _local(n.tag) == "channel"), root)
    language = _text(channel, "language") or policy.get("default_language", "en")
    for item in root.iter():
        if _local(item.tag) not in {"item", "entry"}:
            continue
        examined += 1
        if examined > 200:
            break
        original_title = _text(item, "title").strip()
        if query.source == "aist_press_rss" and len(original_title) > 1000:
            continue  # Do not publish a shortened or rewritten AIST title.
        title = original_title if query.source == "aist_press_rss" else _plain(original_title)[:1000]
        link = _text(item, "link").strip()
        if not link:
            link = next((c.get("href", "") for c in item if _local(c.tag) == "link"
                         and c.get("rel", "alternate") == "alternate"), "")
        target = urlsplit(link)
        if target.scheme != "https" or target.hostname != host or target.username or target.port not in (None, 443):
            continue
        if policy.get("record_path_prefixes") and not any(
                target.path.startswith(prefix) for prefix in policy["record_path_prefixes"]):
            wrong_paths += 1
            continue
        day = _published(_text(item, "pubDate") or _text(item, "published"))
        if not day:
            unknown_dates += 1
            continue
        if not start.isoformat() <= day <= end.isoformat() or not title or link in seen:
            if not start.isoformat() <= day <= end.isoformat():
                outside_dates += 1
            continue
        categories = [_plain("".join(c.itertext()))[:100] for c in item if _local(c.tag) == "category"]
        searchable = set(re.findall(r"\w+", (title + " " + " ".join(categories)).casefold()))
        matched = all(any(_literal_present(term, title.casefold()) for term in entry["alternatives"])
                      for entry in applied) if applied else all(term in searchable for term in words)
        if not matched:
            continue
        seen.add(link)
        observations.append({
            "url": link, "title": title, "published_at": day, "language": language,
            "author": _plain(_text(item, "creator") or _text(item, "author"))[:200] or None,
            "publisher": publisher, "publisher_organisation": organisation,
            "source_country": policy.get("source_country", "United States"), "country_scope": "publisher_origin_only",
            "record_type": "institutional_announcement", "categories": categories[:20],
            "matched_terms": [entry["query_term"] for entry in applied] if applied else words,
            "match_basis": "explicit_curated_literal_japanese_aliases" if applied else "headline_and_categories_all_terms",
            "independent_confirmation": False,
        })
        if applied:
            observations[-1].update(model_input_allowed=False, title_preserved_from_feed=True,
                                    query_match_terms=applied, usage_scope="personal_research")
    observations.sort(key=lambda row: (row["published_at"], row["url"]), reverse=True)
    result = _base(query, retrieved_at)
    result.update({"status": "complete" if observations else "empty_observed_response",
                   "observations": observations[:query.max_records],
                   "observed_article_count": min(len(observations), query.max_records),
                   "response_items_examined": min(examined, 200),
                   "records_without_publication_date": unknown_dates,
                   "result_cap_reached": len(observations) >= query.max_records,
                   "raw_response_bytes_sha256": hashlib.sha256(payload).hexdigest()})
    if query.source == "aist_press_rss":
        result.update(records_outside_research_release_paths=wrong_paths,
                      records_outside_publication_interval=outside_dates)
    result["report_payload_sha256"] = digest(result)
    return result


def fetch(query: RSSQuery, timeout: float = 10.0) -> dict:
    result = _base(query, datetime.now(timezone.utc).isoformat())
    _, _, _, host = FEEDS[query.source]
    request = Request(result["request"]["url"], headers={"User-Agent": "SAIA-personal-research-prototype/0.4.54",
                                                        "Accept": "application/rss+xml, application/atom+xml, application/xml"})
    try:
        with build_opener(_OfficialRedirects(host)).open(request, timeout=timeout) as response:
            return parse_response(query, response.read(MAX_RESPONSE_BYTES + 1), result["retrieved_at"])
    except HTTPError as error:
        result.update(status="rate_limited" if error.code == 429 else "source_http_error",
                      http_status=error.code, status_reason=f"http_{error.code}")
    except (URLError, TimeoutError) as error:
        result.update(status="source_unavailable", status_reason=type(error).__name__)
    except (ValueError, ET.ParseError) as error:
        result.update(status="invalid_source_response", status_reason=type(error).__name__)
    result["observations"] = None
    result["observed_article_count"] = None
    result["report_payload_sha256"] = digest(result)
    return result
