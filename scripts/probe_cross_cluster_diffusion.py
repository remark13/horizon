"""Read-only frozen database export and cross-topic phrase regression.

Writes new file artifacts only. Known control IDs are loaded for evaluation
AFTER candidate generation, not for retrieval, extraction or thresholds.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import time

from saia.cross_cluster_diffusion import DiffusionPolicy, find_phrase_candidates, lineage_families


ROOT = Path(__file__).resolve().parents[1]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_frozen_input(cfg: dict) -> dict:
    from saia import db  # Offline replay needs only the Python standard library.
    start = date.fromisoformat(cfg["date_from"])
    end = date.fromisoformat(cfg["as_of_date_exclusive"])
    if start >= end or start.month != 1 or start.day != 1 or end.month != 1 or end.day != 1:
        raise ValueError("Complete year boundaries required by this regression adapter")
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        cur.execute("SET LOCAL statement_timeout = '60s'")
        cur.execute("SELECT mission_id,kind,status,upstream_run_id,as_of_date,code_version,notes "
                    "FROM analysis_run WHERE run_id=%s", (cfg["cluster_run_id"],))
        run = cur.fetchone()
        if (not run or run[:4] != (cfg["mission_id"], "cluster", "done", cfg["normalize_run_id"])
                or run[4] != end):
            raise ValueError("Wrong or incomplete frozen cluster run")
        cur.execute("SELECT topic_id,label FROM topic WHERE run_id=%s ORDER BY topic_id",
                    (cfg["cluster_run_id"],))
        topics = {str(identifier): label for identifier, label in cur.fetchall()}
        if len(topics) != cfg["expected_topics"]:
            raise ValueError("Frozen topic count changed")
        cur.execute("SELECT tm.work_id,tm.topic_id,tm.window_key FROM topic_membership tm "
                    "JOIN topic t USING(topic_id) WHERE t.run_id=%s ORDER BY tm.work_id,tm.topic_id,tm.window_key",
                    (cfg["cluster_run_id"],))
        membership_rows = cur.fetchall()
        memberships = {}
        for identifier, topic, window in membership_rows:
            memberships.setdefault(identifier, []).append((str(topic), window))
        cur.execute("SELECT l.parent_topic,l.child_topic FROM topic_lineage l "
                    "JOIN topic c ON c.topic_id=l.child_topic "
                    "JOIN topic p ON p.topic_id=l.parent_topic "
                    "WHERE c.run_id=%s AND p.run_id=%s ORDER BY l.parent_topic,l.child_topic",
                    (cfg["cluster_run_id"], cfg["cluster_run_id"]))
        edges = [(str(left), str(right)) for left, right in cur.fetchall()]
        cur.execute("SELECT q.decision,count(*) FROM work_quality q JOIN work w USING(work_id) "
                    "WHERE w.run_id=%s AND q.policy_version=%s GROUP BY q.decision ORDER BY q.decision",
                    (cfg["normalize_run_id"], cfg["quality_policy_version"]))
        quality_counts = dict(cur.fetchall())
        if quality_counts.get("include") != cfg["expected_quality_include_works"]:
            raise ValueError("Frozen include-policy count changed")
        cur.execute("SELECT w.work_id,w.effective_date,w.canonical_title,coalesce(w.abstract,'') "
                    "FROM work w JOIN work_quality q USING(work_id) "
                    "WHERE w.run_id=%s AND q.policy_version=%s AND q.decision='include' "
                    "AND w.effective_date >= %s AND w.effective_date < %s ORDER BY w.work_id",
                    (cfg["normalize_run_id"], cfg["quality_policy_version"], start, end))
        works = cur.fetchall()
        if len(works) != cfg["expected_eligible_works"] or len(works) > cfg["max_input_works"]:
            raise ValueError("Frozen eligible work count changed or resource cap exceeded")
        if quality_counts["include"] - len(works) != cfg["expected_quality_include_outside_period"]:
            raise ValueError("Out-of-period include count changed")
        cur.execute("SELECT i.work_id,i.kind,i.value FROM identifier i JOIN work w USING(work_id) "
                    "WHERE w.run_id=%s ORDER BY i.work_id,i.kind,i.value", (cfg["normalize_run_id"],))
        identifiers = {}
        for identifier, kind, value in cur.fetchall():
            identifiers.setdefault(identifier, []).append({"kind": kind, "value": value})
    documents = []
    for identifier, day, title, abstract in works:
        window = str(day.year)
        assigned = memberships.get(identifier, [])
        if any(member_window != window for _, member_window in assigned):
            raise ValueError("Membership window differs from frozen publication window")
        documents.append({"id": str(identifier), "date": day.isoformat(), "window": window,
                          "title": title, "abstract": abstract,
                          "areas": sorted({topic for topic, _ in assigned}),
                          "identifiers": identifiers.get(identifier, [])})
    included_ids = {row["id"] for row in documents}
    if any(str(identifier) not in included_ids for identifier in memberships):
        raise ValueError("Saved membership includes a noneligible/out-of-period work")
    families = lineage_families(set(topics), edges)
    body = {"version": "cross-cluster-frozen-regression-input-v1", "mission_id": cfg["mission_id"],
            "normalize_run_id": cfg["normalize_run_id"], "cluster_run_id": cfg["cluster_run_id"],
            "quality_policy_version": cfg["quality_policy_version"],
            "quality_decisions": quality_counts, "cluster_code_version": run[5],
            "quality_include_outside_period": quality_counts["include"] - len(works),
            "cluster_notes": run[6], "topics": topics, "lineage_edges": edges,
            "lineage_families": families, "documents": documents,
            "windows": [str(year) for year in range(start.year, end.year)],
            "date_from": start.isoformat(), "as_of_date_exclusive": end.isoformat()}
    body["payload_sha256"] = hashlib.sha256(json.dumps(
        body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return body


def evaluate_after_generation(result: dict, payload: dict, acceptance: dict) -> dict:
    # Deliberately called only after full proposals have been generated.
    mission = json.loads((ROOT / "missions" / f"{payload['mission_id']}.json").read_text())
    control_ids = set(mission["benchmark"]["target_arxiv_ids"])
    controls = {row["id"]: next(item["value"] for item in row["identifiers"]
                              if item["kind"] == "arxiv" and item["value"] in control_ids)
                for row in payload["documents"] if any(
                    item["kind"] == "arxiv" and item["value"] in control_ids
                    for item in row["identifiers"])}
    hits = []
    for rank, row in enumerate(result["rows"], 1):
        found = sorted(controls[e["work_id"]] for e in row["evidence"] if e["work_id"] in controls)
        if found:
            hits.append({"rank": rank, "phrase": row["phrase"], "control_arxiv_ids": found,
                         "control_works": len(found), "candidate_works": len(row["evidence"]),
                         "control_fraction_of_eligible": len(found) / len(controls),
                         "control_fraction_in_candidate": len(found) / len(row["evidence"]),
                         "recent_active_areas": row["recent_active_areas"]})
    passed = any(row["rank"] <= acceptance["maximum_review_rank"]
                 and row["control_works"] >= acceptance["minimum_eligible_controls_in_one_proposal"]
                 and row["control_fraction_of_eligible"] >= acceptance["minimum_eligible_control_fraction"]
                 for row in hits)
    # Preserve the original mission's stricter narrow-line composition gate.
    # A broad CNN phrase containing two controls must not count as GCN detection.
    min_target_share = mission["benchmark"]["acceptance"]["min_target_share_in_dominant_topic"]
    distinct_passed = any(row["rank"] <= acceptance["maximum_review_rank"]
                          and row["control_works"] >= acceptance["minimum_eligible_controls_in_one_proposal"]
                          and row["control_fraction_of_eligible"] >= acceptance["minimum_eligible_control_fraction"]
                          and row["control_fraction_in_candidate"] >= min_target_share
                          for row in hits)
    return {"known_failure_regression_only": True, "eligible_control_works": len(controls),
            "control_hits_in_stored_proposals": hits,
            "predeclared_control_recovery_gate_passed": passed,
            "original_mission_minimum_target_share": min_target_share,
            "original_mission_distinct_target_gate_passed": distinct_passed,
            "top15_weak_signal_precision": None,
            "limitations": ["Recovery alone does not establish a coherent line or acceptable noise.",
                            "Known GCN failure is development evidence, not a hidden test."]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--input", type=Path, help="Replay an already exported frozen input, no database")
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError(args.output_dir)
    cfg = json.loads(args.config.read_text(encoding="utf-8"))
    if (cfg.get("version") != "cross-cluster-diffusion-regression-protocol-v1"
            or cfg.get("production_use") is not False):
        raise ValueError("Invalid frozen diagnostic protocol")
    policy = DiffusionPolicy(**cfg["policy"])
    policy.validate()
    parent = args.output_dir.parent
    if not parent.is_dir() or shutil.disk_usage(parent).free < cfg["min_free_disk_reserve_bytes"]:
        raise ValueError("Missing parent directory or insufficient disk reserve")
    started = time.monotonic()
    payload = (json.loads(args.input.read_text(encoding="utf-8")) if args.input
               else load_frozen_input(cfg))
    body = {key: value for key, value in payload.items() if key != "payload_sha256"}
    if hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":")).encode()).hexdigest() != payload["payload_sha256"]:
        raise ValueError("Frozen input checksum mismatch")
    if (payload["mission_id"] != cfg["mission_id"]
            or payload["cluster_run_id"] != cfg["cluster_run_id"]
            or payload["normalize_run_id"] != cfg["normalize_run_id"]
            or payload["quality_policy_version"] != cfg["quality_policy_version"]
            or payload["date_from"] != cfg["date_from"]
            or payload["as_of_date_exclusive"] != cfg["as_of_date_exclusive"]
            or len(payload["documents"]) != cfg["expected_eligible_works"]
            or len(payload["documents"]) > cfg["max_input_works"]
            or len(payload["topics"]) != cfg["expected_topics"]):
        raise ValueError("Replay input is not the frozen protocol corpus")
    if (payload["quality_decisions"].get("include") != cfg["expected_quality_include_works"]
            or payload.get("quality_include_outside_period") != cfg["expected_quality_include_outside_period"]):
        raise ValueError("Replay quality/period accounting differs from the protocol")
    result = find_phrase_candidates(payload["documents"], payload["windows"], policy=policy,
                                    coverage_comparable=cfg["coverage_comparable"])
    family_result = find_phrase_candidates(payload["documents"], payload["windows"], policy=policy,
                                           area_families=payload["lineage_families"],
                                           coverage_comparable=cfg["coverage_comparable"])
    # Same call repeated before controls are read; stable ranking/composition.
    repeated = find_phrase_candidates(payload["documents"], payload["windows"], policy=policy,
                                      coverage_comparable=cfg["coverage_comparable"])
    if result != repeated:
        raise ValueError("Deterministic repeat failed")
    by_id = {row["id"]: row for row in payload["documents"]}
    for generated in (result, family_result):
        for row in generated["rows"]:
            for evidence in row["evidence"]:
                source = by_id[evidence["work_id"]]
                evidence["date"] = source["date"]
                evidence["source_urls"] = [
                    "https://arxiv.org/abs/" + item["value"] if item["kind"] == "arxiv"
                    else "https://doi.org/" + item["value"] if item["kind"] == "doi"
                    else "https://openalex.org/" + item["value"].upper()
                    for item in source["identifiers"] if item["kind"] in {"arxiv", "doi", "openalex"}]
    summary = {"version": cfg["version"], "created_at": datetime.now(timezone.utc).isoformat(),
               "config_sha256": sha256_file(args.config), "frozen_input_sha256": payload["payload_sha256"],
               "source_code_sha256": sha256_file(ROOT / "saia" / "cross_cluster_diffusion.py"),
               "policy": asdict(policy), "unique_works": len(payload["documents"]),
               "topics": len(payload["topics"]),
               "lineage_families": len(set(payload["lineage_families"].values())),
               "family_size_distribution": dict(Counter(Counter(payload["lineage_families"].values()).values())),
               "unassigned_works": result["unassigned_works"],
               "raw_topic_proposals": result["candidate_compositions"],
               "family_proposals": family_result["candidate_compositions"],
               "deterministic_repeat": True, "coverage_comparable": cfg["coverage_comparable"],
               "raw_topic_control_evaluation": evaluate_after_generation(result, payload, cfg["acceptance"]),
               "family_control_evaluation": evaluate_after_generation(family_result, payload, cfg["acceptance"]),
               "elapsed_seconds": round(time.monotonic() - started, 3),
               "production_changed": False, "weak_signal_accuracy_measured": False,
               "limitations": cfg["limitations"]}
    outputs = {"raw-topic-proposals.json": result,
               "lineage-family-proposals.json": family_result, "summary.json": summary}
    if args.input:
        resolved = args.input.resolve()
        reference = str(resolved.relative_to(ROOT)) if resolved.is_relative_to(ROOT) else str(resolved)
        outputs["input-reference.json"] = {"path": reference,
            "bytes": args.input.stat().st_size, "file_sha256": sha256_file(args.input),
            "payload_sha256": payload["payload_sha256"], "included_in_this_output": False}
    else:
        outputs["input.json"] = payload
    serialized = {name: json.dumps(value, ensure_ascii=False, sort_keys=True,
                                   indent=2) + "\n" for name, value in outputs.items()}
    if sum(len(value.encode()) for value in serialized.values()) > cfg["max_output_bytes"]:
        raise ValueError("Output byte cap exceeded")
    args.output_dir.mkdir()
    for name, value in serialized.items():
        with (args.output_dir / name).open("x", encoding="utf-8") as stream:
            stream.write(value)
    manifest = {"version": "cross-cluster-diffusion-artifacts-v1", "production_use": False,
                "files": [{"name": name, "bytes": (args.output_dir / name).stat().st_size,
                           "sha256": sha256_file(args.output_dir / name)} for name in sorted(outputs)]}
    with (args.output_dir / "manifest.json").open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
