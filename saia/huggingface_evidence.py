"""Bounded Hugging Face Hub model/dataset/Space evidence, never score."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from saia.external_sources import VERSION, load_policy
from saia.hybrid import digest


def _day(value: str | date) -> date:
    return value if isinstance(value, date) else date.fromisoformat(value)


def _iso_day(value: object) -> str | None:
    text = str(value or "")[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


@dataclass(frozen=True)
class HuggingFaceQuery:
    topic_id: str
    query: str
    start: str | date
    end: str | date
    as_of: str | date
    max_records: int = 10

    def validate(self) -> tuple[date, date, date]:
        text = self.query.strip()
        if not self.topic_id.strip() or not 2 <= len(text) <= 200:
            raise ValueError("Нужны topic ID и запрос Hugging Face от 2 до 200 знаков.")
        if any(ord(char) < 32 for char in text):
            raise ValueError("Запрос Hugging Face содержит управляющие символы.")
        start, end, as_of = _day(self.start), _day(self.end), _day(self.as_of)
        if end < start or end > as_of:
            raise ValueError("Некорректный интервал Hugging Face.")
        if not 1 <= self.max_records <= int(load_policy()["ai_artifacts"]["max_records"]):
            raise ValueError("Превышен лимит артефактов Hugging Face.")
        return start, end, as_of


def request_urls(query: HuggingFaceQuery) -> dict[str, str]:
    query.validate()
    policy = load_policy()["ai_artifacts"]
    params = urlencode({"search": query.query.strip(), "sort": "createdAt",
                        "direction": -1, "limit": query.max_records, "full": "true"})
    return {surface: f"{policy['base_url']}/{surface}?{params}" for surface in policy["surfaces"]}


def parse_response(query: HuggingFaceQuery, payloads: dict[str, bytes], retrieved_at: str,
                   urls: dict[str, str], http_statuses: dict[str, int] | None = None) -> dict:
    start, end, as_of = query.validate()
    policy = load_policy()["ai_artifacts"]
    maximum = int(policy["max_response_bytes"])
    observations = []
    hashes = {}
    for surface in policy["surfaces"]:
        payload_bytes = payloads.get(surface)
        if not isinstance(payload_bytes, bytes):
            raise ValueError(f"Hugging Face response is missing surface: {surface}.")
        if len(payload_bytes) > maximum:
            raise ValueError("Hugging Face response exceeds the bounded connector limit.")
        rows = json.loads(payload_bytes)
        if not isinstance(rows, list):
            raise ValueError("Hugging Face response is not a list.")
        hashes[surface] = hashlib.sha256(payload_bytes).hexdigest()
        singular = {"models": "model", "datasets": "dataset", "spaces": "space"}[surface]
        for row in rows:
            if not isinstance(row, dict):
                continue
            repo_id = str(row.get("id") or row.get("modelId") or "").strip()
            created = _iso_day(row.get("createdAt"))
            if not repo_id or not created or not start.isoformat() <= created <= end.isoformat():
                continue
            card = row.get("cardData") if isinstance(row.get("cardData"), dict) else {}
            observations.append({
                "artifact_type": singular,
                "repo_id": repo_id,
                "title": card.get("pretty_name") or card.get("title") or repo_id,
                "author": row.get("author"),
                "created_at": created,
                "last_modified": row.get("lastModified"),
                "downloads_current": row.get("downloads"),
                "likes_current": row.get("likes"),
                "gated": row.get("gated"),
                "tags": [str(tag) for tag in (row.get("tags") or [])[:30]],
                "sha": row.get("sha"),
                "url": f"https://huggingface.co/{'datasets/' if singular == 'dataset' else 'spaces/' if singular == 'space' else ''}{quote(repo_id, safe='/')}",
            })
    observations.sort(key=lambda item: (item["created_at"], item["artifact_type"], item["repo_id"]), reverse=True)
    counts = {kind: sum(item["artifact_type"] == kind for item in observations)
              for kind in ("model", "dataset", "space")}
    result = {
        "version": VERSION, "source": "huggingface_hub", "role": "ai_artifact_diffusion_only",
        "topic_id": query.topic_id.strip(),
        "query": {"text": query.query.strip(), "start": start.isoformat(),
                  "end": end.isoformat(), "as_of": as_of.isoformat(),
                  "max_records_per_surface": query.max_records},
        "request": {"urls": urls, "http_statuses": http_statuses or {key: 200 for key in urls}},
        "retrieved_at": retrieved_at,
        "raw_response_sha256_by_surface": hashes,
        "status": "complete" if observations else "empty_observed_response",
        "observations": observations,
        "observed_artifact_count": len(observations),
        "observed_counts_by_type": counts,
        "observed_author_count_unverified": len({item["author"] for item in observations if item["author"]}),
        "current_engagement_is_historical_evidence": False,
        "source_result_is_exhaustive": False,
        "retrospective_safe": False,
        "missing_is_zero": False, "scientific_score_modified": False,
        "limitations": [policy["limitation"]],
    }
    result["report_payload_sha256"] = digest(result)
    return result


def unavailable(query: HuggingFaceQuery, status: str, retrieved_at: str,
                urls: dict[str, str], statuses: dict[str, int | None],
                reasons: dict[str, str]) -> dict:
    start, end, as_of = query.validate()
    result = {
        "version": VERSION, "source": "huggingface_hub", "role": "ai_artifact_diffusion_only",
        "topic_id": query.topic_id.strip(),
        "query": {"text": query.query.strip(), "start": start.isoformat(),
                  "end": end.isoformat(), "as_of": as_of.isoformat(),
                  "max_records_per_surface": query.max_records},
        "request": {"urls": urls, "http_statuses": statuses},
        "retrieved_at": retrieved_at,
        "status": status, "status_reason": reasons, "observations": None,
        "observed_artifact_count": None, "observed_counts_by_type": None,
        "current_engagement_is_historical_evidence": False,
        "source_result_is_exhaustive": False, "retrospective_safe": False,
        "missing_is_zero": False, "scientific_score_modified": False,
        "limitations": [load_policy()["ai_artifacts"]["limitation"]],
    }
    result["report_payload_sha256"] = digest(result)
    return result


def fetch(query: HuggingFaceQuery, timeout: float = 30.0) -> dict:
    urls, retrieved = request_urls(query), datetime.now(timezone.utc).isoformat()
    maximum = int(load_policy()["ai_artifacts"]["max_response_bytes"])
    payloads, statuses, reasons = {}, {}, {}
    for surface, url in urls.items():
        request = Request(url, headers={"Accept": "application/json",
                                        "User-Agent": "SAIA-research-prototype/0.4.31"})
        try:
            with urlopen(request, timeout=timeout) as response:
                payloads[surface] = response.read(maximum + 1)
                statuses[surface] = response.status
        except HTTPError as error:
            statuses[surface] = error.code
            reasons[surface] = "rate_limited" if error.code == 429 else f"http_{error.code}"
        except (URLError, TimeoutError) as error:
            statuses[surface] = None
            reasons[surface] = type(error).__name__
    if len(payloads) != len(urls):
        status = "rate_limited" if any(value == "rate_limited" for value in reasons.values()) else "source_unavailable"
        return unavailable(query, status, retrieved, urls, statuses, reasons)
    return parse_response(query, payloads, retrieved, urls, statuses)
