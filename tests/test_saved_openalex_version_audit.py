import hashlib
import json

import pytest

from scripts.audit_saved_openalex_versions import audit


def fixture(tmp_path):
    package = tmp_path / "package"
    (package / "openalex").mkdir(parents=True)
    rows = [
        {"id": "https://openalex.org/W1", "display_name": "Same study",
         "publication_date": "2026-01-01", "doi": "https://doi.org/10.6084/m9.figshare.123.v1",
         "type": "supplementary-materials", "primary_location": {"source": {"display_name": "Figshare"}},
         "authorships": [{"author": {"display_name": "Alice Smith"}},
                         {"author": {"display_name": "Bob Jones"}}]},
        {"id": "https://openalex.org/W2", "display_name": "Same study",
         "publication_date": "2026-01-01", "doi": "https://doi.org/10.6084/m9.figshare.123",
         "type": "supplementary-materials", "primary_location": {"source": {"display_name": "Figshare"}},
         "authorships": [{"author": {"display_name": "Alice Smith"}},
                         {"author": {"display_name": "Bob Jones"}}]},
    ]
    page = json.dumps({"results": rows}).encode()
    (package / "openalex" / "page_0001.json").write_bytes(page)
    manifest = {"mission_id": "mission", "sources": {"openalex": {
        "total_records": 2, "files": [{"file": "page_0001.json", "records": 2,
                                       "sha256": hashlib.sha256(page).hexdigest()}]}}}
    (package / "manifest.json").write_text(json.dumps(manifest))
    packet = {"version": "top15-full-composition-review-packet-v1", "items": [
        {"item_id": "one", "direction": "Ферментация", "generated_label": "Topic",
         "works": [{"title": "Same study", "sources": ["https://openalex.org/W1"]},
                   {"title": "Same study", "sources": ["https://openalex.org/W2"]}]},
        {"item_id": "two", "direction": "БАС", "generated_label": "Other",
         "works": [{"title": "UAV", "sources": ["https://arxiv.org/abs/2601.00001"]}]},
    ]}
    return package, packet


def test_audit_scopes_packet_to_matching_direction_and_preserves_raw_records(tmp_path):
    package, packet = fixture(tmp_path)
    result = audit(package, packet, "Ферментация")
    assert result["records"] == 2
    assert len(result["version_families"]["families"]) == 1
    assert len(result["review_packet_cards"]) == 1
    card = result["review_packet_cards"][0]
    assert card["works_shown"] == 2
    assert card["sensitivity_units_after_supporting_type_and_explicit_alias_rules"] == 0
    assert len(card["non_standalone_record_ids"]) == 2
    assert card["missing_from_raw_package"] == []


def test_audit_rejects_wrong_direction_missing_record_and_tampered_page(tmp_path):
    package, packet = fixture(tmp_path)
    with pytest.raises(ValueError, match="Explicit matching"):
        audit(package, packet)
    packet["items"][0]["works"][0]["sources"] = ["https://openalex.org/W3"]
    with pytest.raises(ValueError, match="missing from source"):
        audit(package, packet, "Ферментация")
    (package / "openalex" / "page_0001.json").write_text("{}")
    with pytest.raises(ValueError, match="hash mismatch"):
        audit(package, direction="Ферментация")


def test_already_merged_openalex_ids_count_as_one_work(tmp_path):
    package, packet = fixture(tmp_path)
    packet["items"][0]["works"] = [
        {"title": "Same study", "sources": ["https://openalex.org/W1",
                                             "https://openalex.org/W2"]}]
    card = audit(package, packet, "Ферментация")["review_packet_cards"][0]
    assert card["works_shown"] == 1
    assert card["same_title_extra_memberships"] == 0
    assert card["explicit_version_family_extra_memberships"] == 0
    assert card["sensitivity_units_after_supporting_type_and_explicit_alias_rules"] == 0
