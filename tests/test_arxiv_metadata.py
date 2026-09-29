import copy
import hashlib
import json
from datetime import datetime

import pytest

pa = pytest.importorskip("pyarrow")
import pyarrow.parquet as pq

from saia.arxiv_metadata import (ORTHOGRAPHIC_MATCHING_VERSION, matches_controlled_plan,
                                 matches_text_scope, records, selected, text_scope,
                                 to_record, version_dates)


def test_controlled_included_terms_are_alternatives_not_required_concepts():
    plan = {"included_terms": ["quantum-inspired", "tensor networks", "compression"],
            "exclusions": []}
    assert matches_controlled_plan(
        {"title": "Quantum-inspired computational fluid dynamics", "abstract": ""}, plan)
    assert matches_controlled_plan(
        {"title": "Neural network compression", "abstract": ""}, plan)
    assert not matches_controlled_plan(
        {"title": "Neuromorphic processors", "abstract": ""}, plan)


def test_approved_orthographic_matching_accepts_separator_variants_but_not_semantic_leaps():
    plan = {"included_terms": ["vision language action"], "exclusions": [],
            "matching_version": ORTHOGRAPHIC_MATCHING_VERSION}
    for title in ("Vision-Language-Action Models", "Vision–Language—Action Models",
                  "Vision_Language/Action Models", "Vision Language Action Models"):
        assert matches_controlled_plan({"title": title, "abstract": ""}, plan)
    assert not matches_controlled_plan(
        {"title": "Visual language action models", "abstract": ""}, plan)
    assert not matches_controlled_plan(
        {"title": "Vision-language", "abstract": "action models"}, plan)
    assert not matches_controlled_plan(
        {"title": "A VLA model for robots", "abstract": ""}, plan)
    assert not matches_controlled_plan(
        {"title": "Vision-language-actionable models", "abstract": ""}, plan)
    # Frozen older plans have no matching_version and keep literal behavior.
    assert not matches_controlled_plan(
        {"title": "Vision-Language-Action Models", "abstract": ""},
        {"included_terms": ["vision language action"], "exclusions": []})
    plan["exclusions"] = ["on robot"]
    assert not matches_controlled_plan(
        {"title": "Vision-Language-Action on-robot deployment", "abstract": ""}, plan)


def enable_text_scope(mission):
    mission['query']['arxiv_local_text_scope'] = {
        'mode': 'any_exact_phrase', 'fields': ['title', 'abstract'],
        'phrases': ['active learning', 'label-efficient learning']}


def test_explicit_phrase_match_is_literal_field_local_and_case_insensitive(tmp_path):
    _, _, mission = fixture(tmp_path)
    assert matches_text_scope(row(), mission)  # terms alone retain old category cohort
    enable_text_scope(mission)
    r = row(); r['title'] = 'ACTIVE  \n learning for segmentation'
    assert selected(r, mission)
    r['title'] = 'inactive learning'; assert not selected(r, mission)
    r['title'] = 'active'; r['abstract'] = 'learning'; assert not selected(r, mission)
    r['abstract'] = 'A label-efficient learning method'; assert selected(r, mission)
    r['abstract'] = 'adaptive labeling'; assert not selected(r, mission)


@pytest.mark.parametrize('bad', [{}, {'mode': 'regex', 'fields': ['title', 'abstract'], 'phrases': ['a']},
    {'mode': 'any_exact_phrase', 'fields': ['title', 'abstract'], 'phrases': []},
    {'mode': 'any_exact_phrase', 'fields': ['title', 'abstract'], 'phrases': [' ']}])
def test_bad_text_scope_fails_before_export(tmp_path, bad):
    source, config, mission = fixture(tmp_path)
    mission['query']['arxiv_local_text_scope'] = bad
    config.write_text(json.dumps(mission))
    with pytest.raises(ValueError, match='text_scope'):
        extract(source, config, tmp_path/'out', min_free_gib=0, verbose=False)
    assert not (tmp_path/'out').exists()


