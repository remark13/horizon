"""Compact reusable arXiv cache for the nine internal focus profiles.

The cache is an acceleration layer, never a query authority.  A user query may
use it only when every approved literal include phrase is proven covered by a
broader-or-equal indexed anchor.  Otherwise the caller must scan the pinned
mirror.  Profile membership is not a weak-signal label or a UI restriction.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import re
import shutil
import tempfile
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

from saia.arxiv_metadata import (LITERAL_MATCHING_VERSION, first_submission,
                                 matches_controlled_plan, validate_schema)
from saia.controlled_collection import inventory_sha256, sha256_file
from saia.focus_areas import catalog
from saia.local_arxiv_search import _substring_mask, policy, validate_inventory
from saia.query_expansion import digest


VERSION = "thematic-arxiv-cache-0.4.41-r2"
DEFAULT_START = date(2000, 1, 1)
MAX_CACHE_BYTES = 2 * 1024 ** 3
MAX_TARGET_PACK_BYTES = 2 * 1024 ** 3
_TOKEN = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)
_GENERIC = frozenset({
    "advanced", "artificial", "development", "engineering", "industrial",
    "new", "production", "system", "systems", "technology", "technologies",
    "technology", "технологии", "технология", "развитие", "системы", "новые",
    "and", "for", "of", "in", "to", "и", "для", "в", "на",
})
_CONNECTORS = frozenset({"and", "for", "of", "in", "to", "и", "для", "в", "на"})


def _normalised(value: str) -> str:
    return " ".join(str(value or "").casefold().split())


def _anchor_phrases(value: str) -> set[str]:
    """Derive conservative literal anchors, not synonyms or a ready query."""
    tokens = _TOKEN.findall(_normalised(value))
    if not tokens:
        return set()
    phrases = {" ".join(tokens)} if len(tokens) <= 8 else set()
    for width in (2, 3, 4):
        for start in range(len(tokens) - width + 1):
            group = tokens[start:start + width]
            # A domain phrase may legitimately end in a broad word, e.g.
            # ``tissue engineering`` or ``energy systems``.  Only grammatical
            # connectors are forbidden at phrase boundaries; broad terms are
            # still forbidden as standalone anchors below.
            if group[0] in _CONNECTORS or group[-1] in _CONNECTORS:
                continue
            if all(token in _GENERIC for token in group):
                continue
            phrases.add(" ".join(group))
    for token in tokens:
        if len(token) >= 10 and token not in _GENERIC and not token.isdigit():
            phrases.add(token)
    return {phrase for phrase in phrases if len(phrase) >= 4}


def anchor_catalog() -> dict:
    """Build a deterministic many-to-many anchor/profile catalog."""
    mapping: dict[str, set[tuple[str, str | None]]] = defaultdict(set)
    source = catalog()
    for profile in source["profiles"]:
        profile_id = profile["id"]
        values = [
            profile["name_ru"], profile["short_name_ru"],
            profile["starter_query_en"], *profile["aliases_ru"],
            *profile["aliases_en"],
        ]
        for value in values:
            for phrase in _anchor_phrases(value):
                mapping[phrase].add((profile_id, None))
        for subarea in profile["subareas"]:
            for value in (subarea["name_ru"], subarea["query_en"]):
                for phrase in _anchor_phrases(value):
                    mapping[phrase].add((profile_id, subarea["id"]))
    anchors = []
    for phrase in sorted(mapping):
        anchors.append({
            "anchor_id": "a-" + hashlib.sha256(phrase.encode("utf-8")).hexdigest()[:16],
            "phrase": phrase,
            "targets": [
                {"profile_id": profile_id, "subarea_id": subarea_id}
                for profile_id, subarea_id in sorted(
                    mapping[phrase], key=lambda item: (item[0], item[1] or "")
                )
            ],
        })
    result = {
        "version": VERSION,
        "focus_area_catalog_version": source["version"],
        "policy": {
            "ui_exposed_as_fixed_choices": False,
            "user_query_may_be_narrowed_automatically": False,
            "cache_is_query_authority": False,
            "unknown_query_falls_back_to_full_mirror": True,
            "matching": "literal_phrase_casefold_collapsed_whitespace_word_boundaries",
        },
        "anchors": anchors,
    }
    result["payload_sha256"] = digest(result)
    return result


def _phrase_present(texts: tuple[str, str], phrase: str) -> bool:
    expression = re.compile(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)")
    return any(expression.search(text) for text in texts)


def _covered_phrase(phrase: str, anchors: set[str]) -> str | None:
    """Return an indexed phrase whose result set is a superset of ``phrase``."""
    value = _normalised(phrase)
    for anchor in sorted(anchors, key=lambda item: (-len(item), item)):
        if re.search(r"(?<!\w)" + re.escape(anchor) + r"(?!\w)", value):
            return anchor
    return None


def coverage_for_plan(cache_catalog: dict, plan: dict) -> dict:
    included = list(plan.get("included_terms") or [])
    anchors = {item["phrase"] for item in cache_catalog.get("anchors") or []}
    covered = {phrase: _covered_phrase(phrase, anchors) for phrase in included}
    missing = [phrase for phrase, anchor in covered.items() if anchor is None]
    legacy_semantics = plan.get("matching_version", LITERAL_MATCHING_VERSION) == LITERAL_MATCHING_VERSION
    return {
        "complete": bool(included) and not missing and legacy_semantics,
        "included_term_coverage": covered,
        "missing_included_terms": missing,
        "safe_semantics": (
            "Each approved include phrase has a broader-or-equal indexed literal anchor; "
            "the exact approved predicate is reapplied to cached rows."
            if included and not missing and legacy_semantics else
            "Cache cannot prove complete coverage; scan the pinned mirror."
        ),
    }


def build_cache(mirror_dir: str | Path, output: str | Path, *,
                period_from: date = DEFAULT_START,
                as_of_date: date | None = None,
                expected_files: int | None = None,
                expected_rows: int | None = None,
                max_cache_bytes: int = MAX_CACHE_BYTES) -> dict:
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    as_of_date = as_of_date or date.today().replace(day=1)
    if period_from >= as_of_date:
        raise ValueError("Thematic cache period is empty")
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("Thematic cache output already exists; caches are immutable")
    cfg = policy()
    expected_files = cfg["expected_files"] if expected_files is None else expected_files
    expected_rows = cfg["expected_rows"] if expected_rows is None else expected_rows
    files = validate_inventory(str(Path(mirror_dir).resolve()), expected_files, expected_rows)
    anchors_payload = anchor_catalog()
    anchors = anchors_payload["anchors"]
    phrases = [item["phrase"] for item in anchors]
    by_phrase = {item["phrase"]: item for item in anchors}
    token_to_phrases: dict[str, set[str]] = defaultdict(set)
    for phrase in phrases:
        tokens = _TOKEN.findall(phrase)
        key = max(tokens, key=lambda token: (len(token), token))
        token_to_phrases[key].add(phrase)

    output.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=output.name + ".tmp-", dir=output.parent))
    doc_writer = assignment_writer = None
    scanned = coarse = documents = assignments = invalid_dates = 0
    exact_duplicates_skipped = older_conflicting_duplicates_skipped = 0
    target_counts: Counter[tuple[str, str | None]] = Counter()
    year_counts: Counter[str] = Counter()
    identifiers: dict[str, tuple[str, str]] = {}
    try:
        anchors_path = temp / "anchors.json"
        anchors_path.write_text(
            json.dumps(anchors_payload, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )
        for path in files:
            parquet = pq.ParquetFile(path)
            validate_schema(parquet.schema_arrow.names)
            for batch in parquet.iter_batches(batch_size=8192):
                scanned += batch.num_rows
                table = pa.Table.from_batches([batch])
                normalised = {
                    field: pc.replace_substring_regex(
                        pc.utf8_lower(pc.fill_null(table[field], "")),
                        pattern=r"\s+", replacement=" ",
                    )
                    for field in ("title", "abstract")
                }
                combined = None
                # Chunked RE2 prefilter avoids one operation per anchor and
                # keeps the expression size bounded.
                for offset in range(0, len(phrases), 64):
                    pattern = "(?:" + "|".join(
                        re.escape(item) for item in phrases[offset:offset + 64]
                    ) + ")"
                    chunk = pc.or_kleene(
                        pc.match_substring_regex(normalised["title"], pattern),
                        pc.match_substring_regex(normalised["abstract"], pattern),
                    )
                    combined = chunk if combined is None else pc.or_kleene(combined, chunk)
                candidate_table = table.filter(pc.fill_null(combined, False))
                coarse += candidate_table.num_rows
                chosen_indexes: list[int] = []
                assignment_rows: list[dict] = []
                for index, row in enumerate(candidate_table.to_pylist()):
                    try:
                        published = date.fromisoformat(first_submission(row.get("versions")))
                    except ValueError:
                        invalid_dates += 1
                        continue
                    if not period_from <= published < as_of_date:
                        continue
                    identifier = str(row.get("id") or "").strip()
                    if not identifier:
                        continue
                    texts = (_normalised(row.get("title")), _normalised(row.get("abstract")))
                    tokens = set(_TOKEN.findall(" ".join(texts)))
                    candidate_phrases: set[str] = set()
                    for token in tokens:
                        candidate_phrases.update(token_to_phrases.get(token, ()))
                    matched = [phrase for phrase in candidate_phrases
                               if _phrase_present(texts, phrase)]
                    if not matched:
                        continue
                    fingerprint = hashlib.sha256(json.dumps(
                        row, ensure_ascii=False, sort_keys=True, default=str,
                        separators=(",", ":"),
                    ).encode("utf-8")).hexdigest()
                    update_key = str(row.get("update_date") or "")
                    previous = identifiers.get(identifier)
                    if previous is not None:
                        previous_update, previous_fingerprint = previous
                        if fingerprint == previous_fingerprint:
                            exact_duplicates_skipped += 1
                            continue
                        if update_key < previous_update:
                            older_conflicting_duplicates_skipped += 1
                            continue
                        raise ValueError(
                            "Thematic cache encountered a same-or-newer conflicting "
                            f"duplicate after writing arXiv id {identifier}; source order "
                            "cannot be resolved without a separate canonicalization pass"
                        )
                    identifiers[identifier] = (update_key, fingerprint)
                    chosen_indexes.append(index)
                    documents += 1
                    year_counts[str(published.year)] += 1
                    document_targets: set[tuple[str, str | None]] = set()
                    for phrase in sorted(matched):
                        anchor = by_phrase[phrase]
                        assignment_rows.append({
                            "arxiv_id": identifier,
                            "anchor_id": anchor["anchor_id"],
                            "phrase": phrase,
                        })
                        assignments += 1
                        document_targets.update(
                            (target["profile_id"], target["subarea_id"])
                            for target in anchor["targets"]
                        )
                    for target in document_targets:
                        target_counts[target] += 1
                if chosen_indexes:
                    chosen = candidate_table.take(pa.array(chosen_indexes, type=pa.int64()))
                    if doc_writer is None:
                        doc_writer = pq.ParquetWriter(
                            temp / "documents.parquet", chosen.schema, compression="zstd"
                        )
                    doc_writer.write_table(chosen)
                if assignment_rows:
                    assignment_table = pa.Table.from_pylist(assignment_rows)
                    if assignment_writer is None:
                        assignment_writer = pq.ParquetWriter(
                            temp / "assignments.parquet", assignment_table.schema,
                            compression="zstd",
                        )
                    assignment_writer.write_table(assignment_table)
                current_bytes = sum(
                    item.stat().st_size for item in temp.iterdir() if item.is_file()
                )
                if current_bytes > max_cache_bytes:
                    raise ValueError("Thematic cache exceeded the configured size limit")
        if doc_writer is None or documents == 0:
            raise ValueError("Thematic cache is empty")
        doc_writer.close()
        doc_writer = None
        assignment_writer.close()
        assignment_writer = None
        manifest = {
            "version": VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "focus_area_catalog_version": anchors_payload["focus_area_catalog_version"],
            "anchor_catalog_sha256": anchors_payload["payload_sha256"],
            "source": {
                "dataset": cfg["dataset"], "revision": cfg["revision"],
                "inventory_files": len(files), "inventory_rows": expected_rows,
                "inventory_sha256": inventory_sha256(files, expected_rows, cfg),
            },
            "period": {"from": period_from.isoformat(), "as_of_exclusive": as_of_date.isoformat()},
            "counts": {
                "scanned_rows": scanned, "coarse_rows": coarse,
                "documents": documents, "assignments": assignments,
                "invalid_dates": invalid_dates, "anchors": len(anchors),
                "exact_duplicates_skipped": exact_duplicates_skipped,
                "older_conflicting_duplicates_skipped": older_conflicting_duplicates_skipped,
            },
            "year_counts": dict(sorted(year_counts.items())),
            "target_counts": [
                {"profile_id": profile_id, "subarea_id": subarea_id, "documents": count}
                for (profile_id, subarea_id), count in sorted(
                    target_counts.items(), key=lambda item: (item[0][0], item[0][1] or "")
                )
            ],
            "files": {},
            "interpretation": {
                "cache_is_query_authority": False,
                "profile_membership_is_weak_signal_label": False,
                "unknown_query_falls_back_to_full_mirror": True,
            },
        }
        for name in ("anchors.json", "documents.parquet", "assignments.parquet"):
            item = temp / name
            manifest["files"][name] = {
                "bytes": item.stat().st_size, "sha256": sha256_file(item)
            }
        manifest["payload_sha256"] = digest(manifest)
        (temp / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )
        temp.rename(output)
        return manifest
    except Exception:
        if doc_writer is not None:
            doc_writer.close()
        if assignment_writer is not None:
            assignment_writer.close()
        shutil.rmtree(temp, ignore_errors=True)
        raise


@functools.lru_cache(maxsize=4)
def _load_cache_cached(root_text: str, manifest_mtime_ns: int) -> tuple[dict, dict]:
    root = Path(root_text)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    anchors = json.loads((root / "anchors.json").read_text(encoding="utf-8"))
    if manifest.get("version") != VERSION or anchors.get("version") != VERSION:
        raise ValueError("Unsupported thematic cache version")
    expected = manifest.get("payload_sha256")
    if expected != digest({key: value for key, value in manifest.items()
                           if key != "payload_sha256"}):
        raise ValueError("Thematic cache manifest hash mismatch")
    if manifest.get("anchor_catalog_sha256") != anchors.get("payload_sha256"):
        raise ValueError("Thematic cache anchor catalog mismatch")
    for name, metadata in manifest["files"].items():
        path = root / name
        if path.stat().st_size != metadata["bytes"] or sha256_file(path) != metadata["sha256"]:
            raise ValueError(f"Thematic cache file identity mismatch: {name}")
    return manifest, anchors


def load_cache(cache_dir: str | Path) -> tuple[dict, dict]:
    root = Path(cache_dir).resolve()
    manifest_path = root / "manifest.json"
    return _load_cache_cached(str(root), manifest_path.stat().st_mtime_ns)


def select(cache_dir: str | Path, plan: dict, *, max_records: int | None = None):
    """Return exact matching cached rows only when coverage is provably complete."""
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    manifest, anchors = load_cache(cache_dir)
    coverage = coverage_for_plan(anchors, plan)
    if not coverage["complete"]:
        raise ValueError("Thematic cache does not cover every approved include phrase")
    start = date.fromisoformat(plan["date_from"])
    cutoff = date.fromisoformat(plan["as_of_date"])
    cache_start = date.fromisoformat(manifest["period"]["from"])
    cache_cutoff = date.fromisoformat(manifest["period"]["as_of_exclusive"])
    if start < cache_start or cutoff > cache_cutoff:
        raise ValueError("Thematic cache does not cover the approved date range")
    included = list(plan.get("included_terms") or [])
    rows = []
    coarse_rows = 0
    for batch in pq.ParquetFile(Path(cache_dir) / "documents.parquet").iter_batches(
            batch_size=8192):
        table = pa.Table.from_batches([batch])
        candidates = table.filter(
            pc.fill_null(_substring_mask(table, included), False)
        )
        coarse_rows += candidates.num_rows
        for row in candidates.to_pylist():
            if not matches_controlled_plan(row, plan):
                continue
            published = date.fromisoformat(first_submission(row.get("versions")))
            if start <= published < cutoff:
                rows.append(row)
                if max_records is not None and len(rows) > max_records:
                    raise ValueError(
                        f"Selected arXiv cohort exceeds the approved limit of {max_records} records"
                    )
    return pa.Table.from_pylist(rows), {
        "cache_version": VERSION,
        "cache_manifest_sha256": manifest["payload_sha256"],
        "coverage": coverage,
        "cache_documents_scanned": manifest["counts"]["documents"],
        "cache_coarse_rows": coarse_rows,
        "selected_records": len(rows),
        "full_mirror_rows_avoided": manifest["counts"]["scanned_rows"],
        "selection_predicate_reapplied": True,
    }


def _target_key(profile_id: str, subarea_id: str | None) -> str:
    return profile_id + (f"--{subarea_id}" if subarea_id else "--broad")


def build_target_packs(cache_dir: str | Path, output: str | Path, *,
                       max_output_bytes: int = MAX_TARGET_PACK_BYTES) -> dict:
    """Explode the shared cache into reusable profile/subarea supersets."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    cache_root = Path(cache_dir).resolve()
    source_manifest, anchors = load_cache(cache_root)
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("Thematic target-pack output already exists; packs are immutable")
    phrase_targets = {
        item["phrase"]: {
            _target_key(target["profile_id"], target["subarea_id"])
            for target in item["targets"]
        }
        for item in anchors["anchors"]
    }
    document_targets: dict[str, set[str]] = defaultdict(set)
    assignments_path = cache_root / "assignments.parquet"
    for batch in pq.ParquetFile(assignments_path).iter_batches(
            batch_size=65536, columns=["arxiv_id", "phrase"]):
        ids = batch.column("arxiv_id").to_pylist()
        phrases = batch.column("phrase").to_pylist()
        for identifier, phrase in zip(ids, phrases, strict=True):
            document_targets[identifier].update(phrase_targets[phrase])

    output.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=output.name + ".tmp-", dir=output.parent))
    writers: dict[str, object] = {}
    counts: Counter[str] = Counter()
    try:
        documents_path = cache_root / "documents.parquet"
        for batch_index, batch in enumerate(
                pq.ParquetFile(documents_path).iter_batches(batch_size=8192)):
            table = pa.Table.from_batches([batch])
            grouped: dict[str, list[int]] = defaultdict(list)
            for index, identifier in enumerate(table["id"].to_pylist()):
                for target in document_targets.get(identifier, ()):
                    grouped[target].append(index)
            for target, indexes in grouped.items():
                selected = table.take(pa.array(indexes, type=pa.int64()))
                writer = writers.get(target)
                if writer is None:
                    writer = pq.ParquetWriter(
                        temp / f"{target}.parquet", selected.schema, compression="zstd"
                    )
                    writers[target] = writer
                writer.write_table(selected)
                counts[target] += selected.num_rows
            if batch_index % 20 == 0:
                current_bytes = sum(
                    item.stat().st_size for item in temp.glob("*.parquet")
                )
                if current_bytes > max_output_bytes:
                    raise ValueError("Thematic target packs exceeded the configured size limit")
        for writer in writers.values():
            writer.close()
        writers.clear()
        target_metadata = []
        total_bytes = 0
        expected_counts = {
            _target_key(item["profile_id"], item["subarea_id"]): item["documents"]
            for item in source_manifest["target_counts"]
        }
        for target in sorted(counts):
            path = temp / f"{target}.parquet"
            size = path.stat().st_size
            total_bytes += size
            if counts[target] != expected_counts.get(target):
                raise ValueError(f"Target-pack row count mismatch: {target}")
            profile_id, suffix = target.split("--", 1)
            target_metadata.append({
                "target_key": target, "profile_id": profile_id,
                "subarea_id": None if suffix == "broad" else suffix,
                "file": path.name, "rows": counts[target], "bytes": size,
                "sha256": sha256_file(path),
            })
        if total_bytes > max_output_bytes:
            raise ValueError("Thematic target packs exceeded the configured size limit")
        manifest = {
            "version": VERSION + "+target-packs-v1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_cache_manifest_sha256": source_manifest["payload_sha256"],
            "source_anchor_catalog_sha256": source_manifest["anchor_catalog_sha256"],
            "targets": target_metadata,
            "counts": {
                "targets": len(target_metadata),
                "rows_with_duplication": sum(counts.values()),
                "bytes": total_bytes,
            },
            "interpretation": {
                "packs_are_candidate_supersets": True,
                "exact_query_predicate_must_be_reapplied": True,
                "packs_are_not_ui_choices_or_weak_signal_labels": True,
            },
        }
        manifest["payload_sha256"] = digest(manifest)
        (temp / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )
        temp.rename(output)
        return manifest
    except Exception:
        for writer in writers.values():
            writer.close()
        shutil.rmtree(temp, ignore_errors=True)
        raise


