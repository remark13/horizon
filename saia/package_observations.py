"""Audited local-arXiv observations without importing partial data into the DB.

Counts are native source identities, NOT adjudicated independent studies.
Calendar completeness, package completeness and field coverage are distinct.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from saia.arxiv_local import digest
from saia.arxiv_metadata import matches_text_scope, text_scope
from saia.ingest import READERS, collection_content_hash, collection_coverage, validate_collection_input
from saia.measurement import WindowCounts, analyze_series
from saia.normalize import doi_conflicts, identity_policy, parse_arxiv
from saia.publication_status import status_evidence
from saia.quality import POLICY_VERSION as QUALITY_VERSION, decide
from saia import methodology

POLICY = {
    "version": "local-package-observations-0.4.7",
    "identity_unit": "unique_native_arxiv_id_not_independent_research",
    "coverage_comparable": None,
    "counts": "all_structurally_valid_selected_deposits_by_v1_date",
    "status_projection": "current_reported_status; effective_date_unknown; never_historical_truth",
    "text_analysis": "quality_include_only; later_than_cutoff_text_quarantined",
    "max_records": 2000,
    "window_step": "month",
    "verdict": "descriptive_only_no_confirmed_signal",
}


def payload_hash(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def monthly_bounds(start: date, end: date) -> list[tuple[date, date]]:
    if start.day != 1 or end.day != 1 or start >= end:
        raise ValueError("Require a nonempty period bounded by whole calendar months")
    windows = []
    while start < end:
        following = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
        windows.append((start, following))
        start = following
    return windows


def _series(records: list[dict], parent: list[dict], grid: list[tuple[date, date]], cutoff: date) -> dict:
    counts = Counter(r["publication_date"][:7] for r in records)
    totals = Counter(r["publication_date"][:7] for r in parent)
    return analyze_series([WindowCounts(start, end, counts[start.isoformat()[:7]],
                                        totals[start.isoformat()[:7]], coverage_comparable=None)
                           for start, end in grid], cutoff)


def _author_observations(records: list[dict], grid: list[tuple[date, date]]) -> dict:
    """Exact full-name sets are a signature proxy, not team independence."""
    seen: set[tuple[str, ...]] = set()
    points = []
    for start, end in grid:
        cohort = [r for r in records if start.isoformat() <= r["publication_date"] < end.isoformat()]
        signatures = {tuple(sorted({" ".join(a.casefold().split()) for a in r["author_names"] if a.strip()}))
                      for r in cohort if r["author_names"]}
        signatures.discard(())
        points.append({"start": start.isoformat(), "end": end.isoformat(),
                       "deposits": len(cohort), "deposits_with_author_names": sum(bool(r["author_names"]) for r in cohort),
                       "distinct_full_name_set_signatures": len(signatures),
                       "new_signature_count_in_this_package": len(signatures - seen),
                       "continuing_signature_count": len(signatures & seen)})
        seen.update(signatures)
    return {"version": "full-name-signature-observations-0.4.7", "points": points,
            "independent_group_count": None, "organisation_diffusion": None,
            "limitations": ["Разные составы имён не доказывают независимость команд: авторы могут пересекаться или менять написание.",
                            "Новизна подписи только относительно предыдущих месяцев пакета, не всей науки.",
                            "В этом mirror нет проверенных организаций и исторических affiliation."]}


def observe_package(root: Path, *, max_records: int = 2000) -> dict:
    if type(max_records) is not int or not 1 <= max_records <= POLICY["max_records"]:
        raise ValueError("Observation bound must be an integer between 1 and 2000")
    root = root.resolve()
    manifest_path = root / "manifest.json"
    if not manifest_path.resolve().is_relative_to(root):
        raise ValueError("Manifest file outside package")
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    if (manifest.get("mission_snapshot_file") != "mission.json"
            or set(manifest.get("sources", {})) != {"arxiv"}
            or manifest["sources"]["arxiv"].get("access_mode") != "local-arxiv-metadata-parquet"):
        raise ValueError("Observation path accepts only frozen local-arXiv packages")
    mission_path = root / "mission.json"
    if not mission_path.resolve().is_relative_to(root):
        raise ValueError("Mission file outside package")
    mission_bytes = mission_path.read_bytes()
    mission_text = mission_bytes.decode("utf-8")
    mission = json.loads(mission_text)
    validate_collection_input(root, manifest, mission, mission_text)
    scope = text_scope(mission)
    if scope is None:
        raise ValueError("Observations require an explicit title/abstract phrase scope")
    block = manifest["sources"]["arxiv"]
    if (type(block.get("total_records")) is not int or not 0 < block["total_records"] <= max_records
            or sum(e["records"] for e in block["files"]) != block["total_records"]):
        raise ValueError("Package record count is invalid or exceeds the observation bound")
    start = date.fromisoformat(mission["period"]["from"])
    end = date.fromisoformat(mission["period"]["to"]) + timedelta(days=1)
    cutoff = date.fromisoformat(mission["as_of_date"])
    grid = monthly_bounds(start, end)
    if end > cutoff:
        raise ValueError("Observation period extends beyond the cutoff")
    started = datetime.now(timezone.utc).isoformat()
    policy = {**POLICY, "max_records": max_records}
    quality_policy = methodology.load_default().raw["publication_quality"]
    code_root = Path(__file__).resolve().parents[1]
    files = ["saia/package_observations.py", "saia/arxiv_local.py", "saia/arxiv_metadata.py",
             "saia/ingest.py", "saia/normalize.py", "saia/quality.py", "saia/publication_status.py",
             "saia/measurement.py", "saia/observed_series.py", "saia/methodology.py",
             "config/measurement.v0.4.yaml", "config/normalization.v0.4.5.json"]
    code_hashes = {name: digest(code_root / name) for name in files}
    source_inputs = []
    seen = set()
    for entry in block["files"]:
        path = root / "arxiv" / entry["file"]
        items = list(READERS["arxiv"](path, mission))
        if len(items) != entry["records"]:
            raise ValueError("Reader count differs from manifest; no silent out-of-scope filtering")
        for identifier, payload in items:
            if identifier in seen:
                raise ValueError("Repeated native arXiv ID in observation input")
            seen.add(identifier)
            created = payload["created"]
            if not start.isoformat() <= created < end.isoformat():
                raise ValueError("Publication outside frozen period")
            source_inputs.append((identifier, payload, parse_arxiv(payload), entry["file"]))
    if len(source_inputs) != block["total_records"]:
        raise ValueError("Package total differs from actual records")
    conflicts = doi_conflicts([parsed for _, _, parsed, _ in source_inputs])
    rows = []
    for index, (identifier, payload, parsed, filename) in enumerate(sorted(source_inputs)):
        conflict_evidence = [conflicts[v] for k, v in parsed["identifiers"] if k == "doi" and v in conflicts]
        decision = decide(title=parsed["title"], abstract=parsed["abstract"], publication_year=int(payload["created"][:4]),
                          date_is_imprecise=False, terms=scope["phrases"], sources={"arxiv"},
                          openalex_payloads=[], arxiv_payloads=[payload], categories=mission["query"]["arxiv_categories"],
                          as_of_date=cutoff.isoformat(), policy=quality_policy, identity_conflicts=conflict_evidence,
                          status_observed_at=started)
        quality = asdict(decision)
        # Quality's database default provenance must not be asserted in a file-only report.
        for status in quality["flags"].get("deposit_status_evidence", []):
            status["observation_time_source"] = "file_report.created_at"
        status = status_evidence([payload], [])[0]
        status["status_observed_at"] = started
        status["observation_time_source"] = "file_report.created_at"
        rows.append({"record_key": index + 1, "source_record_id": identifier,
                     "source_url": "https://arxiv.org/abs/" + identifier,
                     "package_file": filename, "payload_sha256": payload_hash(payload),
                     "publication_date": payload["created"], "latest_version_date": payload["updated"],
                     "title": parsed["title"], "abstract": parsed["abstract"],
                     "author_names": [a["display_name"] for a in parsed["authors"] if a.get("display_name")],
                     "status_evidence": status, "identity_conflicts": conflict_evidence,
                     "quality": quality, "primary_research": None, "independent_research_identity": None})
    current_status_rows = [r for r in rows if r["status_evidence"]["status"] not in ("withdrawn", "retracted")]
    text_rows = [r for r in rows if r["quality"]["decision"] == "include"]
    phrases = []
    for phrase in scope["phrases"]:
        one_phrase_mission = {"query": {"arxiv_local_text_scope": {
            "mode": "any_exact_phrase", "fields": ["title", "abstract"], "phrases": [phrase]}}}
        members = [r for r in rows if matches_text_scope(r, one_phrase_mission)]
        phrases.append({"phrase": phrase, "source_record_ids": [r["source_record_id"] for r in members],
                        "publication_series": _series(members, rows, grid, cutoff),
                        "status_projection_series": _series(
                            [r for r in members if r in current_status_rows], current_status_rows, grid, cutoff),
                        "author_signature_observations": _author_observations(members, grid),
                        "classification": "scope_phrase_not_new_technology"})
    report = {
        "version": POLICY["version"], "created_at": started,
        "mission_id": mission["mission_id"], "as_of_date": cutoff.isoformat(),
        "period_from": start.isoformat(), "period_end_exclusive": end.isoformat(),
        "input": {"package_path": str(root), "manifest_bytes_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                  "mission_bytes_sha256": hashlib.sha256(mission_bytes).hexdigest(),
                  "collection_content_sha256": collection_content_hash(manifest),
                  "coverage": collection_coverage(manifest), "local_audit": manifest.get("local_audit"),
                  "upstream_inventory": block.get("upstream_inventory"), "text_scope": scope},
        "effective_policy": policy, "policy_sha256": payload_hash(policy),
        "normalization_policy": identity_policy("conservative"),
        "quality_policy": quality_policy, "quality_policy_sha256": payload_hash(quality_policy),
        "quality_version": QUALITY_VERSION,
        "implementation_files_sha256": code_hashes,
        "counts": {"valid_selected_source_identities": len(rows), "reported_withdrawn_or_retracted": len(rows)-len(current_status_rows),
                   "current_status_not_reported_withdrawn_or_retracted": len(current_status_rows),
                   "quality_include_texts": len(text_rows), "quality_quarantine_texts": sum(r["quality"]["decision"] == "quarantine" for r in rows),
                   "quality_exclude_texts": sum(r["quality"]["decision"] == "exclude" for r in rows),
                   "ambiguous_doi_count": len(conflicts), "independent_studies": None,
                   "confirmed_weak_signals": None},
        "parent_publication_series": _series(rows, rows, grid, cutoff),
        "parent_current_status_projection_series": _series(current_status_rows, current_status_rows, grid, cutoff),
        "phrases": phrases, "records": rows,
        "accuracy_evaluated": False, "database_written": False, "models_used": False,
        "limitations": ["Выгрузка из фиксированного mirror: полный обход файлов не доказывает полноту науки или сопоставимость покрытия.",
                        "Partial пакет сохраняет причины усечения/карантина. Наблюдаемая динамика может объясняться изменением поставки данных.",
                        "Счётчики — уникальные arXiv депозиты, не независимые исследования; связанные повторные тексты могут завышать поддержку.",
                        "Current status projection исключает явно отозванные записи по сегодняшним метаданным, не восстанавливает их статус в прошлом.",
                        "Фразы запроса описывают scope, а не список обнаруженных новых технологий. Нужен отдельный анализ конкретных линий.",
                        "Тексты — текущие версии. Нельзя объявлять этот расчёт прогнозом на прошлую дату.",
                        "Научная первичность, будущая траектория, рынок и независимость команд этим отчётом не доказаны."]}
    # Verify every frozen package file again before publishing the report.
    if manifest_path.read_bytes() != manifest_bytes or mission_path.read_bytes() != mission_bytes:
        raise ValueError("Frozen package changed during observation")
    validate_collection_input(root, manifest, mission, mission_text)
    if any(digest(code_root / name) != sha for name, sha in report["implementation_files_sha256"].items()):
        raise ValueError("Implementation changed during observation")
    report["report_payload_sha256"] = payload_hash(report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-records", type=int, default=2000)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output exists; choose a new immutable report path")
    report = observe_package(args.package, max_records=args.max_records)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({"version": report["version"], "counts": report["counts"],
                      "report_payload_sha256": report["report_payload_sha256"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