def test_scoped_export_preserves_partition_quarantine_and_scope_fingerprint(tmp_path):
    good = row(); good['title'] = 'Active learning'
    unrelated = row('1612.00002'); unrelated['versions'] = []  # not in text scope, not quarantined
    broken = row('1612.00003'); broken['title'] = 'Active learning'; broken['versions'] = []
    source, config, mission = fixture(tmp_path, [good, unrelated, broken])
    enable_text_scope(mission)
    mission['query']['arxiv_local_snapshot']['record_error_policy'] = 'quarantine'
    config.write_text(json.dumps(mission))
    manifest = extract(source, config, tmp_path/'out', min_free_gib=0, verbose=False)
    audit = manifest['local_audit']
    assert (audit['selected_records'], audit['excluded_by_text_records'], audit['quarantined_records']) == (1, 1, 1)
    assert audit['examined_category_records'] == 3 and manifest['incomplete']
    validate_collection_input(tmp_path/'out', manifest, mission, config.read_text())
    before = collection_content_hash(manifest)
    changed = copy.deepcopy(manifest)
    changed['sources']['arxiv']['selection']['text_scope']['phrases'] = ['other']
    assert collection_content_hash(changed) != before
    with pytest.raises(ValueError, match='Text scope'):
        validate_collection_input(tmp_path/'out', changed, mission, config.read_text())


@pytest.mark.parametrize('cap', [None, 1])
def test_phrase_scope_clean_and_capped_packages_remain_honest(tmp_path, cap):
    first = row(); first['abstract'] = 'An active learning method'
    second = row('1612.00002'); second['title'] = 'Active learning follow-up'
    source, config, mission = fixture(tmp_path, [first, second])
    enable_text_scope(mission)
    config.write_text(json.dumps(mission))
    manifest = extract(source, config, tmp_path/'out', max_records=cap, min_free_gib=0, verbose=False)
    assert bool(manifest['incomplete']) == (cap is not None)
    assert manifest['local_audit']['inventory_traversal_complete'] == (cap is None)
    validate_collection_input(tmp_path/'out', manifest, mission, config.read_text())
    records_read = list(READERS['arxiv'](tmp_path/'out/arxiv/train-00000-of-00001.parquet', mission))
    assert len(records_read) == (1 if cap else 2)
    mutated = copy.deepcopy(manifest)
    del mutated['sources']['arxiv']['selection']['text_scope']
    with pytest.raises(ValueError, match='Text scope'):
        validate_collection_input(tmp_path/'out', mutated, mission, config.read_text())
from saia.arxiv_local import extract, inventory
from saia.ingest import READERS, collection_content_hash, collection_coverage, validate_collection_input
from saia.normalize import parse_arxiv

REVISION = "a" * 40


def row(identifier="1612.00001"):
    return {"id": identifier, "title": "A research contribution", "abstract": "A scientific abstract.",
            "categories": "cs.DC cs.LG", "authors": "A. Smith and B. Li", "authors_parsed": [["Smith", "Alice", ""], ["Li", "Bo", "Jr."]],
            "doi": "10.1000/example", "journal-ref": "A journal", "comments": "12 pages",
            "license": "http://arxiv.org/licenses/nonexclusive-distrib/1.0/",
            "versions": [{"version": "v1", "created": "Thu, 01 Dec 2016 12:00:00 GMT"},
                         {"version": "v2", "created": "Wed, 01 Feb 2017 12:00:00 GMT"}],
            "update_date": datetime(2026, 9, 11)}


def fixture(tmp_path, rows=None):
    source = tmp_path / REVISION / "data"
    source.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(rows if rows is not None else [row()]), source / "train-00000-of-00001.parquet")
    mission = {"mission_id": "local-fixture", "query_version": "local-fixture/v1", "title": "Fixture", "as_of_date": "2017-01-01",
               "sources": ["arxiv"], "period": {"from": "2016-12-01", "to": "2016-12-31"},
               "query": {"terms": ["machine learning"], "arxiv_categories": ["cs.LG"],
                         "arxiv_local_snapshot": {"dataset": "fixture/test", "revision": REVISION,
                                                  "expected_files": 1, "expected_rows": len(rows) if rows is not None else 1}}}
    config = tmp_path / "mission.json"
    config.write_text(json.dumps(mission))
    return source, config, mission


