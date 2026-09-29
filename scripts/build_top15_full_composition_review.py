"""Export a rank-blinded review of every work in saved top-15 queues.

The public packet is not a gold standard. It contains no stored score, rank,
gate, status, or expert decision. The private key is kept in a separate file.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from pathlib import Path

from saia import db
from saia.candidates import export_cards
from saia.triage import build_queue


QUESTIONS = {
    "research_line_coherence": "Образуют ли публикации одну достаточно узкую технологическую линию?",
    "primary_result_evidence": "Есть ли первичное техническое или экспериментальное исследование, а не только обзоры и новости?",
    "independent_groups_evidence": "Есть ли хотя бы две независимые исследовательские группы?",
    "weak_signal_at_cutoff": "Достаточно ли публикационных данных, чтобы считать это кандидатом слабого сигнала на дату среза?",
}
QUESTIONS_V2 = {
    "direction_relevance": (
        "Соответствует ли предлагаемая технологическая линия исходному направлению "
        "пользовательского запроса, а не только смежной области?"
    ),
    **QUESTIONS,
}
CHOICES = "yes / no / uncertain / not_assessable"


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def _sources(identifiers: list[tuple[str | None, str | None]]) -> list[str]:
    links = []
    for kind, value in identifiers:
        if not value:
            continue
        if kind == "arxiv":
            links.append("https://arxiv.org/abs/" + value)
        elif kind == "doi":
            links.append("https://doi.org/" + value)
        elif kind == "openalex":
            links.append(value if value.startswith("https://") else "https://openalex.org/" + value)
    return sorted(set(links))


def load_work_composition(topic_ids: list[int]) -> dict[int, list[dict]]:
    """Read full topic membership and bibliographic metadata without changing DB."""
    if not topic_ids or len(topic_ids) != len(set(topic_ids)):
        raise ValueError("Topic IDs must be nonempty and unique")
    by_topic = {topic_id: {} for topic_id in topic_ids}
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        cur.execute("""
            SELECT tm.topic_id, w.work_id, w.canonical_title, w.effective_date,
                   i.kind, i.value
            FROM topic_membership tm
            JOIN work w ON w.work_id = tm.work_id
            LEFT JOIN identifier i ON i.work_id = w.work_id
            WHERE tm.topic_id = ANY(%s)
            ORDER BY tm.topic_id, w.work_id, i.kind, i.value
        """, (topic_ids,))
        identifiers = {}
        for topic_id, work_id, title, published_at, kind, value in cur.fetchall():
            if not title or not published_at:
                raise ValueError(f"Work {work_id} lacks title/date")
            by_topic[topic_id][work_id] = {
                "title": title, "published_at": published_at.isoformat()}
            identifiers.setdefault((topic_id, work_id), []).append((kind, value))
    result = {}
    for topic_id, works in by_topic.items():
        if not works:
            raise ValueError(f"Topic {topic_id} has no works")
        result[topic_id] = [
            {**work, "sources": _sources(identifiers[(topic_id, work_id)])}
            for work_id, work in sorted(works.items(), key=lambda row: (
                row[1]["published_at"], row[1]["title"], row[0]))
        ]
    return result


def build(config: dict, queues: list[dict], compositions: dict[int, list[dict]]) -> tuple[dict, dict, str]:
    if config.get("version") not in {
            "top15-full-composition-review-v1", "top15-full-composition-review-v2"}:
        raise ValueError("Unknown frozen review configuration")
    questions = (QUESTIONS_V2 if config["version"].endswith("v2") else QUESTIONS)
    if len(queues) != len(config["runs"]):
        raise ValueError("Missing saved score queue")
    entries = []
    for run, queue in zip(config["runs"], queues, strict=True):
        if (queue["mission_id"] != run["mission_id"]
                or queue["score_run_id"] != run["score_run_id"]
                or len(queue["queue"]) != run["expected_cards"]):
            raise ValueError("Saved queue does not match frozen review configuration")
        as_of = queue["provenance"][0]["as_of_date"]
        for item in queue["queue"]:
            card = item["card"]
            topic_id = item["topic_id"]
            works = compositions.get(topic_id)
            if not works:
                raise ValueError(f"Full composition missing for topic {topic_id}")
            observed_count = ((card.get("metrics") or {}).get("observed") or {}).get("doc_count")
            if len(works) != observed_count:
                raise ValueError(f"Topic {topic_id}: {len(works)} works != observed {observed_count}")
            if any(not work["title"] or not work["published_at"] for work in works):
                raise ValueError(f"Topic {topic_id}: incomplete bibliographic metadata")
            basis = f'{run["score_run_id"]}:{card["candidate_id"]}:{card["composition_sha256"]}'
            blind_id = "item-" + hashlib.sha256(basis.encode()).hexdigest()[:16]
            points = ((card.get("metrics") or {}).get("publication_series") or {}).get("points") or []
            series = [{key: point.get(key) for key in (
                "start", "end", "topic_works", "corpus_works", "share", "complete",
                "coverage_comparable")} for point in points]
            entries.append((blind_id, {
                "item_id": blind_id,
                "direction": run["direction"],
                "generated_label": card["label"],
                "as_of_date": as_of,
                "full_composition_work_count": len(works),
                "works_shown": len(works),
                "works_truncated": False,
                "publication_series": series,
                "works": works,
            }, {
                "item_id": blind_id, "mission_id": run["mission_id"],
                "score_run_id": run["score_run_id"], "rank": item["rank"],
                "candidate_id": card["candidate_id"], "topic_id": topic_id,
                "composition_sha256": card["composition_sha256"],
                "system_status": card["status"],
            }))
    if len({id for id, _, _ in entries}) != len(entries):
        raise ValueError("Blind item IDs collide")
    entries.sort(key=lambda row: hashlib.sha256(
        f'{config["seed"]}:{row[0]}'.encode()).hexdigest())
    packet = {
        "version": "top15-full-composition-review-packet-" + config["version"].rsplit("-", 1)[-1],
        "purpose": "independent_development_review_not_gold",
        "scope": "Three saved publication-only runs; not ten-area accuracy or market validation",
        "blinding": "Stored rank, score, gates, status and developer decisions are hidden",
        "limitations": [
            "One work can occur in more than one topic; work counts are memberships, not unique studies.",
            "Publication shares depend on saved corpus and comparability flags; they are not market shares.",
            "Titles and links alone may not establish research role or independence: open originals.",
            "Commercial publications, patents and investment deals are not evaluated in this packet.",
        ],
        "questions": questions,
        "answer_choices": CHOICES,
        "items": [public for _, public, _ in entries],
    }
    packet["packet_payload_sha256"] = _digest(packet)
    private = {
        "version": "top15-full-composition-review-private-key-" + config["version"].rsplit("-", 1)[-1],
        "packet_payload_sha256": packet["packet_payload_sha256"],
        "mapping": [key for _, _, key in entries],
    }
    private["private_payload_sha256"] = _digest(private)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["item_id", *questions, "rationale", "source_urls", "reviewer_id"])
    for row in packet["items"]:
        writer.writerow([row["item_id"], *([""] * len(questions)), "", "", ""])
    return packet, private, output.getvalue()


def _write_new(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(value)


def render_markdown(packet: dict) -> str:
    """Readable counterpart of the same blinded JSON, with every work shown."""
    lines = [
        "# Независимая проверка карточек SAIA",
        "",
        "Это список кандидатов по публикациям, не подтверждённых сигналов. "
        "Порядок ниже перемешан; системные оценки и решения скрыты. "
        "Ссылки нужно открывать для проверки содержания. «Нет данных» — допустимый вывод.",
        "",
        "Ответы вносите в отдельный файл answer-template.csv. "
        "Закрытый ключ не открывайте до отправки индивидуального решения.",
        "",
    ]
    for item in packet["items"]:
        lines += [
            f'## {item["item_id"]}: {item["direction"]} — {item["generated_label"]}',
            "",
            f'Срез: {item["as_of_date"]}. Показаны все {item["works_shown"]} '
            "публикаций, вошедших в эту тему; это не обязательно столько же независимых исследований.",
            "",
            "Период | Работ по теме | Работ в корпусе | Доля | Сопоставимость",
            "--- | ---: | ---: | ---: | ---",
        ]
        for point in item["publication_series"]:
            share = point["share"]
            share_text = f"{share:.3f}" if isinstance(share, (float, int)) else "нет данных"
            lines.append(f'{point["start"]} — {point["end"]} | '
                         f'{point["topic_works"]} | {point["corpus_works"]} | '
                         f'{share_text} | '
                         f'{"да" if point["coverage_comparable"] else "нет"}')
        lines += ["", "Публикации:", ""]
        for number, work in enumerate(item["works"], 1):
            urls = ", ".join(f'<{url}>' for url in work["sources"]) or "прямой ссылки нет"
            lines.append(f'{number}. {work["title"]} ({work["published_at"]}). {urls}')
        lines += ["", "Вопросы для проверки:", ""]
        for name, question in packet["questions"].items():
            lines.append(f'- `{name}`: {question}')
        lines += ["", "Допустимые ответы: " + CHOICES + ".", ""]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config/top15-full-composition-review.v1.json"))
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    queues = [build_queue(export_cards(row["mission_id"], row["score_run_id"]), limit=15)
              for row in config["runs"]]
    topic_ids = [item["topic_id"] for queue in queues for item in queue["queue"]]
    compositions = load_work_composition(topic_ids)
    packet, private, template = build(config, queues, compositions)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for filename in ("review-packet.json", "review-packet.md", "private-key.json", "answer-template.csv"):
        if (args.output_dir / filename).exists():
            raise FileExistsError("Immutable packet already exists: " + filename)
    _write_new(args.output_dir / "review-packet.json",
               json.dumps(packet, ensure_ascii=False, indent=2) + "\n")
    _write_new(args.output_dir / "private-key.json",
               json.dumps(private, ensure_ascii=False, indent=2) + "\n")
    _write_new(args.output_dir / "answer-template.csv", template)
    _write_new(args.output_dir / "review-packet.md", render_markdown(packet))
    print(json.dumps({"items": len(packet["items"]),
                      "work_memberships": sum(len(item["works"]) for item in packet["items"]),
                      "works_without_links": sum(not work["sources"] for item in packet["items"]
                                                for work in item["works"]),
                      "packet_payload_sha256": packet["packet_payload_sha256"]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
