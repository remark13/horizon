"""Audit publication-version candidates in a sealed OpenAlex page package.

This is read-only. It neither changes canonical works nor adjusts saved scores.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

from saia.normalize import restore_abstract, title_key
from saia.publication_versions import find_version_families


VERSION = "saved-openalex-version-audit-v3"
NON_STANDALONE_TYPES = frozenset({"peer-review", "erratum", "supplementary-materials"})


def audit(package: Path, packet: dict | None = None,
          direction: str | None = None) -> dict:
    manifest_path = package / "manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    source = (manifest.get("sources") or {}).get("openalex") or {}
    files = source.get("files") or []
    if not files or source.get("total_records") is None:
        raise ValueError("OpenAlex manifest lacks frozen pages/count")
    records = []
    page_hashes = []
    for item in files:
        path = package / "openalex" / item["file"]
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != item["sha256"]:
            raise ValueError(f"Frozen page hash mismatch: {path.name}")
        payload = json.loads(data)
        rows = payload.get("results") or []
        if len(rows) != item["records"]:
            raise ValueError(f"Frozen page count mismatch: {path.name}")
        records.extend(rows)
        page_hashes.append({"file": item["file"], "sha256": digest,
                            "records": len(rows)})
    if len(records) != source["total_records"]:
        raise ValueError("OpenAlex total differs from sealed manifest")
    identifiers = [record.get("id") for record in records]
    if not all(identifiers) or len(set(identifiers)) != len(identifiers):
        raise ValueError("OpenAlex IDs are missing or repeated across pages")
    rows = []
    for record in records:
        rows.append({
            "work_id": record["id"],
            "title": record.get("display_name") or record.get("title") or "",
            "published_at": record.get("publication_date"),
            "doi": record.get("doi"),
            "authors": [a.get("author", {}).get("display_name") for a in
                        record.get("authorships") or []
                        if a.get("author", {}).get("display_name")],
            "abstract": restore_abstract(record.get("abstract_inverted_index")),
            "type": record.get("type"),
            "venue": ((record.get("primary_location") or {}).get("source") or {}).get("display_name"),
        })
    families = find_version_families(rows)
    by_title: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        key = title_key(row["title"])
        if key:
            by_title[key].append(row)
    repeated = [group for group in by_title.values() if len(group) > 1]
    id_to_row = {row["work_id"]: row for row in rows}
    reviewer_groups = [
        {"title": group[0]["title"], "records": [
            {key: row[key] for key in ("work_id", "doi", "type", "venue", "published_at", "authors")}
            for row in group]}
        for group in sorted(repeated, key=lambda group: group[0]["title"].casefold())
    ]
    by_card = []
    if packet is not None:
        if packet.get("version") != "top15-full-composition-review-packet-v1":
            raise ValueError("Unsupported review packet")
        directions = {item["direction"] for item in packet["items"]}
        if not direction or direction not in directions:
            raise ValueError("Explicit matching packet direction is required")
        family_for = {identifier: index for index, family in enumerate(families["families"])
                      for identifier in family["work_ids"]}
        for item in packet["items"]:
            if item["direction"] != direction:
                continue
            oa_ids_by_work = [
                [url for url in work["sources"] if url.startswith("https://openalex.org/")]
                for work in item["works"]
            ]
            oa_ids = [identifier for group in oa_ids_by_work for identifier in group]
            if len(oa_ids) != len(set(oa_ids)):
                raise ValueError("Repeated OpenAlex ID inside a card")
            if any(not group for group in oa_ids_by_work):
                raise ValueError("Packet work has no OpenAlex ID in the matching source package")
            missing = sorted(identifier for identifier in oa_ids if identifier not in id_to_row)
            if missing:
                raise ValueError("Packet contains OpenAlex IDs missing from source package")
            title_counts = Counter(title_key(work["title"]) for work in item["works"])
            types = Counter((id_to_row[identifier]["type"] or "unknown") for identifier in oa_ids)
            supporting_ids = []
            unit_keys = []
            family_counts = Counter()
            for index, group in enumerate(oa_ids_by_work):
                if all(id_to_row[identifier]["type"] in NON_STANDALONE_TYPES
                       for identifier in group):
                    supporting_ids.extend(group)
                    continue
                family_ids = {family_for[identifier] for identifier in group
                              if identifier in family_for}
                if len(family_ids) > 1:
                    raise ValueError("One canonical work spans conflicting version families")
                family_id = next(iter(family_ids)) if family_ids else None
                if family_id is not None:
                    family_counts[family_id] += 1
                    unit_keys.append(("family", family_id))
                else:
                    unit_keys.append(("work", index))
            by_card.append({
                "item_id": item["item_id"], "direction": item["direction"],
                "generated_label": item["generated_label"],
                "works_shown": len(item["works"]),
                "same_title_extra_memberships": sum(n - 1 for n in title_counts.values() if n > 1),
                "explicit_version_family_extra_memberships": sum(
                    n - 1 for n in family_counts.values() if n > 1),
                "openalex_record_types": dict(sorted(types.items())),
                "non_standalone_record_ids": sorted(supporting_ids),
                "sensitivity_units_after_supporting_type_and_explicit_alias_rules":
                    len(set(unit_keys)),
                "missing_from_raw_package": missing,
            })
    return {
        "version": VERSION,
        "source_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "source_mission_id": manifest["mission_id"],
        "page_hashes": page_hashes,
        "records": len(rows),
        "types": dict(sorted(Counter(row["type"] or "unknown" for row in rows).items())),
        "same_title_groups": len(repeated),
        "same_title_extra_records": sum(len(group) - 1 for group in repeated),
        "non_standalone_types": sorted(NON_STANDALONE_TYPES),
        "version_families": families,
        "same_title_groups_for_review": reviewer_groups,
        "review_packet_cards": by_card,
        "interpretation": "Candidate relations, not automatic identity merges or detector accuracy",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--packet", type=Path)
    parser.add_argument("--direction")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Audit output is immutable")
    packet = json.loads(args.packet.read_text()) if args.packet else None
    result = audit(args.package, packet, args.direction)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")
    print(json.dumps({key: result[key] for key in
                      ("records", "same_title_groups", "same_title_extra_records")}
                     | {"version_family_groups": len(result["version_families"]["families"]),
                        "version_family_links": len(result["version_families"]["links"])},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
