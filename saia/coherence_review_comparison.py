"""Compare two exported 30-pair reviews; no automatic gold or scout gating."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from saia.coherence_review import ANSWERS, QUESTIONS, VERSION, read_packet, validate_submission
from saia.cross_domain_relevance_review import _agreement


COMPARISON_VERSION = "bas-coherence-review-comparison-v1"


def _verified_export(path: Path) -> dict:
    submission = json.loads(path.read_text(encoding="utf-8"))
    body = {key: value for key, value in submission.items() if key != "submission_sha256"}
    digest = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")).encode()).hexdigest()
    if submission.get("version") != VERSION or submission.get("submission_sha256") != digest:
        raise ValueError("Review export version or checksum mismatch")
    # Recheck completeness, source quotes and frozen packet identity. The newly
    # generated validation timestamp is not evidence of reviewer independence.
    clean = validate_submission(submission)
    return {"reviewer_id": clean["reviewer_id"], "packet_sha256": clean["packet_sha256"],
            "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "export_sha256": submission["submission_sha256"],
            "answers": {row["case_id"]: row for row in clean["answers"]}}


def compare_exports(left_path: Path, right_path: Path) -> dict:
    if left_path.resolve() == right_path.resolve():
        raise ValueError("Two distinct review files required")
    left, right = _verified_export(left_path), _verified_export(right_path)
    if left["reviewer_id"].casefold() == right["reviewer_id"].casefold():
        raise ValueError("Two distinct reviewer identifiers required")
    packet = read_packet()
    ids = [row["case_id"] for row in packet["cases"]]
    agreements = {}
    by_case = []
    for question in QUESTIONS:
        left_labels = [left["answers"][identifier][question] for identifier in ids]
        right_labels = [right["answers"][identifier][question] for identifier in ids]
        metric = _agreement(left_labels, right_labels, set(ANSWERS))
        matrix = Counter(zip(left_labels, right_labels))
        metric["confusion_matrix"] = {a: {b: matrix[(a, b)] for b in sorted(ANSWERS)}
                                      for a in sorted(ANSWERS)}
        metric["reviewer_answer_distributions"] = {
            left["reviewer_id"]: dict(sorted(Counter(left_labels).items())),
            right["reviewer_id"]: dict(sorted(Counter(right_labels).items()))}
        metric["unclear_fraction_by_reviewer"] = {
            left["reviewer_id"]: left_labels.count("unclear") / len(ids),
            right["reviewer_id"]: right_labels.count("unclear") / len(ids)}
        agreements[question] = metric
    for identifier in ids:
        disagreements = [q for q in QUESTIONS if left["answers"][identifier][q]
                         != right["answers"][identifier][q]]
        if disagreements:
            by_case.append({"case_id": identifier, "disagreement_questions": disagreements,
                            "reviewer_answers": {review["reviewer_id"]: review["answers"][identifier]
                                                 for review in (left, right)}})
    return {"version": COMPARISON_VERSION, "packet_sha256": packet["packet_sha256"],
            "items": len(ids),
            "reviewers": [{key: row[key] for key in
                           ("reviewer_id", "file_sha256", "export_sha256")} for row in (left, right)],
            "agreement_by_question": agreements, "disagreements_for_adjudication": by_case,
            "problem_vs_line_answer_differences": {
                review["reviewer_id"]: sum(review["answers"][identifier]["same_research_problem"]
                                           != review["answers"][identifier]["one_signal_line"] for identifier in ids)
                for review in (left, right)},
            "automatic_consensus": None, "weak_signal_precision": None,
            "production_changed": False,
            "limitations": [
                "Distinct names and declared independence do not prove independent human expertise.",
                "Low kappa can reflect class imbalance or insufficient evidence, not just bad questions.",
                "Thirty pairs in two BAS cards do not evaluate Top-15 or other technology directions.",
                "Agreement is not accuracy against independently adjudicated gold.",
                "Runtime scout results do not require completion of this development review.",
            ]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--left", type=Path, required=True)
    parser.add_argument("--right", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = compare_exports(args.left, args.right)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")
    print(json.dumps({"items": result["items"],
                      "disagreement_pairs": len(result["disagreements_for_adjudication"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