def test_dispatcher_recognizes_new_schema_and_preserves_versions(tmp_path):
    source, _, mission = fixture(tmp_path)
    identifier, payload = list(READERS["arxiv"](next(source.glob("*.parquet")), mission))[0]
    assert identifier == "1612.00001"
    assert payload["created"] == "2016-12-01" and payload["updated"] == "2017-02-01"
    assert payload["_metadata_update_date_raw"] == "2026-09-11T00:00:00"
    assert payload["_arxiv_versions"] == row()["versions"]
    assert payload["_snapshot_revision"] == REVISION
    assert payload["arxiv_primary_category"] is None
    assert [author["name"] for author in payload["author"]] == ["Alice Smith", "Bo Li Jr."]
    normalized = parse_arxiv(payload)
    assert ("arxiv", identifier) in normalized["identifiers"]
    assert normalized["publication_date"] == "2016-12-01"
    assert all(author["external_id"] is None and not author["organisations"] for author in normalized["authors"])
    json.dumps(payload)  # no datetime may leak into JSONB


def test_first_submission_is_not_metadata_update_or_array_order():
    history = list(reversed(row()["versions"]))
    assert version_dates(history) == ("2016-12-01", "2017-02-01")
    assert version_dates([{"version": "v1", "created": "Thu, 01 Dec 2016 00:30:00 +0200"}])[0] == "2016-11-30"


@pytest.mark.parametrize("history", [None, [], [{"version": "v2", "created": "Thu, 01 Dec 2016 12:00:00 GMT"}],
    [{"version": "v1", "created": "bad date"}], [{"version": "v1", "created": "Thu, 01 Dec 2016 12:00:00"}],
    row()["versions"] + [row()["versions"][0]],
    [{"version": "v1", "created": "Wed, 01 Feb 2017 12:00:00 GMT"}, {"version": "v2", "created": "Thu, 01 Dec 2016 12:00:00 GMT"}]])
def test_unresolvable_version_history_is_rejected(history):
    with pytest.raises(ValueError): version_dates(history)


def test_crosslisted_category_and_inclusive_dates_are_selected(tmp_path):
    _, _, mission = fixture(tmp_path)
    assert selected(row(), mission)  # cs.LG is not the first token
    r = row(); r["categories"] = "cs.LGG"
    assert not selected(r, mission)
    r = row(); r["versions"] = [{"version": "v1", "created": "Sat, 31 Dec 2016 23:59:59 GMT"}]
    assert selected(r, mission)
    r["versions"][0]["created"] = "Sun, 01 Jan 2017 00:00:00 GMT"
    assert not selected(r, mission)


def test_missing_parsed_authors_do_not_invent_teams():
    r = row(); r["authors_parsed"] = None
    _, payload = to_record(r, filename="fixture")
    assert payload["author"] == [] and payload["_author_parse_status"] == "unresolved"
    assert payload["_authors_raw"] == r["authors"]


def test_invalid_later_revision_outside_period_does_not_block_unrelated_cohort(tmp_path):
    source, config, mission = fixture(tmp_path)
    irrelevant = row("2410.11838")
    irrelevant["versions"] = [
        {"version": "v1", "created": "Tue, 15 Oct 2024 17:59:04 GMT"},
        {"version": "v2", "created": "Fri, 18 Apr 2025 17:32:22 GMT"},
        {"version": "v3", "created": "Thu, 10 Apr 2025 16:43:52 GMT"}]
    assert not selected(irrelevant, mission)
    pq.write_table(pa.Table.from_pylist([row(), irrelevant]), next(source.glob("*.parquet")))
    mission["query"]["arxiv_local_snapshot"]["expected_rows"] = 2
    config.write_text(json.dumps(mission))
    manifest = extract(source, config, tmp_path / "output", min_free_gib=0, verbose=False)
    assert manifest["local_audit"]["selected_records"] == 1 and not manifest["incomplete"]


