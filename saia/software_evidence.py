"""Exact package history from deps.dev as software evidence, never score."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from saia.external_sources import VERSION, load_policy
from saia.hybrid import digest


def _day(value: str | date) -> date:
    return value if isinstance(value, date) else date.fromisoformat(value)


@dataclass(frozen=True)
class SoftwareQuery:
    topic_id: str
    system: str
    package: str
    start: str | date
    end: str | date
    as_of: str | date
    max_versions: int = 50

    def validate(self) -> tuple[date, date, date]:
        policy = load_policy()["software"]
        system, package = self.system.strip().lower(), self.package.strip()
        if not self.topic_id.strip() or system not in policy["allowed_systems"]:
            raise ValueError("Нужны topic ID и поддерживаемая пакетная экосистема.")
        if not 1 <= len(package) <= 200 or any(ord(char) < 32 for char in package):
            raise ValueError("Некорректное точное имя пакета.")
        start, end, as_of = _day(self.start), _day(self.end), _day(self.as_of)
        if end < start or end > as_of:
            raise ValueError("Некорректный интервал версий пакета.")
        if not 1 <= self.max_versions <= int(policy["max_versions"]):
            raise ValueError("Превышен лимит версий пакета.")
        return start, end, as_of


def request_url(query: SoftwareQuery) -> str:
    query.validate()
    template = load_policy()["software"]["package_url"]
    return template.format(system=quote(query.system.strip().lower(), safe=""),
                           package=quote(query.package.strip(), safe=""))


def _published_day(value: object) -> str:
    text = str(value or "")[:10]
    return date.fromisoformat(text).isoformat()


def parse_response(query: SoftwareQuery, payload_bytes: bytes, retrieved_at: str,
                   url: str, final_url: str | None = None,
                   http_status: int = 200) -> dict:
    start, end, as_of = query.validate()
    maximum = int(load_policy()["software"]["max_response_bytes"])
    if len(payload_bytes) > maximum:
        raise ValueError("deps.dev response exceeds the bounded connector limit.")
    payload = json.loads(payload_bytes)
    rows = payload.get("versions")
    if not isinstance(rows, list):
        raise ValueError("deps.dev response has no versions list.")
    through_as_of, in_window = [], []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("versionKey"), dict):
            raise ValueError("Invalid deps.dev version record.")
        version = str(row["versionKey"].get("version") or "").strip()
        if not version:
            raise ValueError("deps.dev version record has no version.")
        published = _published_day(row.get("publishedAt"))
        item = {
            "version": version, "published_at": published,
            "is_default": bool(row.get("isDefault")),
            "is_deprecated": bool(row.get("isDeprecated")),
            "deprecated_reason": row.get("deprecatedReason") or None,
            "url": f"https://deps.dev/{query.system.strip().lower()}/{quote(query.package.strip(), safe='')}/{quote(version, safe='')}",
        }
        if published <= as_of.isoformat():
            through_as_of.append(item)
        if start.isoformat() <= published <= end.isoformat():
            in_window.append(item)
    in_window.sort(key=lambda item: (item["published_at"], item["version"]), reverse=True)
    observations = in_window[:query.max_versions]
    first_release = min((item["published_at"] for item in through_as_of), default=None)
    source_key = payload.get("packageKey") if isinstance(payload.get("packageKey"), dict) else {}
    result = {
        "version": VERSION, "source": "deps_dev", "role": "software_diffusion_only",
        "topic_id": query.topic_id.strip(),
        "query": {"system": query.system.strip().lower(), "package": query.package.strip(),
                  "start": start.isoformat(), "end": end.isoformat(),
                  "as_of": as_of.isoformat(), "max_versions": query.max_versions},
        "request": {"url": url, "final_url": final_url, "http_status": http_status},
        "retrieved_at": retrieved_at,
        "raw_response_bytes_sha256": hashlib.sha256(payload_bytes).hexdigest(),
        "status": "complete" if observations else "empty_observed_response",
        "package_key": source_key,
        "observations": observations,
        "observed_version_count": len(observations),
        "source_version_count": len(rows),
        "versions_through_as_of_count": len(through_as_of),
        "first_release_date_through_as_of": first_release,
        "current_inventory_contains_post_as_of_versions": any(
            item["published_at"] > as_of.isoformat()
            for item in [
                {"published_at": _published_day(row.get("publishedAt"))}
                for row in rows if isinstance(row, dict)
            ]
        ),
        "result_cap_reached": len(in_window) > len(observations),
        "source_result_is_exhaustive": len(in_window) <= len(observations),
        "exact_package_identity_required": True,
        "adoption_established": False, "retrospective_safe": False,
        "missing_is_zero": False, "scientific_score_modified": False,
        "limitations": [load_policy()["software"]["limitation"]],
    }
    result["report_payload_sha256"] = digest(result)
    return result


def unavailable(query: SoftwareQuery, status: str, retrieved_at: str, url: str,
                http_status: int | None = None, payload_bytes: bytes = b"",
                retry_after: str | None = None, reason: str | None = None,
                observed_empty: bool = False) -> dict:
    start, end, as_of = query.validate()
    result = {
        "version": VERSION, "source": "deps_dev", "role": "software_diffusion_only",
        "topic_id": query.topic_id.strip(),
        "query": {"system": query.system.strip().lower(), "package": query.package.strip(),
                  "start": start.isoformat(), "end": end.isoformat(),
                  "as_of": as_of.isoformat(), "max_versions": query.max_versions},
        "request": {"url": url, "final_url": None, "http_status": http_status,
                    "retry_after": retry_after},
        "retrieved_at": retrieved_at,
        "raw_response_bytes_sha256": hashlib.sha256(payload_bytes).hexdigest() if payload_bytes else None,
        "status": status, "status_reason": reason,
        "observations": [] if observed_empty else None,
        "observed_version_count": 0 if observed_empty else None,
        "source_version_count": 0 if observed_empty else None,
        "versions_through_as_of_count": 0 if observed_empty else None,
        "first_release_date_through_as_of": None,
        "current_inventory_contains_post_as_of_versions": None,
        "result_cap_reached": False if observed_empty else None,
        "source_result_is_exhaustive": observed_empty,
        "exact_package_identity_required": True,
        "adoption_established": False, "retrospective_safe": False,
        "missing_is_zero": False, "scientific_score_modified": False,
        "limitations": [load_policy()["software"]["limitation"]],
    }
    result["report_payload_sha256"] = digest(result)
    return result


def fetch(query: SoftwareQuery, timeout: float = 30.0) -> dict:
    url, retrieved = request_url(query), datetime.now(timezone.utc).isoformat()
    maximum = int(load_policy()["software"]["max_response_bytes"])
    request = Request(url, headers={"Accept": "application/json",
                                    "User-Agent": "SAIA-research-prototype/0.4.30"})
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = response.read(maximum + 1)
            return parse_response(query, payload, retrieved, url, response.geturl(), response.status)
    except HTTPError as error:
        payload = error.read(maximum + 1)
        if error.code == 404:
            return unavailable(query, "package_not_found", retrieved, url, error.code,
                               payload, reason="exact_package_not_found", observed_empty=True)
        status = "rate_limited" if error.code == 429 else "source_http_error"
        return unavailable(query, status, retrieved, url, error.code, payload,
                           error.headers.get("Retry-After"), f"http_{error.code}")
    except (URLError, TimeoutError) as error:
        return unavailable(query, "source_unavailable", retrieved, url,
                           reason=type(error).__name__)
