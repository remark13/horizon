import hashlib
import json
from datetime import date, datetime, timezone

import pyarrow as pa
import pyarrow.parquet as pq

import saia.arxiv_parent_corpus as parent
from saia.controlled_collection import sha256_file


def row(identifier, title, created):
    stamp = datetime.combine(created, datetime.min.time(), tzinfo=timezone.utc)
    return {
        "id": identifier, "title": title, "abstract": "Example abstract",
        "categories": "cs.LG",
        "versions": [{"version": "v1", "created": stamp.strftime("%a, %d %b %Y %H:%M:%S %Z")}],
    }


def test_controlled_parent_audit_only_rechecks_sealed_selected_ids(tmp_path, monkeypatch):
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    source = mirror / "train-00000-of-00001.parquet"
    rows = [
        row("2401.00001", "Machine learning for manufacturing", date(2024, 1, 1)),
        row("2401.00002", "Unrelated topic", date(2024, 1, 2)),
        row("2402.00001", "Robot learning for factories", date(2024, 2, 1)),
        row("2402.00002", "Another unrelated topic", date(2024, 2, 2)),
    ]
    pq.write_table(pa.Table.from_pylist(rows), source)
    package = tmp_path / "package"
    package.mkdir()
    (package / "arxiv").mkdir()
    selected = package / "arxiv" / "selected.parquet"
    pq.write_table(pa.Table.from_pylist([rows[0], rows[2]]), selected)
    mission = {
        "mission_id": "controlled-test", "as_of_date": "2024-03-01",
        "period": {"from": "2024-01-01", "to": "2024-02-29"},
        "query": {"arxiv_categories": []},
        "controlled_search_plan": {
            "included_terms": ["machine learning", "robot learning"],
            "exclusions": [],
        },
    }
    mission_path = package / "mission.json"
    mission_path.write_text(json.dumps(mission), encoding="utf-8")
    manifest = {
        "mission_id": mission["mission_id"],
        "mission_file_sha256": hashlib.sha256(mission_path.read_bytes()).hexdigest(),
        "sources": {"arxiv": {
            "dataset_revision": "test", "upstream_inventory_sha256": "frozen",
            "total_records": 2,
            "files": [{"file": selected.name, "sha256": sha256_file(selected)}],
        }},
    }
    manifest_path = package / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(parent, "local_policy", lambda: {
        "revision": "test", "expected_files": 1, "expected_rows": 4,
    })
    monkeypatch.setattr(parent, "validate_inventory", lambda *_: (source,))
    monkeypatch.setattr(parent, "inventory_sha256", lambda *_: "frozen")
    original = parent.matches_controlled_plan
    checked_ids = []

    def checked(row, plan):
        checked_ids.append(row["id"])
        return original(row, plan)

    monkeypatch.setattr(parent, "matches_controlled_plan", checked)
    report = parent.audit(mirror, mission_path, manifest_path)
    assert report["counts"]["parent_native_ids_in_period"] == 4
    assert report["counts"]["phrase_scope_native_ids_in_period"] == 2
    assert set(checked_ids) == {"2401.00001", "2402.00001"}