def test_new_adapter_activates_existing_post_cutoff_quarantine():
    from saia.quality import decide
    _, payload = to_record(row(), filename="fixture")
    result = decide(title=payload["title"], abstract=payload["summary"], publication_year=2016,
                    date_is_imprecise=False, terms=["machine learning"], categories=payload["categories"],
                    sources={"arxiv"}, openalex_payloads=[], arxiv_payloads=[payload], as_of_date="2017-01-01")
    assert result.flags["content_revision_after_cutoff"] is True
    assert result.decision == "quarantine"


def test_unknown_parquet_schema_fails_instead_of_silently_producing_zero(tmp_path):
    path = tmp_path / "unknown.parquet"
    pq.write_table(pa.Table.from_pylist([{"some_other_field": "value"}]), path)
    with pytest.raises(ValueError, match="schema"): list(READERS["arxiv"](path, {}))


def test_package_has_verified_subset_and_immutable_originals(tmp_path):
    source, config, mission = fixture(tmp_path)
    input_path = next(source.glob("*.parquet"))
    before = hashlib.sha256(input_path.read_bytes()).hexdigest()
    output = tmp_path / "output"
    manifest = extract(source, config, output, min_free_gib=0, verbose=False)
    assert before == hashlib.sha256(input_path.read_bytes()).hexdigest()
    assert not manifest["incomplete"] and manifest["local_audit"]["selected_records"] == 1
    assert manifest["local_audit"]["selected_revised_after_cutoff"] == 1
    assert manifest["local_audit"]["weak_signal_precision"] is None
    validate_collection_input(output, manifest, mission, config.read_text())
    assert (output / "mission.json").read_bytes() == config.read_bytes()
    block = manifest["sources"]["arxiv"]
    assert block["files"][0]["http_status"] is None
    assert block["files"][0]["upstream_sha256"] == before
    coverage = collection_coverage(manifest)
    assert coverage["source_modes"]["arxiv"]["upstream_inventory_sha256"] == block["upstream_inventory_sha256"]
    changed = copy.deepcopy(manifest)
    changed["sources"]["arxiv"]["upstream_inventory_sha256"] = "different"
    assert collection_content_hash(changed) != collection_content_hash(manifest)
    with pytest.raises(ValueError, match="not empty"): extract(source, config, output, min_free_gib=0)


def test_cap_is_explicitly_partial_never_complete(tmp_path):
    source, config, _ = fixture(tmp_path, [row(), row("1612.00002")])
    manifest = extract(source, config, tmp_path / "output", max_records=1, min_free_gib=0, verbose=False)
    assert manifest["local_audit"]["cap_reached"] and manifest["incomplete"]["arxiv"]
    assert list(records(tmp_path / "output/arxiv/train-00000-of-00001.parquet"))[0][0] == "1612.00001"


@pytest.mark.parametrize("change", ["revision", "count", "rows", "schema"])
def test_inventory_rejects_unproven_or_incomplete_input_before_output(tmp_path, change):
    source, config, mission = fixture(tmp_path)
    policy = mission["query"]["arxiv_local_snapshot"]
    if change == "revision": policy["revision"] = "b" * 40
    if change == "count": policy["expected_files"] = 2
    if change == "rows": policy["expected_rows"] = 5
    if change == "schema": pq.write_table(pa.Table.from_pylist([{"id": "1612.00001"}]), next(source.glob("*.parquet")))
    config.write_text(json.dumps(mission))
    with pytest.raises(ValueError): extract(source, config, tmp_path / "output", min_free_gib=0, verbose=False)
    assert not (tmp_path / "output").exists()


def test_disk_reserve_failure_precedes_output(tmp_path):
    source, config, _ = fixture(tmp_path)
    with pytest.raises(ValueError, match="disk reserve"):
        extract(source, config, tmp_path / "output", min_free_gib=10**9)
    assert not (tmp_path / "output").exists()


def test_duplicate_selected_ids_cannot_be_declared_complete(tmp_path):
    source, config, _ = fixture(tmp_path, [row(), row()])
    with pytest.raises(ValueError, match="Duplicate selected"): extract(source, config, tmp_path / "output", min_free_gib=0, verbose=False)
    assert not (tmp_path / "output/manifest.json").exists()


