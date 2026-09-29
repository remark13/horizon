"""Import a sealed file-only embedding generation into PostgreSQL."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import pyarrow.parquet as pq
from psycopg.types.json import Jsonb

from saia import db
from saia.embed import build_text
from saia.package_observations import payload_hash


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_manifest(generation_dir: Path, observation_report: Path) -> tuple[dict, dict, object]:
    manifest_path = generation_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    claimed = manifest.get("manifest_payload_sha256")
    without_hash = {key: value for key, value in manifest.items() if key != "manifest_payload_sha256"}
    if claimed != payload_hash(without_hash):
        raise ValueError("Embedding manifest payload hash mismatch")
    artifact = manifest.get("artifact") or {}
    parquet_path = generation_dir / str(artifact.get("file") or "")
    if not parquet_path.is_file() or not parquet_path.resolve().is_relative_to(generation_dir.resolve()):
        raise ValueError("Embedding artifact must be inside generation directory")
    if sha256(parquet_path) != artifact.get("bytes_sha256"):
        raise ValueError("Embedding parquet hash mismatch")

    report_bytes_hash = sha256(observation_report)
    report = json.loads(observation_report.read_text(encoding="utf-8"))
    report_claim = report.get("report_payload_sha256")
    report_without_hash = {key: value for key, value in report.items() if key != "report_payload_sha256"}
    if report_claim != payload_hash(report_without_hash):
        raise ValueError("Observation report payload hash mismatch")
    source = manifest.get("input") or {}
    if report_bytes_hash != source.get("observation_report_bytes_sha256"):
        raise ValueError("Observation report bytes differ from embedding input")
    if report_claim != source.get("observation_report_payload_sha256"):
        raise ValueError("Observation report payload differs from embedding input")
    if manifest.get("status") != "passed" or manifest.get("accuracy_evaluated") is not False:
        raise ValueError("Only a passed technical generation with explicit unevaluated accuracy is importable")
    table = pq.read_table(parquet_path)
    if table.num_rows != artifact.get("rows") or table.num_rows != source.get("selected_count"):
        raise ValueError("Embedding row count mismatch")
    return manifest, report, table


def _selected_report_rows(report: dict) -> dict[str, dict]:
    selected = {
        row["source_record_id"]: row
        for row in report.get("records", [])
        if row.get("quality", {}).get("decision") == "include"
        and isinstance(row.get("title"), str) and row["title"].strip()
        and isinstance(row.get("abstract"), str) and row["abstract"].strip()
    }
    if len(selected) != report.get("counts", {}).get("quality_include_texts"):
        raise ValueError("Observation report include population mismatch")
    return selected


def import_generation(generation_dir: Path, observation_report: Path,
                      quality_generation_id: int) -> dict:
    if type(quality_generation_id) is not int or quality_generation_id < 1:
        raise ValueError("quality_generation_id must be a positive integer")
    generation_dir, observation_report = generation_dir.resolve(), observation_report.resolve()
    manifest, report, table = verify_manifest(generation_dir, observation_report)
    selected = _selected_report_rows(report)
    ids = table["source_record_id"].to_pylist()
    payload_hashes = table["input_payload_sha256"].to_pylist()
    vectors = table["embedding"].to_pylist()
    if len(ids) != len(set(ids)) or set(ids) != set(selected):
        raise ValueError("Embedding IDs differ from the report include population")
    for identifier, checksum, vector in zip(ids, payload_hashes, vectors):
        if checksum != selected[identifier]["payload_sha256"]:
            raise ValueError(f"Input payload hash mismatch for {identifier}")
        if (len(vector) != manifest["model"]["dimension"]
                or any(isinstance(value, bool) or not math.isfinite(value) for value in vector)
                or not any(value != 0 for value in vector)):
            raise ValueError(f"Invalid vector for {identifier}")

    mission_id = manifest["input"]["mission_id"]
    model = manifest["model"]["name"]
    dimension = manifest["model"]["dimension"]
    parquet_hash = manifest["artifact"]["bytes_sha256"]
    report_hash = manifest["input"]["observation_report_bytes_sha256"]
    manifest_hash = manifest["manifest_payload_sha256"]
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT q.normalize_run_id FROM quality_generation q JOIN analysis_run r "
            "ON r.run_id=q.normalize_run_id WHERE q.generation_id=%s AND q.status='done' "
            "AND r.status='done' AND r.kind='normalize' AND r.mission_id=%s",
            (quality_generation_id, mission_id),
        )
        found = cur.fetchone()
        if not found:
            raise ValueError("Quality generation is not a completed generation for this mission")
        normalize_run_id = found[0]
        cur.execute(
            "SELECT g.embedding_generation_id,g.quality_generation_id,g.row_count,"
            "g.observation_report_sha256,g.vectors_file_sha256,g.vectors_manifest_payload_sha256 "
            "FROM embedding_import_generation g WHERE g.normalize_run_id=%s AND g.model=%s",
            (normalize_run_id, model),
        )
        existing_generation = cur.fetchone()
        expected_generation = (
            quality_generation_id, len(ids), report_hash, parquet_hash, manifest_hash
        )
        if existing_generation:
            if existing_generation[1:] != expected_generation:
                raise ValueError("A different immutable file generation already exists for this run/model")
            cur.execute(
                "SELECT count(*) FROM embedding_import_member WHERE embedding_generation_id=%s",
                (existing_generation[0],),
            )
            if cur.fetchone()[0] != len(ids):
                raise ValueError("Existing embedding generation membership is incomplete")
            return {
                "embedding_generation_id": existing_generation[0],
                "normalize_run_id": normalize_run_id,
                "quality_generation_id": quality_generation_id,
                "model": model,
                "rows": len(ids),
                "reused": True,
            }

        cur.execute(
            "SELECT count(*) FROM work_embedding e JOIN work w USING(work_id) "
            "WHERE w.run_id=%s AND e.model=%s",
            (normalize_run_id, model),
        )
        if cur.fetchone()[0]:
            raise ValueError("Untracked embeddings already exist for this run/model; import refused")
        cur.execute(
            "SELECT v.source_record_id,w.work_id,w.canonical_title,w.abstract,q.decision "
            "FROM work_version v JOIN work w ON w.work_id=v.work_id "
            "JOIN quality_snapshot q ON q.work_id=w.work_id AND q.generation_id=%s "
            "WHERE v.run_id=%s AND v.source='arxiv'",
            (quality_generation_id, normalize_run_id),
        )
        mapping = {
            identifier: (work_id, title, abstract, decision)
            for identifier, work_id, title, abstract, decision in cur.fetchall()
        }
        if set(mapping) & set(ids) != set(ids):
            raise ValueError("Not all embedding source IDs map to this normalized run")
        for identifier in ids:
            _, title, abstract, decision = mapping[identifier]
            source = selected[identifier]
            if decision != "include" or title != source["title"] or abstract != source["abstract"]:
                raise ValueError(f"Normalized text or quality decision differs for {identifier}")

        cur.execute(
            "INSERT INTO embedding_import_generation "
            "(mission_id,normalize_run_id,quality_generation_id,model,dimension,row_count,"
            "observation_report_sha256,vectors_file_sha256,vectors_manifest_payload_sha256) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING embedding_generation_id",
            (mission_id, normalize_run_id, quality_generation_id, model, dimension, len(ids),
             report_hash, parquet_hash, manifest_hash),
        )
        generation_id = cur.fetchone()[0]
        for identifier, checksum, vector in zip(ids, payload_hashes, vectors):
            work_id, title, abstract, _ = mapping[identifier]
            text, text_source = build_text(title, abstract)
            cur.execute(
                "INSERT INTO work_embedding (work_id,model,dim,embedding,text_source,char_count) "
                "VALUES (%s,%s,%s,%s::vector,%s,%s)",
                (work_id, model, dimension,
                 "[" + ",".join(f"{float(value):.9g}" for value in vector) + "]",
                 text_source, len(text)),
            )
            cur.execute(
                "INSERT INTO embedding_import_member "
                "(embedding_generation_id,work_id,model,input_payload_sha256) VALUES (%s,%s,%s,%s)",
                (generation_id, work_id, model, checksum),
            )
        conn.commit()
    return {
        "embedding_generation_id": generation_id,
        "normalize_run_id": normalize_run_id,
        "quality_generation_id": quality_generation_id,
        "model": model,
        "rows": len(ids),
        "reused": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("generation_dir", type=Path)
    parser.add_argument("observation_report", type=Path)
    parser.add_argument("--quality-generation", type=int, required=True)
    args = parser.parse_args(argv)
    result = import_generation(args.generation_dir, args.observation_report, args.quality_generation)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
