from datetime import date, datetime, timezone

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from saia.local_arxiv_search import validate_inventory
from saia.arxiv_metadata import ORTHOGRAPHIC_MATCHING_VERSION
from saia.thematic_arxiv_cache import (
    anchor_catalog, build_cache, build_target_packs, coverage_for_plan,
    load_cache, select, select_from_target_packs,
)


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


def mirror(tmp_path):
    root = tmp_path / "mirror"
    root.mkdir()
    rows = [
        row("1601.00001", "Machine learning result", "Useful.", date(2016, 1, 1)),
        row("1602.00001", "Machine learning clinical study", "Excluded.", date(2016, 2, 1)),
        row("1603.00001", "Unrelated", "No match.", date(2016, 3, 1)),
    ]
    pq.write_table(pa.Table.from_pylist(rows[:2]), root / "train-00000-of-00002.parquet")
    pq.write_table(pa.Table.from_pylist(rows[2:]), root / "train-00001-of-00002.parquet")
    validate_inventory.cache_clear()
    return root


def plan(phrase="machine learning"):
    return {
        "included_terms": [phrase], "exclusions": ["clinical study"],
        "date_from": "2010-01-01", "as_of_date": "2017-01-01",
    }


def test_catalog_is_internal_acceleration_not_query_authority():
    payload = anchor_catalog()
    assert payload["policy"]["ui_exposed_as_fixed_choices"] is False
    assert payload["policy"]["user_query_may_be_narrowed_automatically"] is False
    assert payload["policy"]["cache_is_query_authority"] is False
    assert any(item["phrase"] == "machine learning" for item in payload["anchors"])
    assert coverage_for_plan(payload, plan())["complete"] is True
    assert coverage_for_plan(payload, plan("quantum banana"))["complete"] is False
    newer = {**plan(), "matching_version": ORTHOGRAPHIC_MATCHING_VERSION}
    assert coverage_for_plan(payload, newer)["complete"] is False


def test_cache_is_immutable_verified_and_reapplies_exact_plan(tmp_path):
    output = tmp_path / "cache"
    source = mirror(tmp_path)
    manifest = build_cache(
        source, output, period_from=date(2010, 1, 1),
        as_of_date=date(2017, 1, 1), expected_files=2, expected_rows=3,
    )
    assert manifest["counts"]["scanned_rows"] == 3
    assert manifest["counts"]["documents"] == 2
    loaded, _ = load_cache(output)
    assert loaded["payload_sha256"] == manifest["payload_sha256"]
    table, audit = select(output, plan())
    assert table.column("id").to_pylist() == ["1601.00001"]
    assert audit["selection_predicate_reapplied"] is True
    assert audit["full_mirror_rows_avoided"] == 3
    packs = tmp_path / "packs"
    pack_manifest = build_target_packs(output, packs)
    assert pack_manifest["interpretation"]["exact_query_predicate_must_be_reapplied"] is True
    packed, packed_audit = select_from_target_packs(output, packs, plan())
    assert packed.column("id").to_pylist() == ["1601.00001"]
    assert packed_audit["selected_target_packs"]
    assert packed_audit["selection_predicate_reapplied"] is True
    with pytest.raises(ValueError, match="already exists"):
        build_cache(source, output, expected_files=2, expected_rows=3)


def test_unknown_query_is_refused_instead_of_silently_narrowed(tmp_path):
    output = tmp_path / "cache"
    build_cache(
        mirror(tmp_path), output, period_from=date(2010, 1, 1),
        as_of_date=date(2017, 1, 1), expected_files=2, expected_rows=3,
    )
    with pytest.raises(ValueError, match="does not cover"):
        select(output, plan("quantum banana"))


def test_older_conflicting_duplicate_is_skipped_and_audited(tmp_path):
    root = tmp_path / "duplicate-mirror"
    root.mkdir()
    newest = row(
        "1601.00009", "Machine learning current metadata", "Useful.",
        date(2016, 6, 1),
    )
    older = row(
        "1601.00009", "Machine learning older metadata", "Useful.",
        date(2015, 6, 1),
    )
    # The pinned snapshot currently orders refreshed metadata before its older
    # duplicate.  A newer conflicting row appearing later is rejected by the
    # builder because a streaming cache cannot silently replace written data.
    pq.write_table(pa.Table.from_pylist([newest]), root / "train-00000-of-00002.parquet")
    pq.write_table(pa.Table.from_pylist([older]), root / "train-00001-of-00002.parquet")
    validate_inventory.cache_clear()
    manifest = build_cache(
        root, tmp_path / "duplicate-cache", period_from=date(2010, 1, 1),
        as_of_date=date(2017, 1, 1), expected_files=2, expected_rows=2,
    )
    assert manifest["counts"]["documents"] == 1
    assert manifest["counts"]["older_conflicting_duplicates_skipped"] == 1
