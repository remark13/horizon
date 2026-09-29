"""EPO OPS patent bibliographic evidence with explicit credential boundary."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from saia.external_sources import VERSION, load_policy
from saia.hybrid import digest

MAX_RESPONSE_BYTES = 5_000_000


def _day(value: str | date) -> date:
    return value if isinstance(value, date) else date.fromisoformat(value)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _first_text(parent: ET.Element, name: str) -> str | None:
    for element in parent.iter():
        if _local(element.tag) == name and element.text and element.text.strip():
            return element.text.strip()
    return None


def _party_names(parent: ET.Element, party: str) -> list[str]:
    values = []
    for element in parent.iter():
        if _local(element.tag) != party:
            continue
        name = _first_text(element, "name")
        if name and name not in values:
            values.append(name)
    return values


@dataclass(frozen=True)
class PatentQuery:
    topic_id: str
    phrase: str
    start: str | date
    end: str | date
    as_of: str | date
    max_records: int = 25

    def validate(self) -> tuple[date, date, date]:
        phrase = self.phrase.strip()
        if not self.topic_id.strip() or not 2 <= len(phrase) <= 160:
            raise ValueError("Нужны topic ID и патентная фраза длиной от 2 до 160 знаков.")
        if any(char in phrase for char in ('"', "\\", "\n", "\r", "\t")):
            raise ValueError("Патентная фраза содержит недопустимые символы.")
        start, end, as_of = _day(self.start), _day(self.end), _day(self.as_of)
        if end < start or end > as_of:
            raise ValueError("Некорректный патентный интервал.")
        if not 1 <= self.max_records <= int(load_policy()["patents"]["max_records"]):
            raise ValueError("Превышен лимит патентных записей.")
        return start, end, as_of


def cql(query: PatentQuery) -> str:
    start, end, _ = query.validate()
    return (f'ta="{query.phrase.strip()}" and pd>={start:%Y%m%d} '
            f'and pd<={end:%Y%m%d} and pd>00000000')


def search_url(query: PatentQuery) -> str:
    params = {"q": cql(query), "Range": f"1-{query.max_records}"}
    return f"{load_policy()['patents']['search_url']}?{urlencode(params)}"


def parse_response(query: PatentQuery, payload_bytes: bytes, retrieved_at: str,
                   url: str, final_url: str | None = None,
                   http_status: int = 200) -> dict:
    start, end, as_of = query.validate()
    if len(payload_bytes) > MAX_RESPONSE_BYTES:
        raise ValueError("EPO OPS response exceeds the bounded connector limit.")
    root = ET.fromstring(payload_bytes)
    total = None
    for element in root.iter():
        if _local(element.tag) == "biblio-search" and element.get("total-result-count"):
            total = int(element.get("total-result-count"))
            break
    observations, seen = [], set()
    for document in (element for element in root.iter() if _local(element.tag) == "exchange-document"):
        country = document.get("country") or _first_text(document, "country")
        number = document.get("doc-number") or _first_text(document, "doc-number")
        kind = document.get("kind") or _first_text(document, "kind")
        identifier = "".join(part or "" for part in (country, number, kind))
        if not identifier or identifier in seen:
            continue
        seen.add(identifier)
        title = None
        for element in document.iter():
            if _local(element.tag) == "invention-title" and element.text and element.text.strip():
                if title is None or element.get("lang") == "en":
                    title = element.text.strip()
                if element.get("lang") == "en":
                    break
        publication_date = None
        for reference in document.iter():
            if _local(reference.tag) == "publication-reference":
                raw = _first_text(reference, "date")
                if raw and len(raw) == 8 and raw.isdigit():
                    publication_date = datetime.strptime(raw, "%Y%m%d").date().isoformat()
                break
        abstract_parts = [" ".join(element.itertext()).strip() for element in document.iter()
                          if _local(element.tag) == "abstract"]
        abstract = " ".join(part for part in abstract_parts if part) or None
        observations.append({
            "publication_id": identifier, "country": country, "publication_number": number,
            "kind": kind, "family_id": document.get("family-id"), "title": title,
            "publication_date": publication_date, "applicants": _party_names(document, "applicant"),
            "inventors": _party_names(document, "inventor"), "abstract": abstract,
        })
    result = {
        "version": VERSION, "source": "epo_ops", "role": "patent_landscape_only",
        "topic_id": query.topic_id.strip(),
        "query": {"phrase": query.phrase.strip(), "cql": cql(query),
                  "start": start.isoformat(), "end": end.isoformat(),
                  "as_of": as_of.isoformat(), "max_records": query.max_records},
        "request": {"url": url, "final_url": final_url, "http_status": http_status,
                    "credentials_stored": False},
        "retrieved_at": retrieved_at,
        "raw_response_bytes_sha256": hashlib.sha256(payload_bytes).hexdigest(),
        "status": "complete" if observations else "empty_observed_response",
        "observations": observations, "observed_publication_count": len(observations),
        "reported_total_results": total,
        "result_cap_reached": total is not None and total > query.max_records,
        "source_result_is_exhaustive": False,
        "unique_family_ids_observed": len({item["family_id"] for item in observations if item["family_id"]}),
        "family_deduplication_applied": False, "missing_is_zero": False,
        "stores_full_text": False, "scientific_score_modified": False,
        "limitations": [load_policy()["patents"]["limitation"]],
    }
    result["report_payload_sha256"] = digest(result)
    return result


def unavailable(query: PatentQuery, status: str, retrieved_at: str,
                url: str | None = None, http_status: int | None = None,
                payload_bytes: bytes = b"", reason: str | None = None) -> dict:
    start, end, as_of = query.validate()
    result = {
        "version": VERSION, "source": "epo_ops", "role": "patent_landscape_only",
        "topic_id": query.topic_id.strip(),
        "query": {"phrase": query.phrase.strip(), "cql": cql(query),
                  "start": start.isoformat(), "end": end.isoformat(),
                  "as_of": as_of.isoformat(), "max_records": query.max_records},
        "request": {"url": url, "final_url": None, "http_status": http_status,
                    "credentials_stored": False},
        "retrieved_at": retrieved_at,
        "raw_response_bytes_sha256": hashlib.sha256(payload_bytes).hexdigest() if payload_bytes else None,
        "status": status, "status_reason": reason, "observations": None,
        "observed_publication_count": None, "reported_total_results": None,
        "result_cap_reached": None, "unique_family_ids_observed": None,
        "source_result_is_exhaustive": False,
        "family_deduplication_applied": False, "missing_is_zero": False,
        "stores_full_text": False, "scientific_score_modified": False,
        "limitations": [load_policy()["patents"]["limitation"]],
    }
    result["report_payload_sha256"] = digest(result)
    return result


def _access_token(key: str, secret: str, timeout: float) -> str:
    policy = load_policy()["patents"]
    basic = base64.b64encode(f"{key}:{secret}".encode()).decode()
    request = Request(
        policy["token_url"], data=b"grant_type=client_credentials", method="POST",
        headers={"Authorization": f"Basic {basic}",
                 "Content-Type": "application/x-www-form-urlencoded",
                 "User-Agent": "SAIA-research-prototype/0.4.30"},
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read())
    token = payload.get("access_token")
    if not isinstance(token, str) or not token:
        raise ValueError("EPO OAuth response has no access token.")
    return token


def fetch(query: PatentQuery, timeout: float = 30.0,
          consumer_key: str | None = None, consumer_secret: str | None = None) -> dict:
    query.validate()
    policy = load_policy()["patents"]["credentials"]
    key = consumer_key if consumer_key is not None else os.environ.get(policy["key_env"])
    secret = consumer_secret if consumer_secret is not None else os.environ.get(policy["secret_env"])
    retrieved = datetime.now(timezone.utc).isoformat()
    if not key or not secret:
        return unavailable(query, "credentials_required", retrieved,
                           reason="epo_oauth_credentials_not_configured")
    url = search_url(query)
    try:
        token = _access_token(key, secret, timeout)
        request = Request(url, headers={"Authorization": f"Bearer {token}",
                                       "Accept": "application/exchange+xml",
                                       "User-Agent": "SAIA-research-prototype/0.4.30"})
        with urlopen(request, timeout=timeout) as response:
            payload = response.read(MAX_RESPONSE_BYTES + 1)
            return parse_response(query, payload, retrieved, url, response.geturl(), response.status)
    except HTTPError as error:
        payload = error.read()
        status = "credentials_rejected" if error.code in (401, 403) else "source_http_error"
        return unavailable(query, status, retrieved, url, error.code, payload, f"http_{error.code}")
    except (URLError, TimeoutError) as error:
        return unavailable(query, "source_unavailable", retrieved, url, reason=type(error).__name__)
