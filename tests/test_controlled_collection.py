import hashlib
import json
from datetime import date, datetime, timezone

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from saia.controlled_collection import (build_package, canonical_json,
                                         derive_arxiv_profile,
                                         export_selected_arxiv, inventory_sha256)
from saia.ingest import validate_collection_input
from saia.local_arxiv_search import validate_inventory
from saia.query_expansion import compile_plan, digest
from saia.thematic_arxiv_cache import build_cache, build_target_packs


def row(identifier, title, abstract, created):
    stamp = datetime.combine(created, datetime.min.time(), tzinfo=timezone.utc)
    return {
        "id": identifier, "title": title, "abstract": abstract,
        "categories": "cs.LG", "versions": [{"version": "v1", "created":
        stamp.strftime("%a, %d %b %Y %H:%M:%S %z")}],
        "authors_parsed": [["Doe", "Jane", ""]], "doi": None,
        "journal-ref": None, "comments": None, "update_date": created,
        "authors": "Doe, Jane", "license": None,
    }


def base_payload():
    plan = compile_plan("machine learning", ["gradient descent"], ["clinical study"],
                        date(2010, 1, 1), date(2017, 1, 1))
    return {
        "mission_id": "ml-test", "query_version": "ml-test/v3",
        "title": "test", "question": "test", "as_of_date": "2017-01-01",
        "period": {"from": "2010-01-01", "to": "2016-12-31"},
        "sources": ["arxiv", "openalex"],
        "query": {"terms": plan["included_terms"], "exclusions": plan["exclusions"]},
        "controlled_search_plan": plan,
    }


def profile():
    return derive_arxiv_profile(
        base_payload(), "ml-test/v4", "fixture reviewer",
        {"source_reported_count": 123, "observed_at": "2026-09-21T00:00:00+00:00"},
    )


def mirror(tmp_path):
    root = tmp_path / "mirror"
    root.mkdir(exist_ok=True)
    rows = [
        row("1601.00001", "Machine learning result", "Useful.", date(2016, 1, 1)),
        row("1602.00001", "Machine learning clinical study", "Excluded.", date(2016, 2, 1)),
        row("1701.00001", "Gradient descent future", "Too late.", date(2017, 1, 2)),
        row("1501.00001", "Unrelated", "No match.", date(2015, 1, 1)),
    ]
    first = root / "train-00000-of-00002.parquet"
    second = root / "train-00001-of-00002.parquet"
    if not first.exists():
        pq.write_table(pa.Table.from_pylist(rows[:2]), first)
        pq.write_table(pa.Table.from_pylist(rows[2:]), second)
    validate_inventory.cache_clear()
    return root


def test_profile_is_explicit_new_version_and_preserves_base():
    base = base_payload()
    original = json.loads(json.dumps(base))
    derived = profile()
    assert base == original
    assert derived["query_version"] == "ml-test/v4"
    assert derived["sources"] == ["arxiv"]
    assert derived["collection_profile"]["base_query_version_id"] == "ml-test/v3"
    assert derived["controlled_search_plan"] == base["controlled_search_plan"]