@functools.lru_cache(maxsize=4)
def _load_target_packs_cached(root_text: str, manifest_mtime_ns: int,
                              source_cache_sha256: str) -> dict:
    root = Path(root_text)
    payload = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if payload.get("version") != VERSION + "+target-packs-v1":
        raise ValueError("Unsupported thematic target-pack version")
    if payload.get("source_cache_manifest_sha256") != source_cache_sha256:
        raise ValueError("Thematic target packs belong to another source cache")
    expected = payload.get("payload_sha256")
    if expected != digest({key: value for key, value in payload.items()
                           if key != "payload_sha256"}):
        raise ValueError("Thematic target-pack manifest hash mismatch")
    for item in payload["targets"]:
        path = root / item["file"]
        if path.stat().st_size != item["bytes"] or sha256_file(path) != item["sha256"]:
            raise ValueError(f"Thematic target-pack identity mismatch: {item['target_key']}")
    return payload


def load_target_packs(packs_dir: str | Path, source_cache_sha256: str) -> dict:
    root = Path(packs_dir).resolve()
    manifest_path = root / "manifest.json"
    return _load_target_packs_cached(
        str(root), manifest_path.stat().st_mtime_ns, source_cache_sha256
    )


def _choose_target_packs(anchors: dict, packs: dict, coverage: dict) -> list[dict]:
    phrase_targets = {
        item["phrase"]: {
            _target_key(target["profile_id"], target["subarea_id"])
            for target in item["targets"]
        }
        for item in anchors["anchors"]
    }
    required = [
        phrase_targets[anchor]
        for anchor in coverage["included_term_coverage"].values()
        if anchor is not None
    ]
    by_key = {item["target_key"]: item for item in packs["targets"]}
    common = set.intersection(*required) if required else set()
    if common:
        return [min((by_key[key] for key in common), key=lambda item: item["rows"])]
    chosen = {}
    for targets in required:
        item = min((by_key[key] for key in targets), key=lambda value: value["rows"])
        chosen[item["target_key"]] = item
    return [chosen[key] for key in sorted(chosen)]


