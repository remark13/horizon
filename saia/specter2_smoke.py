"""Bounded, offline SPECTER2 runtime smoke against an immutable report."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import resource
import shutil
import struct
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from saia.embed import (
    SPECTER2_ADAPTER_REVISION,
    SPECTER2_BASE_REVISION,
    Specter2Embedder,
    validate_vector_batch,
)
from saia.package_observations import payload_hash


def _bytes_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _verify_report(report: dict) -> None:
    expected = report.get("report_payload_sha256")
    payload = {key: value for key, value in report.items() if key != "report_payload_sha256"}
    if not expected or payload_hash(payload) != expected:
        raise ValueError("Input observation report payload hash does not match")
    counts = report.get("counts", {})
    if report.get("database_written") is not False or report.get("models_used") is not False:
        raise ValueError("Smoke accepts only the immutable pre-model file report")
    if counts.get("confirmed_weak_signals") is not None:
        raise ValueError("Input unexpectedly asserts confirmed weak signals")


def select_records(report: dict, limit: int) -> list[dict]:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Smoke limit must be an integer between 1 and 100")
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
    selected = records[:limit]
    if len(selected) != limit:
        raise ValueError("Not enough quality-include title/abstract texts for smoke")
    return selected


def peak_rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Darwin reports bytes; Linux and the Python documentation use KiB.
    return int(value if sys.platform == "darwin" else value * 1024)


def _vector_sha256(vector: list[float]) -> str:
    return hashlib.sha256(struct.pack(f">{len(vector)}f", *vector)).hexdigest()


def _adapter_effect(embedder: Specter2Embedder, text: str) -> dict:
    active_vector = embedder.embed([text])[0]
    active = embedder.model.active_adapters
    active_repr = repr(active)
    embedder.model.set_active_adapters(None)
    try:
        base_vector = embedder.embed([text])[0]
    finally:
        embedder.model.set_active_adapters(active)
    restored_repr = repr(embedder.model.active_adapters)
    if "proximity" not in active_repr or "proximity" not in restored_repr:
        raise ValueError("Proximity adapter is not active before and after the probe")
    differences = [a - b for a, b in zip(active_vector, base_vector)]
    l2_difference = math.sqrt(sum(value * value for value in differences))
    if not math.isfinite(l2_difference) or l2_difference <= 1e-6:
        raise ValueError("Proximity adapter does not measurably affect the embedding")
    active_norm = math.sqrt(sum(value * value for value in active_vector))
    base_norm = math.sqrt(sum(value * value for value in base_vector))
    cosine = sum(a * b for a, b in zip(active_vector, base_vector)) / (
        active_norm * base_norm
    )
    return {
        "active_adapters_before": active_repr,
        "active_adapters_after": restored_repr,
        "l2_difference_from_base": l2_difference,
        "max_absolute_difference_from_base": max(abs(value) for value in differences),
        "cosine_similarity_to_base": cosine,
        "proximity_effect_verified": True,
    }


def run(input_path: Path, *, limit: int, batch_size: int, max_rss_gib: float) -> dict:
    if type(batch_size) is not int or not 1 <= batch_size <= limit:
        raise ValueError("Batch size must be an integer between 1 and the smoke limit")
    if not math.isfinite(max_rss_gib) or max_rss_gib <= 0:
        raise ValueError("RSS limit must be positive and finite")
    if os.environ.get("HF_HUB_OFFLINE") != "1" or os.environ.get("TRANSFORMERS_OFFLINE") != "1":
        raise ValueError("Set HF_HUB_OFFLINE=1 and TRANSFORMERS_OFFLINE=1")

    input_path = input_path.resolve()
    report = json.loads(input_path.read_text(encoding="utf-8"))
    selected = select_records(report, limit)
    started_at = datetime.now(timezone.utc).isoformat()
    rss_before = peak_rss_bytes()
    load_started = time.perf_counter()
    embedder = Specter2Embedder()
    load_seconds = time.perf_counter() - load_started
    probe_text = selected[0]["title"] + "\n\n" + selected[0]["abstract"]
    adapter_probe = _adapter_effect(embedder, probe_text)

    expected_dim: int | None = None
    vector_rows: list[dict] = []
    embedding_started = time.perf_counter()
    for start in range(0, len(selected), batch_size):
        chunk = selected[start : start + batch_size]
        texts = [row["title"] + "\n\n" + row["abstract"] for row in chunk]
        vectors = embedder.embed(texts)
        expected_dim = validate_vector_batch(vectors, len(chunk), expected_dim)
        for row, vector in zip(chunk, vectors):
            vector_rows.append(
                {
                    "source_record_id": row["source_record_id"],
                    "input_payload_sha256": row["payload_sha256"],
                    "vector_sha256_float32_be": _vector_sha256(vector),
                }
            )
    embedding_seconds = time.perf_counter() - embedding_started
    rss_peak = peak_rss_bytes()
    max_rss_bytes = int(max_rss_gib * 1024**3)
    root = Path(__file__).resolve().parents[1]
    result = {
        "version": "specter2-runtime-smoke-0.4.7",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "started_at": started_at,
        "status": "passed" if rss_peak <= max_rss_bytes else "rss_limit_exceeded",
        "input": {
            "path": str(input_path),
            "file_bytes_sha256": _bytes_sha256(input_path),
            "report_payload_sha256": report["report_payload_sha256"],
            "mission_id": report["mission_id"],
            "quality_include_population": report["counts"]["quality_include_texts"],
            "selection": "first_source_record_id_sorted_quality_include_with_title_abstract",
            "selected_count": len(selected),
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
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "model_load_seconds": load_seconds,
            "embedding_seconds": embedding_seconds,
            "peak_rss_bytes": rss_peak,
            "rss_before_model_bytes": rss_before,
            "peak_minus_before_lower_bound_bytes": max(0, rss_peak - rss_before),
            "max_rss_bytes": max_rss_bytes,
            "within_rss_limit": rss_peak <= max_rss_bytes,
            "host_free_bytes_after": shutil.disk_usage(root).free,
        },
        "implementation_files_sha256": {
            name: _bytes_sha256(root / name)
            for name in ("saia/embed.py", "saia/specter2_smoke.py")
        },
        "vectors": vector_rows,
        "vectors_count": len(vector_rows),
        "vectors_finite_nonzero": True,
        "accuracy_evaluated": False,
        "database_written": False,
        "scientific_candidates_generated": False,
        "limitations": [
            "Smoke proves local runtime and finite vectors only, not scientific quality.",
            "The deterministic first 100 include texts are a resource sample, not an unbiased evaluation sample.",
            "Vector hashes can differ across hardware or numerical runtimes without implying different scientific quality.",
        ],
    }
    result["report_payload_sha256"] = payload_hash(result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--max-rss-gib", type=float, default=8.0)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("Output exists; choose a new immutable report path")
    result = run(
        args.input,
        limit=args.limit,
        batch_size=args.batch_size,
        max_rss_gib=args.max_rss_gib,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(
        json.dumps(
            {
                key: result[key]
                for key in ("version", "status", "vectors_count", "report_payload_sha256")
            }
            | {"runtime": result["runtime"], "model": result["model"]},
            ensure_ascii=False,
        )
    )
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
