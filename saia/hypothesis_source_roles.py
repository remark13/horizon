"""Validate evidence-role routing for a frozen cross-domain pilot.

The map tells analysts which kind of source could support a claim. It neither
claims that such a source is connected nor converts a search area to a signal.
"""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path

from saia.controlled_collection import sha256_file


TYPES = {
    "scientific_method_material", "technical_application",
    "product_market", "regulatory_infrastructure",
}
EXTERNAL_ROLES = {
    "patent", "research_grant", "clinical_trial", "commercial_event",
    "investment_deal", "program_or_regulation",
}
SCIENTIFIC_ROLES = {"landscape", "technical_mechanism", "technical_background"}


def audit(config_path: Path, catalog_path: Path, pilot_path: Path) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    pilot = json.loads(pilot_path.read_text(encoding="utf-8"))
    if (config.get("version") != "goal-pilot-hypothesis-source-roles-v1"
            or catalog.get("version") != "priority-catalog-v1"
            or pilot.get("version") != "goal-cross-domain-compound-pilot-v1"):
        raise ValueError("Unexpected source-role, catalog or pilot version")
    expected = {
        row["case_id"]: row for row in pilot["cases"]
        if row["role"] in {"national_search_area", "customer_supplied_example"}
    }
    if (len(expected) != 16 or len(config.get("rows") or []) != 16
            or len({row["id"] for row in config["rows"]}) != 16
            or {row["id"] for row in config["rows"]} != set(expected)):
        raise ValueError("Role map does not cover the fixed 16 real pilot cases")
    national = {row["id"]: row for row in catalog["national_search_areas"]}
    customer = {row["id"]: row for row in catalog["customer_examples"]}
    direction_ids = set()
    customer_area_ids = set()
    counts = Counter()
    for row in config["rows"]:
        identifier = row["id"]
        allowed_keys = {"id", "primary_type", "secondary_types",
                        "scientific_role", "priority_external_roles"}
        if set(row) != allowed_keys:
            raise ValueError(f"Unexpected source-role fields: {identifier}")
        primary = row["primary_type"]
        secondary = row["secondary_types"]
        external = row["priority_external_roles"]
        scientific = row["scientific_role"]
        if (primary not in TYPES or not isinstance(secondary, list)
                or not set(secondary) <= TYPES - {primary}
                or len(secondary) != len(set(secondary))
                or scientific not in SCIENTIFIC_ROLES
                or not isinstance(external, list) or not external
                or not set(external) <= EXTERNAL_ROLES
                or len(external) != len(set(external))):
            raise ValueError(f"Invalid evidence-role classification: {identifier}")
        if identifier in national:
            original = national[identifier]
            if (original["role"] != "search_area_not_signal"
                    or expected[identifier]["role"] != "national_search_area"
                    or expected[identifier]["direction_id"] != original["direction_id"]
                    or scientific != "landscape"):
                raise ValueError(f"Search area was treated as a signal: {identifier}")
            direction_ids.add(original["direction_id"])
            counts["national_search_area"] += 1
        elif identifier in customer:
            original = customer[identifier]
            if (original["role"] != "customer_supplied_signal_example"
                    or expected[identifier]["role"] != "customer_supplied_example"
                    or expected[identifier]["client_area_id"] != original["client_area_id"]
                    or scientific == "landscape"):
                raise ValueError(f"Customer example role mismatch: {identifier}")
            if primary == "product_market" and "commercial_event" not in external:
                raise ValueError(f"Market example lacks commercial evidence role: {identifier}")
            customer_area_ids.add(original["client_area_id"])
            counts["customer_supplied_example"] += 1
        else:
            raise ValueError(f"Unknown catalog ID: {identifier}")
        counts[primary] += 1
    if (direction_ids != {row["id"] for row in catalog["directions"]}
            or customer_area_ids != {row["id"] for row in catalog["client_search_areas"]}):
        raise ValueError("Pilot roles do not cover all ten and six source areas")
    return {
        "version": "goal-pilot-hypothesis-source-role-audit-v1",
        "config_sha256": sha256_file(config_path),
        "catalog_sha256": sha256_file(catalog_path),
        "pilot_sha256": sha256_file(pilot_path),
        "counts": dict(sorted(counts.items())),
        "ten_directions": len(direction_ids),
        "six_customer_areas": len(customer_area_ids),
        "role_of_map": "expected_evidence_not_source_availability_or_signal_confirmation",
    }