def test_export_preserves_all_and_only_exact_eligible_rows(tmp_path):
    output = tmp_path / "selected.parquet"
    audit = export_selected_arxiv(mirror(tmp_path), output, profile(),
                                  expected_files=2, expected_rows=4)
    assert audit["scanned_rows"] == 4
    assert audit["selected_records"] == 1
    assert audit["unique_arxiv_ids"] == 1
    assert len(audit["inventory_sha256"]) == 64
    assert pq.read_table(output).column("id").to_pylist() == ["1601.00001"]
    assert audit["output_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()


def test_export_uses_verified_thematic_pack_only_when_complete(tmp_path):
    source = mirror(tmp_path)
    cache = tmp_path / "thematic-cache"
    packs = tmp_path / "thematic-packs"
    build_cache(
        source, cache, period_from=date(2010, 1, 1),
        as_of_date=date(2017, 1, 1), expected_files=2, expected_rows=4,
    )
    build_target_packs(cache, packs)
    mission = profile()
    mission["controlled_search_plan"]["included_terms"] = ["machine learning"]
    mission["controlled_search_plan"].pop("matching_version")  # historical literal plan
    output = tmp_path / "selected-from-cache.parquet"
    audit = export_selected_arxiv(
        source, output, mission, expected_files=2, expected_rows=4,
        thematic_cache_dir=cache, thematic_packs_dir=packs,
    )
    assert audit["selection_engine"] == "verified_thematic_target_pack"
    assert audit["selected_target_packs"]
    assert audit["selection_predicate_reapplied"] is True
    assert pq.read_table(output).column("id").to_pylist() == ["1601.00001"]


def test_export_falls_back_to_full_mirror_for_uncovered_phrase(tmp_path):
    source = mirror(tmp_path)
    cache = tmp_path / "thematic-cache"
    packs = tmp_path / "thematic-packs"
    build_cache(
        source, cache, period_from=date(2010, 1, 1),
        as_of_date=date(2017, 1, 1), expected_files=2, expected_rows=4,
    )
    build_target_packs(cache, packs)
    mission = profile()
    mission["controlled_search_plan"]["included_terms"] = ["unrelated"]
    mission["controlled_search_plan"].pop("matching_version")  # historical literal plan
    output = tmp_path / "selected-from-full-mirror.parquet"
    audit = export_selected_arxiv(
        source, output, mission, expected_files=2, expected_rows=4,
        thematic_cache_dir=cache, thematic_packs_dir=packs,
    )
    assert audit["selection_engine"] == "full_pinned_mirror_scan"
    assert audit["thematic_cache_not_used_reason"] == (
        "approved_include_phrases_not_fully_indexed"
    )
    assert audit["scanned_rows"] == 4
    assert pq.read_table(output).column("id").to_pylist() == ["1501.00001"]


def test_new_orthographic_plan_bypasses_literal_cache_even_for_indexed_phrase(tmp_path):
    source = mirror(tmp_path)
    cache = tmp_path / "thematic-cache"
    packs = tmp_path / "thematic-packs"
    build_cache(source, cache, period_from=date(2010, 1, 1),
                as_of_date=date(2017, 1, 1), expected_files=2, expected_rows=4)
    build_target_packs(cache, packs)
    mission = profile()
    mission["controlled_search_plan"]["included_terms"] = ["machine learning"]
    audit = export_selected_arxiv(
        source, tmp_path / "selected-new-version.parquet", mission,
        expected_files=2, expected_rows=4,
        thematic_cache_dir=cache, thematic_packs_dir=packs,
    )
    assert audit["selection_engine"] == "full_pinned_mirror_scan"
    assert audit["thematic_cache_not_used_reason"] == (
        "orthographic_matching_not_covered_by_literal_cache")


def test_package_manifest_exposes_thematic_selection_provenance(tmp_path, monkeypatch):
    mission = profile()
    mission_path = tmp_path / "mission-source.json"
    mission_path.write_text(canonical_json(mission))
    audit = {
        "selection_engine": "verified_thematic_target_pack",
        "cache_manifest_sha256": "a" * 64,
        "target_pack_manifest_sha256": "b" * 64,
        "selected_target_packs": ["fixture-target"],
        "selection_predicate_reapplied": True,
        "adapter_version": "fixture", "dataset": "fixture", "revision": "fixture",
        "inventory_files": 2, "inventory_rows": 4, "inventory_sha256": "c" * 64,
        "selected_records": 1, "output_sha256": None,
    }

    def fake_export(_mirror, destination, _mission, **_kwargs):
        pq.write_table(pa.Table.from_pylist([
            row("1601.00001", "Machine learning result", "Useful.", date(2016, 1, 1))
        ]), destination)
        audit["output_sha256"] = hashlib.sha256(destination.read_bytes()).hexdigest()
        return audit

    monkeypatch.setattr("saia.controlled_collection.export_selected_arxiv", fake_export)
    result = build_package(mission_path, mirror(tmp_path), tmp_path / "package",
                           expected_files=2, expected_rows=4)
    selection = result["manifest"]["sources"]["arxiv"]["selection"]
    assert selection["selection_engine"] == "verified_thematic_target_pack"
    assert selection["selected_target_packs"] == ["fixture-target"]
    assert selection["selection_predicate_reapplied"] is True


def test_build_package_is_canonical_and_refuses_overwrite(tmp_path):
    mission = profile()
    mission_path = tmp_path / "mission-source.json"
    mission_path.write_text(canonical_json(mission))
    output = tmp_path / "package"
    result = build_package(mission_path, mirror(tmp_path), output,
                           expected_files=2, expected_rows=4)
    manifest = result["manifest"]
    assert manifest["mission_file_sha256"] == digest(mission)
    assert manifest["sources"]["arxiv"]["records_are_selected_cohort"] is True
    assert manifest["sources"]["arxiv"]["files"][0]["records"] == 1
    validate_collection_input(output, manifest, mission,
                              (output / "mission.json").read_text())
    with pytest.raises(ValueError, match="already exists"):
        build_package(mission_path, mirror(tmp_path), output,
                      expected_files=2, expected_rows=4)


def test_validation_refuses_missing_declared_source_and_wrong_plan_hash(tmp_path):
    mission = profile()
    text = canonical_json(mission)
    root = tmp_path / "root"
    (root / "arxiv").mkdir(parents=True)
    raw = root / "arxiv" / "selected.parquet"
    raw.write_bytes(b"fixture")
    entry = {"file": raw.name, "records": 0,
             "sha256": hashlib.sha256(raw.read_bytes()).hexdigest()}
    manifest = {
        "mission_id": mission["mission_id"], "query_version": mission["query_version"],
        "mission_file_sha256": digest(mission),
        "sources": {"arxiv": {"access_mode": "local-arxiv-metadata-parquet",
                               "selection": {"controlled_search_plan_sha256": "bad"},
                               "files": [entry]}},
    }
    with pytest.raises(ValueError, match="План отбора"):
        validate_collection_input(root, manifest, mission, text)
    mission["sources"] = ["arxiv", "openalex"]
    manifest["mission_file_sha256"] = digest(mission)
    with pytest.raises(ValueError, match="Источники пакета"):
        validate_collection_input(root, manifest, mission, canonical_json(mission))


def test_noncanonical_mission_is_rejected(tmp_path):
    mission_path = tmp_path / "mission.json"
    mission_path.write_text(json.dumps(profile(), indent=2, ensure_ascii=False))
    with pytest.raises(ValueError, match="canonical"):
        build_package(mission_path, mirror(tmp_path), tmp_path / "package",
                      expected_files=2, expected_rows=4)


def test_inventory_identity_changes_with_content_addressed_blob(tmp_path):
    root = mirror(tmp_path)
    files = tuple(sorted(root.glob("*.parquet")))
    cfg = {"dataset": "fixture", "revision": "one"}
    first = inventory_sha256(files, 4, cfg)
    assert first == inventory_sha256(files, 4, cfg)
    assert first != inventory_sha256(files, 4, {**cfg, "revision": "two"})


def test_export_stops_before_approved_record_limit_and_removes_partial_file(tmp_path):
    root = tmp_path / "large-mirror"
    root.mkdir()
    rows = [
        row("1601.00001", "Machine learning first", "Useful.", date(2016, 1, 1)),
        row("1601.00002", "Machine learning second", "Useful.", date(2016, 1, 2)),
    ]
    pq.write_table(pa.Table.from_pylist(rows), root / "train-00000-of-00001.parquet")
    validate_inventory.cache_clear()
    output = tmp_path / "partial-must-not-survive.parquet"
    with pytest.raises(ValueError, match="approved limit of 1"):
        export_selected_arxiv(
            root, output, profile(), expected_files=1, expected_rows=2,
            max_records=1,
        )
    assert not output.exists()
