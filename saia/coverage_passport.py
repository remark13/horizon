"""Bind an audited parent-corpus comparison to an immutable collection batch."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from psycopg.types.json import Jsonb

from saia import db
from saia.ingest import collection_content_hash
from saia.package_observations import payload_hash

POLICY = "collection-coverage-passport-0.4.9"
GUARDED_INDEX_POLICY = "collection-coverage-passport-guarded-index-v1"
SCOPE = "within_frozen_snapshot_same_query_window"
EVIDENCE_KEY = "within_frozen_snapshot_same_query_window_comparable"


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(package: Path, evidence_path: Path) -> tuple[dict, dict, dict]:
    package, evidence_path = package.resolve(), evidence_path.resolve()
    manifest_path, mission_path = package / "manifest.json", package / "mission.json"
    manifest_bytes, mission_bytes = manifest_path.read_bytes(), mission_path.read_bytes()
    manifest, mission = json.loads(manifest_bytes), json.loads(mission_bytes)
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    claimed = evidence.get("report_payload_sha256")
    without_hash = {key: value for key, value in evidence.items() if key != "report_payload_sha256"}
    if claimed != payload_hash(without_hash):
        raise ValueError("Parent corpus report payload hash mismatch")
    source = evidence.get("input") or {}
    selected_manifest_sha = source.get("selected_package_manifest_bytes_sha256")
    direct_binding = selected_manifest_sha == hashlib.sha256(manifest_bytes).hexdigest()
    derived_binding = (
        manifest.get("input_base_manifest_sha256") == selected_manifest_sha
        and (mission.get("base_package") or {}).get("manifest_sha256") == selected_manifest_sha
        and (mission.get("base_package") or {}).get("mission_sha256") == source.get("mission_bytes_sha256")
        and (mission.get("base_package") or {}).get("mission_id") == evidence.get("mission_id")
        and (manifest.get("sources", {}).get("arxiv") or {}).get(
            "reused_from_base_manifest_sha256"
        ) == selected_manifest_sha
        and (manifest.get("sources", {}).get("openalex") or {}).get("independent_discovery") is False
    )
    if not (direct_binding or derived_binding):
        raise ValueError("Parent report is not bound to this package or its exact enriched derivative")
    if direct_binding and source.get("mission_bytes_sha256") != hashlib.sha256(mission_bytes).hexdigest():
        raise ValueError("Parent report is not bound to this mission snapshot")
    if mission.get("mission_id") != manifest.get("mission_id"):
        raise ValueError("Mission identifiers differ")
    if direct_binding and evidence.get("mission_id") != manifest.get("mission_id"):
        raise ValueError("Evidence mission differs from direct package")
    if (evidence.get("as_of_date") != mission.get("as_of_date")
            or evidence.get("period_from") != mission.get("period", {}).get("from")
            or evidence.get("period_end_exclusive") != mission.get("as_of_date")):
        raise ValueError("Parent report period differs from the frozen mission")
    coverage = evidence.get("coverage") or {}
    if evidence.get("version") == "arxiv-parent-corpus-audit-guarded-index-v1":
        required = (
            coverage.get("inventory_traversal_method") ==
            "guarded_index_built_from_complete_pinned_mirror"
            and coverage.get("same_source_index_checksum_verified") is True
            and coverage.get("selected_work_predicate_and_metadata_rechecked") is True
            and all(isinstance(source.get(key), str) and len(source[key]) == 64
                    for key in ("index_manifest_sha256", "index_file_sha256",
                                "duplicate_guard_sha256"))
        )
        if not required:
            raise ValueError("Guarded-index parent provenance is incomplete")
    if coverage.get("inventory_traversal_complete") is not True:
        raise ValueError("Full frozen inventory traversal was not established")
    if coverage.get(EVIDENCE_KEY) is not True:
        raise ValueError("Within-snapshot temporal comparability was not established")
    if evidence.get("accuracy_evaluated") is not False or evidence.get("confirmed_weak_signals") is not None:
        raise ValueError("Coverage evidence must not assert detector accuracy or confirmed signals")
    return manifest, mission, evidence


def resolve(cur, batch_id: int | None, base_coverage: dict | None = None) -> tuple[bool | None, int | None]:
    base = (base_coverage or {}).get("temporal_comparable")
    if batch_id is None:
        return base, None
    cur.execute(
        "SELECT coverage_passport_id,temporal_comparable FROM collection_coverage_passport "
        "WHERE batch_id=%s AND comparison_scope=%s",
        (batch_id, SCOPE),
    )
    row = cur.fetchone()
    return (row[1], row[0]) if row else (base, None)


def summarize_evidence(evidence: dict, coverage_passport_id: int) -> dict:
    """Return the parent-field backdrop without exposing a very large passport."""
    descriptive = evidence.get("full_period_descriptive") or {}
    publication = evidence.get("publication_series") or {}
    observed = publication.get("observed_sample") or {}
    return {
        "coverage_passport_id": coverage_passport_id,
        "period_from": evidence.get("period_from"),
        "period_end_exclusive": evidence.get("period_end_exclusive"),
        "parent_scope": (evidence.get("coverage") or {}).get("unit"),
        "counts": evidence.get("counts") or {},
        "full_period": {
            "months": descriptive.get("window_count"),
            "phrase_count_slope_per_month": descriptive.get("phrase_count_slope_per_month"),
            "parent_count_slope_per_month": descriptive.get("parent_count_slope_per_month"),
            "phrase_share_slope_per_month": descriptive.get("share_slope_per_month"),
            "phrase_share_first_to_last_change": descriptive.get("share_first_to_last_change"),
        },
        "recent_window": {
            "phrase_count_direction": (observed.get("count") or {}).get("direction"),
            "phrase_count_change": (observed.get("count") or {}).get("change"),
            "phrase_share_direction": (observed.get("share") or {}).get("direction"),
            "phrase_share_change": (observed.get("share") or {}).get("change"),
        },
        "interpretation": (
            "Фон родительского arXiv-корпуса нужен для проверки общего роста поля. "
            "Рост отдельной микротемы внутри выбранного среза не означает, что весь "
            "срез растёт быстрее родительской области."
        ),
        "limitations": evidence.get("limitations") or [],
    }


def read_summary(coverage_passport_id: int) -> dict:
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT evidence_payload,evidence_content_sha256 FROM collection_coverage_passport "
            "WHERE coverage_passport_id=%s", (coverage_passport_id,),
        )
        row = cur.fetchone()
    if not row:
        raise ValueError("Паспорт покрытия не найден.")
    evidence, expected_hash = row
    claimed_hash = evidence.get("report_payload_sha256")
    without_hash = {key: value for key, value in evidence.items()
                    if key != "report_payload_sha256"}
    if claimed_hash != expected_hash or payload_hash(without_hash) != expected_hash:
        raise ValueError("Хеш сохранённого паспорта покрытия не совпадает.")
    return summarize_evidence(evidence, coverage_passport_id)


def register(package: Path, evidence_path: Path) -> dict:
    manifest, _mission, evidence = verify(package, evidence_path)
    content_hash = collection_content_hash(manifest)
    evidence_file_sha = file_hash(evidence_path)
    evidence_content_sha = evidence["report_payload_sha256"]
    policy = (GUARDED_INDEX_POLICY if evidence.get("version") ==
              "arxiv-parent-corpus-audit-guarded-index-v1" else POLICY)
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT batch_id FROM collection_batch WHERE mission_id=%s AND query_version_id=%s "
            "AND content_sha256=%s AND seal_status='sealed'",
            (manifest["mission_id"], manifest["query_version"], content_hash),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError("Exact sealed collection batch is not present")
        batch_id = row[0]
        cur.execute(
            "SELECT coverage_passport_id,temporal_comparable,policy_version,evidence_content_sha256,evidence_file_sha256 "
            "FROM collection_coverage_passport WHERE batch_id=%s AND comparison_scope=%s",
            (batch_id, SCOPE),
        )
        existing = cur.fetchone()
        expected = (True, policy, evidence_content_sha, evidence_file_sha)
        if existing:
            if existing[1:] != expected:
                raise ValueError("A different immutable coverage passport already exists")
            return {"coverage_passport_id": existing[0], "batch_id": batch_id,
                    "temporal_comparable": True, "reused": True}
        cur.execute(
            "INSERT INTO collection_coverage_passport "
            "(batch_id,comparison_scope,temporal_comparable,policy_version,evidence_payload,"
            "evidence_content_sha256,evidence_file_sha256) VALUES (%s,%s,true,%s,%s,%s,%s) "
            "RETURNING coverage_passport_id",
            (batch_id, SCOPE, policy, Jsonb(evidence), evidence_content_sha, evidence_file_sha),
        )
        passport_id = cur.fetchone()[0]
        conn.commit()
    return {"coverage_passport_id": passport_id, "batch_id": batch_id,
            "temporal_comparable": True, "reused": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("evidence", type=Path)
    args = parser.parse_args(argv)
    print(json.dumps(register(args.package, args.evidence), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
