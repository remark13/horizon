"""Small, explicit aggregator queries. Leads are not verified signal evidence.

Only metadata is retained. Credentialed calls never follow redirects and never
put tokens in a URL, persisted request, diagnostic message or response payload.
Public Dealroom endpoints are samples, not access to its complete database.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from saia.external_sources import load_policy
from saia.hybrid import digest
from saia.news_publishers import publishers, matches_publisher_url, reader_flags

VERSION = "aggregator-evidence-v1"
CHANNELS = {
    "dealroom_marketmaps": "company_maps",
    "dealroom_public_rounds": "investment_rounds",
    "event_registry": "aggregated_news",
    "mediacloud_news": "media_archive",
    "lens_patents": "aggregated_patents",
    "google_news_rss": "google_news",
}
CHANNELS.update({identifier: identifier for identifier in publishers()})
CREDENTIALS = {"event_registry": "EVENT_REGISTRY_API_KEY",
               "mediacloud_news": "MEDIACLOUD_API_KEY", "lens_patents": "LENS_API_TOKEN"}
MAX_BYTES = 2_000_000


@lru_cache(maxsize=1)
def _policy() -> dict:
    # Policy files are versioned and loaded once per process. Do not repeatedly
    # parse their complete lineage for every item of a large publisher RSS feed.
    return load_policy()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _day(value: str | date) -> date:
    return value if isinstance(value, date) else date.fromisoformat(value)


def _url(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 2000 or any(ord(c) < 32 for c in value):
        return None
    try:
        parsed = urlsplit(value)
        return value if parsed.scheme in {"http", "https"} and parsed.hostname and parsed.username is None and parsed.password is None else None
    except ValueError:
        return None


def configuration_status(source: str) -> str:
    if source in CREDENTIALS and not os.environ.get(CREDENTIALS[source], "").strip():
        return "credentials_required"
    if source == "mediacloud_news" and not _collections():
        return "collection_ids_required"
    return "requestable"


def _collections() -> list[str]:
    values = [v.strip() for v in os.environ.get("MEDIACLOUD_COLLECTION_IDS", "").split(",") if v.strip()]
    return values if 1 <= len(values) <= 10 and len(set(values)) == len(values) and all(re.fullmatch(r"[1-9][0-9]{0,9}", v) for v in values) else []


@dataclass(frozen=True)
class AggregatorQuery:
    topic_id: str
    source: str
    query: str
    start: str | date
    end: str | date
    as_of: str | date
    max_records: int = 5

    def validate(self) -> tuple[date, date, date]:
        if self.source not in CHANNELS or not isinstance(self.topic_id, str) or not self.topic_id.strip():
            raise ValueError("Нужны зарегистрированный агрегатор и topic ID.")
        if not isinstance(self.query, str) or not 2 <= len(self.query.strip()) <= 160 or not re.fullmatch(r"[\w\s-]+", self.query, re.UNICODE) or any(ord(c) < 32 for c in self.query):
            raise ValueError("Нужна ограниченная поисковая фраза без операторов.")
        start, end, as_of = _day(self.start), _day(self.end), _day(self.as_of)
        if start > end or end > as_of or as_of > datetime.now(timezone.utc).date():
            raise ValueError("Некорректный временной интервал агрегатора.")
        if self.source.startswith("dealroom_") and as_of != datetime.now(timezone.utc).date():
            raise ValueError("Текущий публичный каталог Dealroom не является историческим снимком.")
        if not 1 <= self.max_records <= int(_policy()[CHANNELS[self.source]]["max_records"]):
            raise ValueError("Превышен лимит записей агрегатора.")
        return start, end, as_of


def request_spec(query: AggregatorQuery) -> tuple[str, dict, bytes | None]:
    """Private in-memory request only; the returned headers/body are not logged."""
    start, end, _ = query.validate()
    base = _policy()[CHANNELS[query.source]]["url"]
    headers = {"Accept": "application/json", "User-Agent": "SAIA-research-prototype/0.4.60"}
    text = query.query.strip()
    if query.source == "dealroom_marketmaps":
        return base + "?" + urlencode({"q": text, "limit": query.max_records}), headers, None
    if query.source == "dealroom_public_rounds":
        # The endpoint does not document a thematic search. Filter its small
        # global sample locally, and explicitly expose this incomplete scope.
        return base + "?" + urlencode({"limit": query.max_records}), headers, None
    if query.source == "google_news_rss" or query.source in publishers():
        publisher = publishers().get(query.source)
        russian = publisher["search_locale"] == "ru" if publisher else bool(re.search(r"[А-Яа-яЁё]", text))
        locale = {"hl": "ru", "gl": "RU", "ceid": "RU:ru"} if russian else {"hl": "en-US", "gl": "US", "ceid": "US:en"}
        # Dates filter the published RSS timestamp; neither locale nor a news
        # publisher location establishes technological diffusion/adoption.
        domain = " site:" + publisher["domain"] if publisher else ""
        params = {"q": f'"{text}"{domain} after:{start.isoformat()} before:{(end + timedelta(days=1)).isoformat()}', **locale}
        return base + "?" + urlencode(params), headers, None
    token = os.environ.get(CREDENTIALS[query.source], "").strip()
    if query.source == "mediacloud_news":
        headers["Authorization"] = "Token " + token
        return base + "?" + urlencode({"q": '"' + text + '"', "start": start.isoformat(), "end": end.isoformat(),
            "platform": "onlinenews-mediacloud", "cs": ",".join(_collections()), "limit": query.max_records}), headers, None
    headers["Content-Type"] = "application/json"
    if query.source == "event_registry":
        body = {"action": "getArticles", "keyword": text, "keywordSearchMode": "phrase",
                "dateStart": start.isoformat(), "dateEnd": end.isoformat(), "resultType": "articles",
                "articlesPage": 1, "articlesCount": query.max_records, "articlesSortBy": "date",
                "articlesSortByAsc": False, "includeArticleBody": False, "articleBodyLen": 0,
                "includeArticleImage": False, "includeArticleEventUri": True, "apiKey": token}
    else:
        headers["Authorization"] = "Bearer " + token
        body = {"query": {"bool": {"must": [{"bool": {"should": [
                    {"match_phrase": {"title": text}}, {"match_phrase": {"abstract": text}}], "minimum_should_match": 1}}],
                    "filter": [{"range": {"date_published": {"gte": start.isoformat(), "lte": end.isoformat()}}}]}},
                "size": query.max_records, "sort": [{"date_published": "desc"}],
                "include": ["lens_id", "jurisdiction", "doc_number", "kind", "date_published", "lang", "publication_type",
                            "biblio.invention_title", "biblio.parties.applicants", "biblio.classifications_cpc", "families"]}
    return base, headers, json.dumps(body).encode("utf-8")


def _base(query: AggregatorQuery, retrieved: str) -> dict:
    start, end, as_of = query.validate()
    policy = _policy()[CHANNELS[query.source]]
    result = {"version": VERSION, "source": query.source, "role": policy["role"], "topic_id": query.topic_id.strip(),
            "query": {"text": query.query.strip(), "start": start.isoformat(), "end": end.isoformat(), "as_of": as_of.isoformat(), "max_records": query.max_records},
            "retrieved_at": retrieved, "request": {"endpoint": policy["url"]},
            "scientific_score_modified": False, "missing_is_zero": False,
            "source_result_is_exhaustive": False, "independent_evidence_verified": False,
            "stores_full_text": False, "public_republication_allowed": False,
            "limitations": [policy["limitation"]]}
    if query.source == "mediacloud_news":
        result["query"]["collection_ids"] = _collections()
    if query.source in publishers():
        result.update(reader_flags(query.source))
    return result


def _seal(report: dict) -> dict:
    report["report_payload_sha256"] = digest(report)
    return report


def unavailable(query: AggregatorQuery, status: str, retrieved: str, http_status: int | None = None) -> dict:
    return _seal({**_base(query, retrieved), "status": status, "observations": None,
                  "observed_count": None, "http_status": http_status})


def _in_window(value: object, query: AggregatorQuery) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        day = date.fromisoformat(value[:10])
        start, end, _ = query.validate()
        return day.isoformat() if start <= day <= end else None
    except ValueError:
        return None


def parse_response(query: AggregatorQuery, raw: bytes, retrieved: str) -> dict:
    query.validate()
    if len(raw) > MAX_BYTES:
        raise ValueError("Ответ агрегатора превышает лимит.")
    if query.source == "google_news_rss" or query.source in publishers():
        return parse_news_feed(query, raw, retrieved)
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("Ответ агрегатора не является объектом.")
    rows = payload.get({"dealroom_marketmaps": "results", "dealroom_public_rounds": "signals",
                        "mediacloud_news": "sample", "lens_patents": "data"}.get(query.source, "articles"))
    if query.source == "event_registry":
        rows = rows.get("results") if isinstance(rows, dict) else None
    if not isinstance(rows, list) or len(rows) > 1000:
        raise ValueError("Не получен ограниченный список записей агрегатора.")
    records, seen, rejected = [], set(), 0
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Неверная запись агрегатора.")
        item = _normalize(query, row)
        if item is None:
            rejected += 1
            continue
        identity = item.get("record_id") or item["url"]
        if identity in seen:
            continue
        seen.add(identity)
        records.append(item)
        if len(records) >= query.max_records:
            break
    report = {**_base(query, retrieved), "status": "complete" if records else "empty_observed_response",
              "observations": records, "observed_count": len(records), "returned_sample_count": len(rows),
              "rejected_record_count": rejected, "raw_response_bytes_sha256": hashlib.sha256(raw).hexdigest(),
              "result_cap_reached": len(records) >= query.max_records}
    if query.source.startswith("dealroom_"):
        report.update(provider_source=payload.get("source"), provider_fetched_at=payload.get("fetchedAt"),
                      provider_cache_status=payload.get("cacheStatus"), historical_availability_verified=False,
                      period_filter_supported=query.source == "dealroom_public_rounds")
        if payload.get("cacheStatus") == "stale":
            report["status"] = "source_data_stale"
        if query.source == "dealroom_public_rounds":
            report.update(query_method="local_literal_filter_of_global_sample", stable_deal_ids_available=False,
                          original_deal_urls_available=False, market_totals_established=False)
    return _seal(report)


def parse_news_feed(query: AggregatorQuery, raw: bytes, retrieved: str) -> dict:
    query.validate()
    if len(raw) > MAX_BYTES:
        raise ValueError("Новостная лента превышает лимит.")
    if b"\x00" in raw or b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError("Недопустимые XML-конструкции в ленте.")
    root = ET.fromstring(raw)
    if root.tag != "rss" or root.find("channel") is None:
        raise ValueError("Ответ не является новостной RSS-лентой.")
    entries = root.findall("./channel/item")
    records, seen, rejected_publishers = [], set(), 0
    for item in entries[:200]:
        title, link = item.findtext("title"), _url(item.findtext("link"))
        try:
            stamp = parsedate_to_datetime(item.findtext("pubDate") or "")
            if stamp.tzinfo is None:
                continue
            published = stamp.astimezone(timezone.utc).date().isoformat()
        except (ValueError, TypeError, OverflowError):
            continue
        published = _in_window(published, query)
        if not link or not title or not published or link in seen:
            continue
        target = urlsplit(link)
        if target.hostname != "news.google.com" or not target.path.startswith(("/rss/articles/", "/articles/")):
            continue
        seen.add(link)
        source = item.find("source")
        homepage = _url(source.get("url")) if source is not None else None
        if query.source in publishers() and not matches_publisher_url(query.source, homepage):
            rejected_publishers += 1
            continue
        records.append({"record_id": item.findtext("guid") or link, "url": link, "title": title[:1000],
                        "publication_date": published, "date_precision": "day", "language": None,
                        "publisher": source.text if source is not None else None,
                        "publisher_homepage": homepage,
                        "record_type": "news_aggregator_item", "aggregator_redirect_url": True,
                        "original_article_url_available": False})
        if query.source in publishers():
            records[-1].update(model_input_allowed=False, usage_scope="personal_research")
    records.sort(key=lambda row: (row["publication_date"], row["url"]), reverse=True)
    selected = records[:query.max_records]
    return _seal({**_base(query, retrieved), "status": "complete" if selected else "empty_observed_response",
                  "observations": selected, "observed_count": len(selected), "returned_sample_count": len(entries),
                  "raw_response_bytes_sha256": hashlib.sha256(raw).hexdigest(),
                  "rejected_publisher_count": rejected_publishers,
                  "result_cap_reached": len(records) >= query.max_records,
                  "aggregator_original_article_resolution_performed": False})


def _normalize(query: AggregatorQuery, row: dict) -> dict | None:
    source = query.source
    if source == "dealroom_marketmaps":
        link, title = _url(row.get("url")), row.get("title")
        if not link or not isinstance(title, str) or not title.strip():
            return None
        return {"record_id": str(row.get("id") or link), "title": title[:1000], "url": link,
                "record_type": "technology_company_landscape", "publication_date": None,
                "date_precision": "unknown", "language": None, "publisher": "Dealroom",
                "reported_company_count": row.get("companyCount", row.get("company_count")),
                "company_records_downloaded": False, "original_record_url_available": True}
    if source == "dealroom_public_rounds":
        title = row.get("title")
        searchable = (str(title or "") + " " + str(row.get("meta") or "")).casefold()
        if not isinstance(title, str) or not all(term.casefold() in searchable for term in query.query.split()):
            return None
        reported = str(row.get("time") or "")
        try:
            month = datetime.strptime(reported, "%b %Y").date()
        except ValueError:
            try:
                month = date.fromisoformat(reported[:7] + "-01")
            except ValueError:
                return None
        start, end, _ = query.validate()
        if not start.strftime("%Y-%m") <= month.strftime("%Y-%m") <= end.strftime("%Y-%m"):
            return None
        return {"record_id": "sample:" + digest({k: row.get(k) for k in ("type", "title", "city", "meta", "value", "time")}),
                "title": title[:1000], "url": "https://dealroom.co/for-agents/", "publisher": "Dealroom",
                "record_type": "unverified_investment_round_lead", "announced_month": month.strftime("%Y-%m"),
                "date_precision": "month", "date_reported_original": reported, "language": None,
                "reported_amount_text": str(row.get("value") or "")[:200], "funding_amount": None,
                "funding_currency": None, "reported_context": str(row.get("meta") or "")[:500],
                "city_reported": str(row.get("city") or "")[:200], "region_scope": "reported_company_city_not_adoption",
                "original_record_url_available": False, "provider_deal_id": None, "synthetic_identity": True}
    if source == "lens_patents":
        identifier = str(row.get("lens_id") or "")
        titles = (row.get("biblio") or {}).get("invention_title")
        entries = [v for v in titles if isinstance(v, dict) and isinstance(v.get("text"), str)] if isinstance(titles, list) else []
        title = next((v for v in entries if v.get("lang") == "en"), entries[0] if entries else None)
        published = _in_window(row.get("date_published"), query)
        if not re.fullmatch(r"\d{3}-\d{3}-\d{3}-\d{3}-\d{3}", identifier) or title is None or published is None:
            return None
        return {"record_id": identifier, "title": title["text"][:1000], "url": "https://www.lens.org/lens/patent/" + identifier,
                "record_type": "patent_publication", "publication_date": published, "date_precision": "day",
                "language": title.get("lang") or row.get("lang"), "publisher": "Lens", "jurisdiction": row.get("jurisdiction"),
                "publication_number": str(row.get("doc_number") or ""), "kind": row.get("kind"),
                "families": row.get("families"), "biblio": {k: row["biblio"].get(k) for k in ("parties", "classifications_cpc")}}
    published = _in_window(row.get("date") if source == "event_registry" else row.get("publish_date"), query)
    link, title = _url(row.get("url")), row.get("title")
    if not link or not isinstance(title, str) or not title.strip() or published is None:
        return None
    publisher = row.get("source") if source == "event_registry" else None
    return {"record_id": str(row.get("uri") or row.get("id") or link), "title": title[:1000], "url": link,
            "record_type": "news_publication", "publication_date": published, "date_precision": "day",
            "language": row.get("lang") if source == "event_registry" else row.get("language"),
            "publisher": publisher.get("title") if isinstance(publisher, dict) else row.get("media_name"),
            "event_uri": row.get("eventUri"), "indexed_date": row.get("indexed_date"),
            "provider_duplicate_flag": row.get("isDuplicate")}


def fetch(query: AggregatorQuery, timeout: float = 12.0) -> dict:
    query.validate()
    retrieved = datetime.now(timezone.utc).isoformat()
    status = configuration_status(query.source)
    if status != "requestable":
        return unavailable(query, status, retrieved)
    url, headers, body = request_spec(query)
    try:
        with build_opener(NoRedirect()).open(Request(url, headers=headers, data=body), timeout=min(20.0, max(1.0, timeout))) as response:
            report = parse_response(query, response.read(MAX_BYTES + 1), retrieved)
            report.pop("report_payload_sha256", None)
            report["http_status"] = response.status
            return _seal(report)
    except HTTPError as error:
        # Error bodies can echo credentials. Neither body nor raw error is kept.
        status = "rate_limited" if error.code == 429 else "credentials_rejected" if error.code in {401, 403} else "source_http_error"
        return unavailable(query, status, retrieved, error.code)
    except (URLError, TimeoutError, OSError):
        return unavailable(query, "source_unavailable", retrieved)
    except (ValueError, TypeError, KeyError, AttributeError, ET.ParseError):
        return unavailable(query, "invalid_source_response", retrieved)
