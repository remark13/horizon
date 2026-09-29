"""Publisher-scoped news discovery. Publishers are not separate aggregators."""
from __future__ import annotations

import json
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

VERSION = "news-publisher-directory-v1"
ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "config/news-publishers.v1.json"
CHECKS_PATH = ROOT / "data/reference/news_publishers/checks.2026-09-29.json"
BACKEND = "google_news_rss"
ENDPOINT = "https://news.google.com/rss/search"
USE_POLICY_RU = ("Личный просмотр заголовков, дат и ссылок через Google News. "
                 "Полные тексты и изображения не копируем. Права на материалы "
                 "остаются у издателя. Публичное переиздание и обучение моделей не разрешены этим подключением.")


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Повтор ключа в перечне издателей.")
        result[key] = value
    return result


def validate_directory(value: dict) -> dict:
    if (not isinstance(value, dict) or value.get("version") != VERSION
            or value.get("retrieval_backend") != BACKEND
            or value.get("independence_verified") is not False
            or value.get("automatic_default_polling") is not False):
        raise ValueError("Неверный паспорт перечня издателей.")
    rows = value.get("publishers")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Перечень издателей пуст.")
    ids, domains = set(), set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Неверная запись издателя.")
        domain = row.get("domain", "")
        if (not isinstance(domain, str) or len(domain) > 253
                or not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}", domain)
                or domain.endswith((".local", ".localhost", ".internal", ".test"))):
            raise ValueError("Нужен фиксированный публичный домен издателя.")
        identifier = "publisher_news_" + re.sub(r"[^a-z0-9]", "_", domain)
        if (row.get("id") != identifier or identifier in ids or domain in domains
                or row.get("homepage") != "https://" + domain + "/"
                or row.get("search_locale") not in {"ru", "en"}
                or row.get("source_kind") not in {"media_publication", "organisation_announcement"}):
            raise ValueError("Идентичность издателя повторяется или неверна.")
        for key in ("name", "category", "category_label_ru", "regional_coverage_ru", "trust_comment_ru", "probe_query"):
            text = row.get(key)
            if (not isinstance(text, str) or not text.strip() or len(text) > 1000
                    or any(ord(char) < 32 for char in text)):
                raise ValueError("Не заполнено описание издателя.")
        ids.add(identifier)
        domains.add(domain)
    return value


def read_directory(path: Path = MANIFEST_PATH) -> dict:
    return validate_directory(json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique))


@lru_cache(maxsize=1)
def publishers() -> dict[str, dict]:
    return {row["id"]: row for row in read_directory()["publishers"]}


def matches_publisher_url(source: str, url: object) -> bool:
    row = publishers().get(source)
    if row is None or not isinstance(url, str) or len(url) > 2000:
        return False
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        return (parsed.scheme in {"https", "http"} and parsed.username is None
                and parsed.password is None and parsed.port in (None, 80, 443)
                and (host == row["domain"] or host.endswith("." + row["domain"])))
    except ValueError:
        return False


def runtime_policy_rows() -> dict:
    return {identifier: {"source": identifier, "role": "news_attention_only", "url": ENDPOINT,
            "max_records": 10, "publisher_filter_domain": row["domain"], "publisher_backend": BACKEND,
            "limitation": "Publisher-scoped Google News metadata sample. Not a publisher API, complete corpus or independent confirmation. Original article URLs are not resolved. Personal title/link reading only. No article bodies, images or model training."}
            for identifier, row in publishers().items()}


def reader_flags(source: str) -> dict:
    row = publishers()[source]
    return {"publisher_filter_domain": row["domain"], "publisher_backend": BACKEND,
            "source_kind": row["source_kind"], "usage_scope": "personal_research",
            "model_input_allowed": False, "model_training_allowed": False,
            "bulk_reuse_approved": False, "stores_full_text": False,
            "public_republication_allowed": False, "stores_images": False,
            "publisher_directory_version": VERSION}


def registry_rows(path: Path = MANIFEST_PATH) -> list[dict]:
    return [{"id": row["id"], "name": row["name"], "decision": "adapt", "role": "news_attention_only",
             "data": "Publisher-filtered RSS titles, dates, publisher names and aggregator links. No article text or images.",
             "temporal_coverage": "A capped search sample within an explicit date window. Not a complete historical archive.",
             "regional_coverage": row["regional_coverage_ru"],
             "access": "Public Google News RSS search restricted to an allowlisted publisher domain. Not a guaranteed publisher API.",
             "credentials": "No key is used for this metadata reader.", "cost": "No paid subscription or purchase activated.",
             "license_status": "Scoped personal title/link reading. Full-text reuse, public republication and training rights are not established.",
             "reproducibility": "Directory version, publisher domain, query window, response SHA and retrieval date retained.",
             "retrotest_fit": "Current discovery does not establish past availability or total counts.",
             "reason": row["trust_comment_ru"], "evidence_urls": [row["homepage"], "https://support.google.com/googlenews/"],
             "publisher_backend": BACKEND, "source_kind": row["source_kind"]}
            for row in read_directory(path)["publishers"]]


def connection_checks() -> dict:
    if not CHECKS_PATH.is_file():
        return {}
    value = json.loads(CHECKS_PATH.read_text(encoding="utf-8"), object_pairs_hook=_unique)
    if (not isinstance(value, dict) or value.get("directory_version") != VERSION
            or not isinstance(value.get("checks"), list)):
        raise ValueError("Неверная версия проверки источников.")
    result = {}
    for row in value["checks"]:
        if (not isinstance(row, dict) or row.get("source") not in publishers()
                or row["source"] in result or row.get("basis") != "directory_connection_probe"
                or row.get("status") not in {"complete", "empty_observed_response", "source_unavailable",
                     "source_http_error", "rate_limited", "credentials_rejected", "invalid_source_response"}):
            raise ValueError("Неверная запись проверки источника.")
        datetime.fromisoformat(row["retrieved_at"])
        count = row.get("observed_count")
        if ((row["status"] == "complete" and (type(count) is not int or not 1 <= count <= 10))
                or (row["status"] == "empty_observed_response" and count != 0)
                or (row["status"] not in {"complete", "empty_observed_response"} and count is not None)):
            raise ValueError("Неизвестные результаты проверки не заменяем нулём.")
        result[row["source"]] = row
    return result
