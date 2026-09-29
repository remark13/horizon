"""Versioned scout hypotheses over a frozen card, not another scoring model.

PESTLE and industry effects are author-written possible consequences. A valid
reference identifies a real saved source, not the truth of the interpretation.
No remote retrieval, model call, expert verdict, or scientific mutation occurs.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from psycopg.types.json import Jsonb

from saia import candidate_external_links, db, external_evidence_store, source_context
from saia.hybrid import digest

VERSION = "candidate-impact-note-v1"
DIMENSIONS = {
    "political": "Политика и государственные приоритеты",
    "economic": "Экономика и рынок",
    "social": "Общество и люди",
    "technological": "Технологии",
    "legal": "Право и регулирование",
    "environmental": "Экология",
}
EFFECTS = {"opportunity": "Возможность", "risk": "Риск", "mixed": "Возможности и риски", "unknown": "Не определено"}
HORIZONS = {"not_assessed": "Срок не оценён", "0_2_years": "До 2 лет", "3_5_years": "3–5 лет", "over_5_years": "Более 5 лет"}


class RevisionConflict(ValueError):
    pass


def _text(value: object, name: str, maximum: int, minimum: int = 0) -> str:
    if not isinstance(value, str) or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", value):
        raise ValueError(f"Поле «{name}» должно содержать обычный текст.")
    result = value.strip()
    if not minimum <= len(result) <= maximum:
        raise ValueError(f"Поле «{name}»: от {minimum} до {maximum} символов.")
    return result


def scope(mission: str, score: int, candidate: int, card: dict) -> dict:
    return {"mission_id": mission, "score_run_id": score, "candidate_id": candidate,
            "composition_sha256": card["composition_sha256"],
            "evidence_sha256": digest(publication_references(card)), "version": VERSION}


def publication_references(card: dict) -> list[dict]:
    result, seen = [], set()
    for item in card.get("evidence") or []:
        for link in item.get("sources") or []:
            url = link.get("url")
            if not isinstance(url, str) or url in seen:
                continue
            from urllib.parse import urlsplit
            parsed = urlsplit(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None or parsed.password is not None:
                continue
            seen.add(url)
            result.append({"id": "publication:" + digest(url), "kind": "publication",
                           "title": item.get("title") or url, "url": url,
                           "date": item.get("published_at"), "date_kind": "publication",
                           "source": link.get("type") or "scientific_publication",
                           "expert_validated": False, "source_connection_verified": True})
    return result


def external_reference(saved: dict, record: dict) -> dict:
    item = source_context.passport(saved["source"], record, saved)
    if item is None:
        raise ValueError("У материала нет допустимой исходной ссылки.")
    return {"id": f"external:{saved['observation_id']}:{digest(item['url'])}",
            "kind": "external", "title": item["title_original"], "url": item["url"],
            "date": item["record_date"], "date_kind": item["date_kind"],
            "source": saved["source"], "source_name": item["source_name"],
            "observation_id": saved["observation_id"], "observation_sha256": saved["report_payload_sha256"],
            "retrieved_at": item["retrieved_at"], "expert_validated": False,
            "source_connection_verified": True}


def resolve_reference(identifier: str, binding: dict, card: dict) -> dict:
    pubs = {ref["id"]: ref for ref in publication_references(card)}
    if identifier in pubs:
        return pubs[identifier]
    parts = identifier.split(":") if isinstance(identifier, str) else []
    if len(parts) != 3 or parts[0] != "external":
        raise ValueError("Основание отсутствует в источниках этой карточки.")
    try:
        observation_id = str(uuid.UUID(parts[1]))
    except ValueError:
        raise ValueError("Неверный идентификатор сохранённого основания.") from None
    saved = external_evidence_store.read(observation_id)
    if saved["status"] != "complete":
        raise ValueError("Основание должно быть отдельной записью успешно полученного источника.")
    captured = saved["payload"].get("candidate_context_binding") or {}
    exact = all(captured.get(key) == binding[key] for key in ("mission_id", "score_run_id", "candidate_id", "composition_sha256"))
    records = [r for r in saved["payload"].get("observations", [])
               if isinstance(r, dict) and isinstance(r.get("url"), str) and digest(r["url"]) == parts[2]]
    if len(records) != 1:
        raise ValueError("Не найдена единственная запись сохранённого основания.")
    if not exact:
        linked = candidate_external_links.for_candidate(binding["mission_id"], binding["score_run_id"], binding["candidate_id"])
        exact = any(row["observation_id"] == observation_id and row["record_url"] == records[0]["url"] for row in linked["links"])
    if not exact:
        raise ValueError("Материал не относится к выбранному прогону и составу карточки.")
    return external_reference(saved, records[0])


def normalize_content(value: dict, binding: dict, card: dict) -> dict:
    if not isinstance(value, dict) or set(value) != {"author", "summary", "pestle", "industry_impacts"}:
        raise ValueError("Неверная структура аналитической заметки.")
    result = {"author": _text(value["author"], "Автор", 120, 1),
              "summary": _text(value["summary"], "Вывод", 1800), "pestle": [], "industry_impacts": []}
    references = {}
    for group, maximum in (("pestle", 6), ("industry_impacts", 8)):
        rows = value[group]
        if not isinstance(rows, list) or len(rows) > maximum:
            raise ValueError(f"Слишком много записей в разделе {group}.")
        for row in rows:
            key = "dimension" if group == "pestle" else "industry"
            if not isinstance(row, dict) or set(row) != {key, "text", "effect", "horizon", "dependencies", "evidence_ids"}:
                raise ValueError("Неверная структура гипотезы влияния.")
            if not isinstance(row["effect"], str) or row["effect"] not in EFFECTS or not isinstance(row["horizon"], str) or row["horizon"] not in HORIZONS:
                raise ValueError("Выберите тип влияния и временной горизонт.")
            target = row[key]
            if group == "pestle" and (not isinstance(target, str) or target not in DIMENSIONS):
                raise ValueError("Неизвестное измерение PESTLE.")
            if group == "industry_impacts":
                target = _text(target, "Отрасль", 120, 2)
            ids = row["evidence_ids"]
            if not isinstance(ids, list) or len(ids) > 6 or any(not isinstance(i, str) or len(i) > 160 for i in ids) or len(ids) != len(set(ids)):
                raise ValueError("Укажите до шести различных оснований гипотезы.")
            for identifier in ids:
                if identifier not in references:
                    references[identifier] = resolve_reference(identifier, binding, card)
            result[group].append({key: target, "text": _text(row["text"], "Гипотеза", 1800, 10),
                                  "effect": row["effect"], "horizon": row["horizon"],
                                  "dependencies": _text(row["dependencies"], "Условия", 800),
                                  "evidence_ids": list(ids),
                                  "status": "evidence_linked_hypothesis" if ids else "hypothesis_without_sources"})
    dimensions = [row["dimension"] for row in result["pestle"]]
    if len(set(dimensions)) != len(dimensions):
        raise ValueError("Для каждого измерения PESTLE сохраните одну сводную гипотезу.")
    if not result["summary"] and not result["pestle"] and not result["industry_impacts"]:
        raise ValueError("Добавьте хотя бы один вывод или гипотезу.")
    result["references"] = list(references.values())
    return result


def checked_payload(payload: dict, checksum: str, binding: dict) -> dict:
    canonical = dict(payload)
    canonical.pop("report_sha256", None)
    if payload.get("binding") != binding or payload.get("report_sha256") != checksum or digest(canonical) != checksum:
        raise ValueError("Заметка не совпала с составом карточки или контрольной суммой.")
    return payload


def _latest(cur, binding: dict) -> dict | None:
    cur.execute("SELECT payload,report_sha256 FROM candidate_analysis_note WHERE candidate_id=%s ORDER BY revision DESC LIMIT 1",
                (binding["candidate_id"],))
    row = cur.fetchone()
    return checked_payload(row[0], row[1], binding) if row else None


def read(mission: str, score: int, candidate: int) -> dict:
    card = candidate_external_links._candidate(mission, score, candidate)
    binding = scope(mission, score, candidate, card)
    with db.connect() as conn, conn.cursor() as cur:
        current = _latest(cur, binding)
        cur.execute("SELECT revision,note_id,created_at FROM candidate_analysis_note WHERE candidate_id=%s ORDER BY revision DESC LIMIT 20", (candidate,))
        history = [{"revision": r[0], "note_id": str(r[1]), "created_at": r[2].isoformat()} for r in cur.fetchall()]
    return {"binding": binding, "note": current, "history": history,
            "revision": current["revision"] if current else 0,
            "scientific_results_modified": False, "expert_validation_required": False}


def reference_catalog(mission: str, score: int, candidate: int) -> dict:
    card = candidate_external_links._candidate(mission, score, candidate)
    refs = publication_references(card)
    context = source_context.read(mission, score, candidate)
    saved = {}
    for report in context["reports"]:
        for material in report["materials"]:
            identifier = material["observation_id"]
            if identifier not in saved:
                saved[identifier] = external_evidence_store.read(identifier)
            ref = external_reference(saved[identifier], next(r for r in saved[identifier]["payload"]["observations"] if r["url"] == material["url"]))
            refs.append(ref)
    links = candidate_external_links.for_candidate(mission, score, candidate)
    for row in links["links"]:
        identifier = row["observation_id"]
        if identifier not in saved:
            saved[identifier] = external_evidence_store.read(identifier)
        refs.append(external_reference(saved[identifier], row["record_snapshot"]))
    return {"binding": scope(mission, score, candidate, card), "references": list({r["id"]: r for r in refs}.values()),
            "reference_scope": "saved_card_samples_and_collected_or_scout_linked_records",
            "source_ids_checked_not_claim_truth": True}


def save(mission: str, score: int, candidate: int, content: dict,
         expected_revision: int, operation_id: str | None = None) -> dict:
    if not isinstance(expected_revision, int) or isinstance(expected_revision, bool) or not 0 <= expected_revision <= 100000:
        raise ValueError("Некорректная версия редактируемой заметки.")
    try:
        note_id = str(uuid.UUID(str(operation_id))) if operation_id is not None else str(uuid.uuid4())
    except ValueError:
        raise ValueError("Ключ сохранения должен быть UUID.") from None
    card = candidate_external_links._candidate(mission, score, candidate)
    binding = scope(mission, score, candidate, card)
    normalized = normalize_content(content, binding, card)
    input_sha = digest({"binding": binding, "expected_revision": expected_revision, "content": normalized})
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s),hashtext(%s))", (VERSION, str(candidate)))
        cur.execute("SELECT payload,report_sha256 FROM candidate_analysis_note WHERE note_id=%s", (note_id,))
        old = cur.fetchone()
        if old:
            payload = checked_payload(old[0], old[1], binding)
            if payload["input_sha256"] != input_sha:
                raise RevisionConflict("Этот ключ уже использован для другой заметки.")
            return {"note": payload, "replayed": True, "scientific_results_modified": False}
        previous = _latest(cur, binding)
        revision = previous["revision"] if previous else 0
        if revision != expected_revision:
            raise RevisionConflict("Заметка уже изменена. Сначала обновите её; ваш несохранённый текст остаётся в форме.")
        payload = {"note_id": note_id, "binding": binding, "revision": revision + 1,
                   "previous_note_id": previous["note_id"] if previous else None,
                   "content": normalized, "input_sha256": input_sha,
                   "created_at": datetime.now(timezone.utc).isoformat(),
                   "status": "scout_hypotheses_not_expert_validated", "author_identity_verified": False,
                   "scientific_results_modified": False, "claim_faithfulness_verified": False}
        payload["report_sha256"] = digest(payload)
        cur.execute("INSERT INTO candidate_analysis_note (note_id,mission_id,score_run_id,candidate_id,composition_sha256,"
                    "revision,version,report_sha256,payload) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (note_id, mission, score, candidate, binding["composition_sha256"], payload["revision"], VERSION, payload["report_sha256"], Jsonb(payload)))
    return {"note": payload, "replayed": False, "scientific_results_modified": False}