def test_invalid_dates_cannot_be_declared_complete(tmp_path):
    bad = row(); bad["versions"][0]["created"] = "bad date"
    source, config, _ = fixture(tmp_path, [bad])
    with pytest.raises(ValueError, match="version date"): extract(source, config, tmp_path / "output", min_free_gib=0, verbose=False)
    assert not (tmp_path / "output/manifest.json").exists()


def quarantine_fixture(tmp_path, rows, limit=100):
    source, config, mission = fixture(tmp_path, rows)
    mission["query"]["arxiv_local_snapshot"].update(record_error_policy="quarantine", max_quarantined_records=limit)
    config.write_text(json.dumps(mission))
    return source, config, mission


def broken_revision(identifier="1612.00002"):
    bad = row(identifier)
    bad["versions"].append({"version": "v3", "created": "Mon, 02 Jan 2017 12:00:00 GMT"})
    return bad


def test_opt_in_quarantine_keeps_valid_data_and_binds_rejected_evidence(tmp_path):
    bad_date = row("1612.00003"); bad_date["versions"][0]["created"] = "bad date"
    outside_category = row("1612.00004"); outside_category["categories"] = "cs.DC"
    outside_period = row("1702.00001")
    outside_period["versions"] = [{"version": "v1", "created": "Wed, 01 Feb 2017 12:00:00 GMT"}]
    source, config, mission = quarantine_fixture(tmp_path, [outside_category, row(), broken_revision(), bad_date, outside_period])
    before = hashlib.sha256(next(source.glob("*.parquet")).read_bytes()).hexdigest()
    output = tmp_path / "output"
    manifest = extract(source, config, output, min_free_gib=0, verbose=False)
    validate_collection_input(output, manifest, mission, config.read_text())
    assert before == hashlib.sha256(next(source.glob("*.parquet")).read_bytes()).hexdigest()
    assert manifest["connector_version"] == "arxiv-local-export-0.4.4"
    assert manifest["incomplete"]["arxiv"] and manifest["local_audit"]["inventory_traversal_complete"]
    audit = manifest["local_audit"]
    assert (audit["examined_category_records"], audit["selected_records"], audit["excluded_by_period_records"], audit["quarantined_records"]) == (4, 1, 1, 2)
    rejected = [json.loads(line) for line in (output / "record-quarantine.jsonl").read_text().splitlines()]
    assert [item["upstream_row_zero_based"] for item in rejected] == [2, 3]
    assert [item["scope_membership"] for item in rejected] == ["selected", "unknown_period"]
    assert rejected[0]["raw_metadata"]["versions"] == broken_revision()["versions"]
    assert len(list(READERS["arxiv"](next((output / "arxiv").glob("*.parquet")), mission))) == 1
    changed = copy.deepcopy(manifest)
    changed["sources"]["arxiv"]["record_quarantine"]["sha256"] = "different"
    assert collection_content_hash(changed) != collection_content_hash(manifest)


@pytest.mark.parametrize("tamper", ["missing", "bytes", "path", "counter", "raw_hash", "reason", "complete", "partition", "policy"])
def test_quarantine_integrity_is_verified_before_import(tmp_path, tamper):
    source, config, mission = quarantine_fixture(tmp_path, [row(), broken_revision()])
    output = tmp_path / "output"
    manifest = extract(source, config, output, min_free_gib=0, verbose=False)
    entry = manifest["sources"]["arxiv"]["record_quarantine"]
    path = output / entry["file"]
    if tamper == "missing": path.unlink()
    if tamper == "bytes": path.write_text("changed")
    if tamper == "path": entry["file"] = "../record-quarantine.jsonl"
    if tamper == "counter": entry["records"] = 0
    if tamper == "complete": manifest["incomplete"] = {}
    if tamper == "partition": manifest["local_audit"]["examined_category_records"] += 1
    if tamper == "policy": del manifest["sources"]["arxiv"]["record_quarantine"]
    if tamper in ("raw_hash", "reason"):
        data = json.loads(path.read_text())
        data["raw_metadata_sha256" if tamper == "raw_hash" else "reason"] = "changed"
        path.write_text(json.dumps(data) + "\n")
        entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError): validate_collection_input(output, manifest, mission, config.read_text())


