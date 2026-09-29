"""Create an immutable file-only SPECTER2 embedding generation."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from saia.embed import (
    SPECTER2_ADAPTER_REVISION,
    SPECTER2_BASE_REVISION,
    Specter2Embedder,
    validate_vector_batch,
)
from saia.package_observations import payload_hash
from saia.specter2_smoke import _adapter_effect, _verify_report, peak_rss_bytes


def bytes_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def included_records(report: dict, max_records: int) -> tuple[list[dict], bool]:
    if type(max_records) is not int or not 1 <= max_records <= 2000:
        raise ValueError("max_records must be an integer between 1 and 2000")
    _verify_report(report)
    records = [
        row
        for row in report.get("records", [])
        if row.get("quality", {}).get("decision") == "include"
        and isinstance(row.get("title"), str)
        and row["title"].strip()
        and isinstance(row.get("abstract"), str)
        and row["abstract"].strip()
    ]
    records.sort(key=lambda row: row["source_record_id"])
    if not records:
        raise ValueError("No quality-include title/abstract records")
    return records[:max_records], len(records) > max_records


def _write_parquet(path: Path, rows: list[dict], vectors: list[list[float]], dimension: int) -> None:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as error:
        raise RuntimeError("pyarrow is required for file embedding generations") from error
    table = pa.table(
        {
            "source_record_id": pa.array(
                [row["source_record_id"] for row in rows], type=pa.string()
            ),
            "input_payload_sha256": pa.array(
                [row["payload_sha256"] for row in rows], type=pa.string()
            ),
            "embedding": pa.array(
                vectors, type=pa.list_(pa.float32(), list_size=dimension)
            ),
        }
    )
    pq.write_table(table, path, compression="zstd")
    check = pq.read_table(path)
    if check.num_rows != len(rows) or check.schema.field("embedding").type.list_size != dimension:
        raise ValueError("Written parquet does not match the in-memory generation")


def build(
    input_path: Path,
    output_dir: Path,
    *,
    max_records: int,
    batch_size: int,
    max_rss_gib: float,
) -> dict:
    if output_dir.exists():
        raise FileExistsError("Output directory exists; choose a new immutable generation")
    if type(batch_size) is not int or not 1 <= batch_size <= max_records:
        raise ValueError("batch_size must be between 1 and max_records")
    if not math.isfinite(max_rss_gib) or max_rss_gib <= 0:
        raise ValueError("max_rss_gib must be positive and finite")
    if os.environ.get("HF_HUB_OFFLINE") != "1" or os.environ.get("TRANSFORMERS_OFFLINE") != "1":
        raise ValueError("Set HF_HUB_OFFLINE=1 and TRANSFORMERS_OFFLINE=1")

    input_path = input_path.resolve()
    output_dir = output_dir.resolve()
    report = json.loads(input_path.read_text(encoding="utf-8"))
    selected, capped = included_records(report, max_records)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_dir.parent / f".{output_dir.name}.tmp-{uuid.uuid4().hex}"
    temporary.mkdir()
    try:
        started_at = datetime.now(timezone.utc).isoformat()
        rss_before = peak_rss_bytes()
        load_started = time.perf_counter()
        embedder = Specter2Embedder()
        load_seconds = time.perf_counter() - load_started
        probe_text = selected[0]["title"] + "\n\n" + selected[0]["abstract"]
        adapter_probe = _adapter_effect(embedder, probe_text)

        expected_dim: int | None = None
        vectors: list[list[float]] = []
        embedding_started = time.perf_counter()
        for start in range(0, len(selected), batch_size):
            chunk = selected[start : start + batch_size]
            texts = [row["title"] + "\n\n" + row["abstract"] for row in chunk]
            one_batch = embedder.embed(texts)
            expected_dim = validate_vector_batch(one_batch, len(chunk), expected_dim)
            vectors.extend(one_batch)
        embedding_seconds = time.perf_counter() - embedding_started
        if expected_dim != 768 or len(vectors) != len(selected):
            raise ValueError("Bounded SPECTER2 generation has an unexpected shape")

        parquet_path = temporary / "vectors.parquet"
        _write_parquet(parquet_path, selected, vectors, expected_dim)
        rss_peak = peak_rss_bytes()
        max_rss_bytes = int(max_rss_gib * 1024**3)
        root = Path(__file__).resolve().parents[1]
        manifest = {
            "version": "specter2-file-embeddings-0.4.8",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "started_at": started_at,
            "status": "passed" if rss_peak <= max_rss_bytes else "rss_limit_exceeded",
            "input": {
                "observation_report_path": str(input_path),
                "observation_report_bytes_sha256": bytes_sha256(input_path),
                "observation_report_payload_sha256": report["report_payload_sha256"],
                "mission_id": report["mission_id"],
                "quality_include_population": report["counts"]["quality_include_texts"],
                "selection": "source_record_id_sorted_quality_include_with_title_abstract",
                "max_records": max_records,
                "selected_count": len(selected),
                "capped": capped,
            },
            "model": {
                "name": embedder.name,
                "base_revision": SPECTER2_BASE_REVISION,
                "adapter_revision": SPECTER2_ADAPTER_REVISION,
                "adapter_name": embedder.adapter_name,
                "active_adapters": embedder.active_adapters,
                "adapter_effect_probe": adapter_probe,
                "device": embedder.device,
                "offline_enforced": True,
                "dimension": expected_dim,
                "batch_size": batch_size,
            },
            "runtime": {
                "python": os.sys.version.split()[0],
                "platform": os.sys.platform,
                "model_load_seconds": load_seconds,
                "embedding_seconds": embedding_seconds,
                "peak_rss_bytes": rss_peak,
                "rss_before_model_bytes": rss_before,
                "peak_minus_before_lower_bound_bytes": max(0, rss_peak - rss_before),
                "max_rss_bytes": max_rss_bytes,
                "within_rss_limit": rss_peak <= max_rss_bytes,
                "host_free_bytes_after": shutil.disk_usage(root).free,
            },
            "artifact": {
                "file": "vectors.parquet",
                "rows": len(selected),
                "bytes": parquet_path.stat().st_size,
                "bytes_sha256": bytes_sha256(parquet_path),
                "schema": "source_record_id:string,input_payload_sha256:string,embedding:fixed_size_list<float32>[768]",
            },
            "implementation_files_sha256": {
                name: bytes_sha256(root / name)
                for name in (
                    "saia/embed.py",
                    "saia/specter2_smoke.py",
                    "saia/file_embeddings.py",
                )
            },
            "accuracy_evaluated": False,
            "database_written": False,
            "scientific_candidates_generated": False,
            "limitations": [
                "This generation proves bounded embedding production, not signal detection quality.",
                "The input is a phrase-scoped arXiv sample and not a complete or coverage-comparable scientific corpus.",
                "The vectors use current text versions and are not a historical-text reconstruction.",
            ],
        }
        manifest["manifest_payload_sha256"] = payload_hash(manifest)
        manifest_path = temporary / "manifest.json"
        with manifest_path.open("x", encoding="utf-8") as stream:
            json.dump(manifest, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        if bytes_sha256(parquet_path) != manifest["artifact"]["bytes_sha256"]:
            raise ValueError("Parquet changed before generation seal")
        os.replace(temporary, output_dir)
        return manifest
    except BaseException:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-records", type=int, default=2000)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--max-rss-gib", type=float, default=8.0)
    args = parser.parse_args(argv)
    result = build(
        args.input,
        args.output_dir,
        max_records=args.max_records,
        batch_size=args.batch_size,
        max_rss_gib=args.max_rss_gib,
    )
    print(
        json.dumps(
            {
                "version": result["version"],
                "status": result["status"],
                "input": result["input"],
                "artifact": result["artifact"],
                "runtime": result["runtime"],
                "manifest_payload_sha256": result["manifest_payload_sha256"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())

