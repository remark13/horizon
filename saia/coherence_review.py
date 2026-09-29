"""Read-only blinded paper-pair packet and stateless submission validation.

This development evaluation is separate from optional expert validation of a
scout card. A syntactically valid submission is not an independent gold label.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from saia.controlled_collection import sha256_file


VERSION = "bas-coherence-review-submission-v1"
PACKET_PATH = (Path(__file__).resolve().parents[1] / "evaluation" /
               "bas-blind-pair-review-2026-09-26-v2.json")
ANSWERS = frozenset({"yes", "no", "unclear"})
QUESTIONS = ("same_research_problem", "same_technical_mechanism",
             "one_signal_line")


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def read_packet() -> dict:
    packet = json.loads(PACKET_PATH.read_text(encoding="utf-8"))
    cases = packet.get("cases") or []
    if (packet.get("version") != "bas-blind-pair-review-v2"
            or len(cases) != 30
            or len({item.get("case_id") for item in cases}) != 30
            or any(not item.get("paper_a", {}).get("abstract")
                   or not item.get("paper_b", {}).get("abstract")
                   or not item.get("paper_a", {}).get("source_url", "").startswith("https://")
                   or not item.get("paper_b", {}).get("source_url", "").startswith("https://")
                   or any(value is not None for value in
                          (item.get("review_fields") or {}).values())
                   for item in cases)):
        raise ValueError("Blinded coherence packet is incomplete or already annotated")
    blind_cases = [{key: value for key, value in item.items()
                    if key != "review_fields"} for item in cases]
    return {"version": packet["version"], "packet_sha256": sha256_file(PACKET_PATH),
            "review_instructions": packet["review_instructions"], "cases": blind_cases,
            "limitations": packet["limitations"]}


def validate_submission(submission: dict) -> dict:
    packet = read_packet()
    if submission.get("packet_sha256") != packet["packet_sha256"]:
        raise ValueError("Пакет изменился; откройте анкету заново.")
    reviewer = " ".join(str(submission.get("reviewer_id") or "").split())
    if not 1 <= len(reviewer) <= 120:
        raise ValueError("Укажите код рецензента до 120 символов.")
    if submission.get("independent_review_declared") is not True:
        raise ValueError("Подтвердите самостоятельное заполнение анкеты.")
    answers = submission.get("answers")
    if not isinstance(answers, list) or len(answers) != len(packet["cases"]):
        raise ValueError("Нужны ответы на все 30 пар.")
    by_id = {item["case_id"]: item for item in packet["cases"]}
    if (len({answer.get("case_id") for answer in answers
             if isinstance(answer, dict)}) != len(by_id)
            or {answer.get("case_id") for answer in answers
                if isinstance(answer, dict)} != set(by_id)):
        raise ValueError("Номера пар в анкете не совпадают с закреплённым пакетом.")
    clean = []
    for answer in answers:
        case = by_id[answer["case_id"]]
        row = {"case_id": answer["case_id"]}
        for field in QUESTIONS:
            if answer.get(field) not in ANSWERS:
                raise ValueError(f"Не заполнен ответ {field} для {row['case_id']}.")
            row[field] = answer[field]
        for side in ("a", "b"):
            field = f"evidence_quote_{side}"
            quote = " ".join(str(answer.get(field) or "").split())
            source = _normalized(" ".join((case[f"paper_{side}"]["title"],
                                           case[f"paper_{side}"]["abstract"])))
            if not 8 <= len(quote) <= 500 or _normalized(quote) not in source:
                raise ValueError(
                    f"Фрагмент {side.upper()} для {row['case_id']} должен дословно "
                    "встречаться в названии или аннотации статьи."
                )
            row[field] = quote
        rationale = " ".join(str(answer.get("rationale") or "").split())
        if not 20 <= len(rationale) <= 3000:
            raise ValueError(f"Обоснование для {row['case_id']} должно содержать 20–3000 символов.")
        row["rationale"] = rationale
        clean.append(row)
    result = {"version": VERSION, "packet_sha256": packet["packet_sha256"],
              "reviewer_id": reviewer, "independent_review_declared": True,
              "validated_at": datetime.now(timezone.utc).isoformat(),
              "answers": clean,
              "interpretation": "Отдельное мнение рецензента, не gold и не изменение карточек Horizon."}
    result["submission_sha256"] = hashlib.sha256(json.dumps(
        result, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    return result
