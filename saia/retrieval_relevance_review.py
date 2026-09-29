"""Blinded review packet for frozen expansion-only retrieval assignments."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import uuid
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from saia.hybrid import digest
from saia.source_registry import file_sha256


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "retrieval-relevance-review.v0.4.26.yaml"
POLICY_VERSION = "retrieval-relevance-review-policy-0.4.26"
PACKET_VERSION = "retrieval-relevance-review-packet-0.4.26"
DYNAMIC_PACKET_VERSION = "balanced-job-retrieval-review-packet-0.4.38"
SUBMISSION_VERSION = "retrieval-relevance-review-submission-0.4.26"
COMPARISON_VERSION = "retrieval-relevance-review-comparison-0.4.26"
CURRENT_PACKET_PATH = (
    ROOT / "evaluation" / "jrc-arxiv-expansion-review-v0426-r1.packet.json"
)
CURRENT_TEMPLATE_PATH = (
    ROOT / "evaluation" / "jrc-arxiv-expansion-review-v0426-r1.template.json"
)


def load_policy(path: Path = POLICY_PATH) -> dict:
    policy = yaml.safe_load(path.read_text(encoding="utf-8"))
    validate_policy(policy)
    return policy


def validate_policy(policy: dict) -> None:
    if not isinstance(policy, dict) or policy.get("version") != POLICY_VERSION:
        raise ValueError("Неизвестная версия политики retrieval-разметки.")
    selection = policy.get("selection") or {}
    if selection.get("algorithm") != "all-frozen-expansion-only-samples-v1":
        raise ValueError("Изменён алгоритм формирования retrieval-пакета.")
    if selection.get("include_all_stored_samples") is not True:
        raise ValueError("Пакет должен включать все заранее сохранённые примеры.")
    if selection.get("sample_supports_micro_precision") is not False:
        raise ValueError("Неравновероятную выборку нельзя выдать за micro precision.")
    questions = policy.get("questions") or {}
    if set(questions) != {"topical_relevance", "evidence_role"}:
        raise ValueError("Набор вопросов retrieval-разметки изменён без новой версии.")
    for question in questions.values():
        choices = question.get("choices") or []
        if not choices or len(choices) != len(set(choices)):
            raise ValueError("У каждого вопроса нужны разные допустимые ответы.")
    hidden = set((policy.get("blinding") or {}).get("hidden_per_item") or [])
    required_hidden = {
        "source_number", "exact_matches", "expanded_matches",
        "expansion_only_matches", "newly_observed_by_expansion",
    }
    if not required_hidden <= hidden:
        raise ValueError("Пакет раскрывает результат retrieval-эксперимента.")
    comparison = policy.get("comparison") or {}
    if comparison.get("automatic_consensus") is not False:
        raise ValueError("Автоматический консенсус запрещён.")
    if comparison.get("precision_requires_adjudicated_binary_labels") is not True:
        raise ValueError("Precision нельзя считать до adjudication.")
    interpretation = policy.get("interpretation") or {}
    if any(interpretation.get(key) is not False for key in (
        "gold_standard", "calibration_allowed", "production_change_allowed",
        "weak_signal_quality_measured",
    )):
        raise ValueError("Retrieval-пакет не разрешает продуктовую калибровку.")


def load_source(policy: dict) -> dict:
    source = policy["source"]
    path = (ROOT / source["report_path"]).resolve()
    if file_sha256(path) != source["report_bytes_sha256"]:
        raise ValueError("Закреплённый expansion report изменился.")
    report = json.loads(path.read_text(encoding="utf-8"))
    expected = report.pop("report_payload_sha256", None)
    if expected != source["report_payload_sha256"] or digest(report) != expected:
        raise ValueError("Payload закреплённого expansion report изменился.")
    report["report_payload_sha256"] = expected
    return report


def _assignment_rows(report: dict) -> list[dict]:
    rows = []
    for result in report.get("results") or []:
        for sample in result.get("expansion_only_review_sample") or []:
            if sample.get("review_label") is not None:
                raise ValueError("Исходный expansion sample уже содержит метку.")
            rows.append({
                "source_number": result["source_number"],
                "target_topic": result["signal_original"],
                "arxiv_id": sample["arxiv_id"],
                "url": sample["url"],
                "title": sample["title"],
                "first_submission_date": sample["first_submission_date"],
                "categories": sample["categories"],
                "exact_matches": result["exact_matches"],
                "expanded_matches": result["expanded_matches"],
                "expansion_only_matches": result["expansion_only_matches"],
                "exact_observed": result["exact_observed"],
                "newly_observed_by_expansion": (
                    not result["exact_observed"] and result["expanded_observed"]
                ),
            })
    return sorted(rows, key=lambda row: (row["source_number"], row["arxiv_id"]))


def build_packet(policy: dict | None = None, report: dict | None = None) -> tuple[dict, dict, dict]:
    policy = copy.deepcopy(policy or load_policy())
    validate_policy(policy)
    report = copy.deepcopy(report or load_source(policy))
    rows = _assignment_rows(report)
    selection = policy["selection"]
    unique_documents = {row["arxiv_id"] for row in rows}
    signals = {row["source_number"] for row in rows}
    observed = (len(rows), len(unique_documents), len(signals))
    expected = (
        selection["expected_assignments"], selection["expected_unique_documents"],
        selection["expected_signals"],
    )
    if observed != expected:
        raise ValueError(f"Состав frozen retrieval sample изменился: {observed} != {expected}.")
    scope = {
        "version": PACKET_VERSION,
        "policy_sha256": digest(policy),
        "source_report_payload_sha256": report["report_payload_sha256"],
        "assignments": [f"{row['source_number']}:{row['arxiv_id']}" for row in rows],
    }
    package_id = str(uuid.uuid5(uuid.NAMESPACE_URL, digest(scope)))
    items, mapping = [], []
    for row in rows:
        item_id = "retrieval-item-" + hashlib.sha256(
            f"{package_id}\0{row['source_number']}\0{row['arxiv_id']}".encode()
        ).hexdigest()[:20]
        items.append({
            "item_id": item_id,
            "target_topic": row["target_topic"],
            "document": {
                "title": row["title"],
                "first_submission_date": row["first_submission_date"],
                "categories": row["categories"],
                "url": row["url"],
                "abstract_included": False,
                "abstract_omission_reason": policy["blinding"]["abstract_reason"],
            },
            "questions": copy.deepcopy(policy["questions"]),
            "evidence_boundary": (
                "Оценивается только релевантность статьи заданной теме. "
                "Это не оценка слабого сигнала, рынка или будущего успеха."
            ),
        })
        mapping.append({
            "item_id": item_id,
            "source_number": row["source_number"],
            "arxiv_id": row["arxiv_id"],
            "exact_matches": row["exact_matches"],
            "expanded_matches": row["expanded_matches"],
            "expansion_only_matches": row["expansion_only_matches"],
            "exact_observed": row["exact_observed"],
            "newly_observed_by_expansion": row["newly_observed_by_expansion"],
        })
    packet = {
        "version": PACKET_VERSION,
        "package_id": package_id,
        "purpose": policy["purpose"],
        "policy_version": policy["version"],
        "policy_sha256": digest(policy),
        "source_report_payload_sha256": report["report_payload_sha256"],
        "selection_summary": {
            "algorithm": selection["algorithm"],
            "assignments": len(items),
            "unique_documents": len(unique_documents),
            "signals": len(signals),
            "all_stored_samples_included": True,
            "uniform_over_documents": False,
            "micro_precision_supported": False,
        },
        "blinding": {
            "retrieval_counts_hidden": True,
            "baseline_observability_hidden": True,
            "expected_labels_present": False,
        },
        "reviewer_instructions": [
            "Откройте arXiv-ссылку и оцените статью относительно указанной темы.",
            "Не пытайтесь оценивать слабый сигнал, рынок или будущий успех.",
            "Если оснований недостаточно, выбирайте uncertain.",
            "Не открывайте private key до завершения индивидуальной анкеты.",
        ],
        "items": items,
        "gold_standard": False,
        "calibration_allowed": False,
        "production_change_allowed": False,
    }
    packet["packet_payload_sha256"] = digest(packet)
    key = {
        "version": PACKET_VERSION + "-private-key",
        "package_id": package_id,
        "packet_payload_sha256": packet["packet_payload_sha256"],
        "contains_expected_labels": False,
        "mapping": mapping,
    }
    key["key_payload_sha256"] = digest(key)
    template = {
        "version": SUBMISSION_VERSION,
        "package_id": package_id,
        "packet_payload_sha256": packet["packet_payload_sha256"],
        "reviewer_id": None,
        "independent_review_declared": None,
        "hidden_fields_not_seen_declared": None,
        "annotations": [
            {
                "item_id": item["item_id"],
                "answers": {name: None for name in policy["questions"]},
                "rationale": None,
                "sources": [],
            }
            for item in items
        ],
    }
    return packet, key, template


def _verified_packet(packet: dict) -> dict:
    value = copy.deepcopy(packet)
    expected = value.pop("packet_payload_sha256", None)
    if (
        value.get("version") not in {PACKET_VERSION, DYNAMIC_PACKET_VERSION}
        or not expected
        or digest(value) != expected
        or value.get("gold_standard") is not False
        or value.get("calibration_allowed") is not False
        or value.get("production_change_allowed") is not False
    ):
        raise ValueError("Retrieval-пакет повреждён или имеет неизвестную версию.")
    value["packet_payload_sha256"] = expected
    return value


def current_packet(path: Path = CURRENT_PACKET_PATH) -> dict:
    return _verified_packet(json.loads(path.read_text(encoding="utf-8")))


def current_template(path: Path = CURRENT_TEMPLATE_PATH) -> dict:
    packet = current_packet()
    value = json.loads(path.read_text(encoding="utf-8"))
    if (
        value.get("version") != SUBMISSION_VERSION
        or value.get("package_id") != packet["package_id"]
        or value.get("packet_payload_sha256") != packet["packet_payload_sha256"]
        or {row.get("item_id") for row in value.get("annotations") or []}
        != {item["item_id"] for item in packet["items"]}
    ):
        raise ValueError("Шаблон не совпадает с retrieval-пакетом.")
    return value


def _clean_sources(values: list, maximum: int) -> list[str]:
    if not isinstance(values, list) or len(values) > maximum:
        raise ValueError("Некорректное число источников рецензента.")
    clean = []
    for value in values:
        if not isinstance(value, str):
            raise ValueError("Источник рецензента должен быть HTTPS-ссылкой.")
        link = value.strip()
        parsed = urlsplit(link)
        if (
            len(link) > 2048 or parsed.scheme != "https" or not parsed.hostname
            or parsed.username or parsed.password or any(char.isspace() for char in link)
        ):
            raise ValueError("Источник должен быть безопасной HTTPS-ссылкой.")
        if link not in clean:
            clean.append(link)
    return clean


def validate_submission(packet: dict, submission: dict, policy: dict | None = None) -> dict:
    packet = _verified_packet(packet)
    policy = copy.deepcopy(policy or load_policy())
    validate_policy(policy)
    if (
        submission.get("version") != SUBMISSION_VERSION
        or submission.get("package_id") != packet["package_id"]
        or submission.get("packet_payload_sha256") != packet["packet_payload_sha256"]
    ):
        raise ValueError("Анкета относится к другому retrieval-пакету.")
    reviewer = submission.get("reviewer_id")
    if not isinstance(reviewer, str) or not 1 <= len(reviewer.strip()) <= 120:
        raise ValueError("Нужен идентификатор рецензента.")
    if submission.get("independent_review_declared") is not True:
        raise ValueError("Нужно подтвердить независимое заполнение.")
    if submission.get("hidden_fields_not_seen_declared") is not True:
        raise ValueError("Нужно подтвердить отсутствие доступа к скрытым полям.")
    expected_items = {item["item_id"] for item in packet["items"]}
    seen, rows = set(), []
    limits = policy["submission"]
    for annotation in submission.get("annotations") or []:
        item_id = annotation.get("item_id")
        if item_id not in expected_items or item_id in seen:
            raise ValueError("Неизвестный или повторный item_id.")
        seen.add(item_id)
        answers = annotation.get("answers") or {}
        if set(answers) != set(policy["questions"]):
            raise ValueError("Ответы должны покрывать точный набор вопросов.")
        for name, answer in answers.items():
            if answer not in policy["questions"][name]["choices"]:
                raise ValueError("Недопустимый ответ retrieval-анкеты.")
        rationale = annotation.get("rationale")
        if not isinstance(rationale, str):
            raise ValueError("К решению нужно объяснение.")
        rationale = rationale.strip()
        if not limits["rationale_min_length"] <= len(rationale) <= limits["rationale_max_length"]:
            raise ValueError("Объяснение выходит за границы политики.")
        rows.append({
            "item_id": item_id,
            "answers": answers,
            "rationale": rationale,
            "sources": _clean_sources(annotation.get("sources") or [], limits["max_sources"]),
        })
    missing = sorted(expected_items - seen)
    result = {
        "version": SUBMISSION_VERSION + "-validated",
        "package_id": packet["package_id"],
        "packet_payload_sha256": packet["packet_payload_sha256"],
        "reviewer_id": reviewer.strip(),
        "identity": "self_declared_not_authenticated",
        "independent_review_declared": True,
        "hidden_fields_not_seen_declared": True,
        "complete": not missing,
        "missing_item_ids": missing,
        "annotations": sorted(rows, key=lambda row: row["item_id"]),
        "individual_opinion_not_gold": True,
        "precision_available": False,
        "production_change_allowed": False,
        "interpretation": (
            "Даже полная анкета одного рецензента не является consensus, "
            "gold или измерением precision."
        ),
    }
    result["submission_payload_sha256"] = digest(result)
    return result


def verify_validated_submission(packet: dict, submission: dict) -> dict:
    packet = _verified_packet(packet)
    value = copy.deepcopy(submission)
    expected = value.pop("submission_payload_sha256", None)
    if (
        not expected or digest(value) != expected
        or value.get("version") != SUBMISSION_VERSION + "-validated"
        or value.get("package_id") != packet["package_id"]
        or value.get("packet_payload_sha256") != packet["packet_payload_sha256"]
        or value.get("complete") is not True
        or value.get("individual_opinion_not_gold") is not True
        or value.get("precision_available") is not False
        or value.get("production_change_allowed") is not False
    ):
        raise ValueError("Проверенная retrieval-анкета повреждена.")
    value["submission_payload_sha256"] = expected
    return value


def _compare_validated(packet: dict, a: dict, b: dict, policy: dict) -> dict:
    if not a["complete"] or not b["complete"]:
        raise ValueError("Для сравнения нужны две полные анкеты.")
    if a["reviewer_id"].casefold() == b["reviewer_id"].casefold():
        raise ValueError("Нужны два разных рецензента.")
    by_a = {row["item_id"]: row for row in a["annotations"]}
    by_b = {row["item_id"]: row for row in b["annotations"]}
    axes, disagreements = {}, []
    for name in policy["questions"]:
        pairs = Counter(
            (by_a[item]["answers"][name], by_b[item]["answers"][name])
            for item in sorted(by_a)
        )
        agreed = sum(count for (x, y), count in pairs.items() if x == y)
        axes[name] = {
            "items": len(by_a),
            "agreements": agreed,
            "raw_agreement": agreed / len(by_a),
            "answer_pairs": {
                f"{x} | {y}": count for (x, y), count in sorted(pairs.items())
            },
        }
        disagreements.extend({
            "item_id": item,
            "axis": name,
            "left": by_a[item]["answers"][name],
            "right": by_b[item]["answers"][name],
        } for item in sorted(by_a) if by_a[item]["answers"][name] != by_b[item]["answers"][name])
    result = {
        "version": COMPARISON_VERSION,
        "package_id": packet["package_id"],
        "packet_payload_sha256": packet["packet_payload_sha256"],
        "reviewers": [a["reviewer_id"], b["reviewer_id"]],
        "submission_sha256": [a["submission_payload_sha256"], b["submission_payload_sha256"]],
        "axes": axes,
        "disagreements": disagreements,
        "consensus_created": False,
        "adjudication_required": bool(disagreements),
        "precision_available": False,
        "production_change_allowed": False,
        "interpretation": (
            "Сырая согласованность не является precision. Разногласия и uncertain "
            "требуют независимой adjudication."
        ),
    }
    result["comparison_payload_sha256"] = digest(result)
    return result


def compare_submissions(
    packet: dict, left: dict, right: dict, policy: dict | None = None,
) -> dict:
    packet = _verified_packet(packet)
    policy = copy.deepcopy(policy or load_policy())
    a = validate_submission(packet, left, policy)
    b = validate_submission(packet, right, policy)
    return _compare_validated(packet, a, b, policy)


def compare_validated_submissions(
    packet: dict, left: dict, right: dict, policy: dict | None = None,
) -> dict:
    packet = _verified_packet(packet)
    policy = copy.deepcopy(policy or load_policy())
    validate_policy(policy)
    a = verify_validated_submission(packet, left)
    b = verify_validated_submission(packet, right)
    return _compare_validated(packet, a, b, policy)


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_new(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--policy", type=Path, default=POLICY_PATH)
    build.add_argument("--packet", type=Path, required=True)
    build.add_argument("--private-key", type=Path, required=True)
    build.add_argument("--template", type=Path, required=True)
    check = sub.add_parser("validate")
    check.add_argument("--packet", type=Path, required=True)
    check.add_argument("--submission", type=Path, required=True)
    check.add_argument("--output", type=Path, required=True)
    compare = sub.add_parser("compare")
    compare.add_argument("--packet", type=Path, required=True)
    compare.add_argument("--left", type=Path, required=True)
    compare.add_argument("--right", type=Path, required=True)
    compare.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "build":
        policy = load_policy(args.policy)
        packet, key, template = build_packet(policy)
        for path, payload in (
            (args.packet, packet), (args.private_key, key), (args.template, template)
        ):
            _write_new(path, payload)
        print(json.dumps({
            "package_id": packet["package_id"],
            "packet_payload_sha256": packet["packet_payload_sha256"],
            "items": len(packet["items"]),
        }, ensure_ascii=False))
    elif args.command == "validate":
        result = validate_submission(_read(args.packet), _read(args.submission))
        _write_new(args.output, result)
        print(json.dumps({
            "complete": result["complete"],
            "submission_payload_sha256": result["submission_payload_sha256"],
        }, ensure_ascii=False))
    else:
        result = compare_submissions(
            _read(args.packet), _read(args.left), _read(args.right)
        )
        _write_new(args.output, result)
        print(json.dumps({
            "disagreements": len(result["disagreements"]),
            "comparison_payload_sha256": result["comparison_payload_sha256"],
        }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