def select_from_target_packs(cache_dir: str | Path, packs_dir: str | Path,
                             plan: dict, *, max_records: int | None = None):
    """Use the smallest proven superset pack and reapply the exact plan."""
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    source_manifest, anchors = load_cache(cache_dir)
    coverage = coverage_for_plan(anchors, plan)
    if not coverage["complete"]:
        raise ValueError("Thematic cache does not cover every approved include phrase")
    packs = load_target_packs(packs_dir, source_manifest["payload_sha256"])
    selected_packs = _choose_target_packs(anchors, packs, coverage)
    start = date.fromisoformat(plan["date_from"])
    cutoff = date.fromisoformat(plan["as_of_date"])
    cache_start = date.fromisoformat(source_manifest["period"]["from"])
    cache_cutoff = date.fromisoformat(source_manifest["period"]["as_of_exclusive"])
    if start < cache_start or cutoff > cache_cutoff:
        raise ValueError("Thematic cache does not cover the approved date range")
    included = list(plan.get("included_terms") or [])
    rows_by_id = {}
    coarse_rows = 0
    for item in selected_packs:
        for batch in pq.ParquetFile(Path(packs_dir) / item["file"]).iter_batches(
                batch_size=8192):
            table = pa.Table.from_batches([batch])
            candidates = table.filter(
                pc.fill_null(_substring_mask(table, included), False)
            )
            coarse_rows += candidates.num_rows
            for row in candidates.to_pylist():
                if not matches_controlled_plan(row, plan):
                    continue
                published = date.fromisoformat(first_submission(row.get("versions")))
                if start <= published < cutoff:
                    rows_by_id[str(row["id"])] = row
                    if max_records is not None and len(rows_by_id) > max_records:
                        raise ValueError(
                            f"Selected arXiv cohort exceeds the approved limit of {max_records} records"
                        )
    rows = [rows_by_id[key] for key in sorted(rows_by_id)]
    return pa.Table.from_pylist(rows), {
        "cache_version": VERSION,
        "cache_manifest_sha256": source_manifest["payload_sha256"],
        "target_pack_manifest_sha256": packs["payload_sha256"],
        "coverage": coverage,
        "selected_target_packs": [item["target_key"] for item in selected_packs],
        "target_pack_rows_scanned": sum(item["rows"] for item in selected_packs),
        "cache_coarse_rows": coarse_rows,
        "selected_records": len(rows),
        "selection_predicate_reapplied": True,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build compact thematic arXiv cache")
    parser.add_argument("mirror_dir")
    parser.add_argument("output")
    parser.add_argument("--period-from", default=DEFAULT_START.isoformat())
    parser.add_argument("--as-of-date", default=date.today().replace(day=1).isoformat())
    args = parser.parse_args(argv)
    result = build_cache(
        args.mirror_dir, args.output,
        period_from=date.fromisoformat(args.period_from),
        as_of_date=date.fromisoformat(args.as_of_date),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