def test_empty_quarantine_still_has_verified_policy_without_false_partial(tmp_path):
    source, config, mission = quarantine_fixture(tmp_path, [row()])
    output = tmp_path / "output"
    manifest = extract(source, config, output, min_free_gib=0, verbose=False)
    validate_collection_input(output, manifest, mission, config.read_text())
    assert not manifest["incomplete"]
    assert manifest["sources"]["arxiv"]["record_quarantine"]["records"] == 0


def test_record_quarantine_does_not_swallow_duplicate_package_identity(tmp_path):
    source, config, _ = quarantine_fixture(tmp_path, [row(), row()])
    with pytest.raises(ValueError, match="Duplicate selected"):
        extract(source, config, tmp_path / "output", min_free_gib=0, verbose=False)
    assert not (tmp_path / "output/manifest.json").exists()


def test_quarantine_limit_failure_and_all_invalid_leave_no_completion_marker(tmp_path):
    source, config, _ = quarantine_fixture(tmp_path, [row(), broken_revision(), broken_revision("1612.00003")], limit=1)
    with pytest.raises(ValueError, match="limit exceeded"):
        extract(source, config, tmp_path / "output", min_free_gib=0, verbose=False)
    assert not (tmp_path / "output/manifest.json").exists()
    source2, config2, _ = quarantine_fixture(tmp_path / "second", [broken_revision()])
    with pytest.raises(ValueError, match="Empty selection"):
        extract(source2, config2, tmp_path / "second/output", min_free_gib=0, verbose=False)
    assert not (tmp_path / "second/output/manifest.json").exists()


def test_cap_and_quarantine_are_distinct_incomplete_reasons(tmp_path):
    source, config, mission = quarantine_fixture(tmp_path, [broken_revision(), row(), row("1612.00003")])
    output = tmp_path / "output"
    manifest = extract(source, config, output, max_records=1, min_free_gib=0, verbose=False)
    validate_collection_input(output, manifest, mission, config.read_text())
    assert len(manifest["incomplete"]["arxiv"]) == 2
    assert not manifest["local_audit"]["inventory_traversal_complete"]
    assert manifest["local_audit"]["examined_category_records"] == 2


def test_real_2410_11838_date_pattern_is_preserved_not_repaired(tmp_path):
    # This exact contradiction exists on the official arXiv metadata page;
    # it is not evidence that the study itself is noise or invalid research.
    bad = row("2410.11838")
    bad["versions"] = [
        {"version": "v1", "created": "Tue, 15 Oct 2024 17:59:04 GMT"},
        {"version": "v2", "created": "Fri, 18 Apr 2025 17:32:22 GMT"},
        {"version": "v3", "created": "Thu, 10 Apr 2025 16:43:52 GMT"}]
    good = row("2410.00001")
    good["versions"] = [bad["versions"][0]]
    source, config, mission = quarantine_fixture(tmp_path, [bad, good])
    mission.update(as_of_date="2026-09-01", period={"from": "2024-09-01", "to": "2026-08-31"})
    config.write_text(json.dumps(mission))
    output = tmp_path / "output"
    manifest = extract(source, config, output, min_free_gib=0, verbose=False)
    validate_collection_input(output, manifest, mission, config.read_text())
    rejected = json.loads((output / "record-quarantine.jsonl").read_text())
    assert rejected["raw_metadata"]["versions"] == bad["versions"]
    assert rejected["scope_membership"] == "selected"
    assert manifest["local_audit"]["selected_records"] == 1


def test_quarantine_row_location_remains_exact_across_batch_boundary(tmp_path):
    rows = [row(f"1612.{i:05d}") for i in range(4096)] + [broken_revision("1612.04096")]
    source, config, mission = quarantine_fixture(tmp_path, rows)
    output = tmp_path / "output"
    manifest = extract(source, config, output, min_free_gib=0, verbose=False)
    validate_collection_input(output, manifest, mission, config.read_text())
    rejected = json.loads((output / "record-quarantine.jsonl").read_text())
    assert rejected["upstream_row_zero_based"] == 4096
    assert manifest["local_audit"]["selected_records"] == 4096
