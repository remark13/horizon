#!/usr/bin/env python3
"""Frozen, explicit source/UI review addendum. Requires the existing SAIA base."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from build_sources_ui_handoff import FILES as SOURCE_FILES, project_version

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.4.50"
OUTPUT = ROOT / "handoff/SAIA_2026-09-28/SAIA_SOURCES_UI_0.4.50_2026-09-28.zip"
FILES = tuple(name for name in SOURCE_FILES if not name.endswith("SOURCES_UI_START_HERE.md")) + (
    "docs/impact-notes-and-brief-2026-09-28.md",
    "docs/sources-ui-checkpoint-0.4.50-2026-09-28.md",
    "reports/generated/SAIA_impact_notes_and_export_2026-09-28.md",
    "handoff/SAIA_2026-09-28/IMPACT_NOTES_START_HERE.md",
    "saia/candidate_analysis.py", "saia/candidate_brief.py", "saia/scout_analysis_web.py",
    "migrations/061_candidate_analysis_note.sql",
    "tests/test_candidate_analysis.py", "tests/test_candidate_brief.py",
    "tests/test_sources_handoff_builder.py",
    "scripts/verify_candidate_brief.py", "scripts/build_impact_notes_handoff.py",
    "outputs/impact-notes-2026-09-28/checkpoint.json",
    "outputs/impact-notes-2026-09-28/card-7061.html",
    "outputs/impact-notes-2026-09-28/VALIDATION.md",
)
REPORTS = {
    "reports/generated/SAIA_sources_interface_2026-09-28.md",
    "reports/generated/SAIA_impact_notes_and_export_2026-09-28.md",
}


def main() -> None:
    installed = project_version(ROOT / "pyproject.toml")
    if installed != VERSION:
        raise ValueError(f"This frozen {VERSION} builder cannot package runtime {installed}.")
    paths = [ROOT / name for name in FILES]
    # Exact public-policy families, not a recursive project/corpus scan.
    paths += sorted((ROOT / "config").glob("external-sources.v0.4.*.yaml"))
    paths += sorted((ROOT / "config").glob("source-adoption.v0.4.*.yaml"))
    paths += sorted((ROOT / "outputs/sources-ui-live-check-2026-09-28/screens").glob("*.png"))
    paths += sorted((ROOT / "outputs/impact-notes-2026-09-28/screens").glob("*.jpg"))
    members = {}
    for path in paths:
        real = path.resolve(strict=True)
        if not real.is_relative_to(ROOT) or real.suffix not in {".md", ".py", ".sql", ".yaml", ".toml", ".json", ".png", ".jpg", ".html"}:
            raise ValueError(f"Unexpected review member: {path.name}")
        if real.stat().st_size > 2_000_000:
            raise ValueError(f"Review member exceeds the small-artifact limit: {path.name}")
        name = str(path.relative_to(ROOT))
        if path.name == "IMPACT_NOTES_START_HERE.md":
            name = "START_HERE.md"
        data = real.read_bytes()
        if name in REPORTS:
            data = data.replace(str(ROOT).encode(), b"../..")
        if name in members:
            raise ValueError(f"Duplicate member: {name}")
        members[name] = data
    if sum(map(len, members.values())) > 15_000_000:
        raise ValueError("Review package exceeds the bounded addendum scope.")
    manifest = {
        "version": VERSION,
        "package_scope": "sources/UI/impact-notes review addendum; requires existing SAIA base",
        "entry_point": "START_HERE.md",
        "members": [{"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                    for name, data in sorted(members.items())],
        "relative_image_paths_in_archive": True,
        "exclusions": [".env", "secrets", "database", "corpora", "model weights", "git history"],
        "evidence_boundaries": {"scientific_score_modified": False, "accuracy_improvement_proven": False,
                                "native_download_checked": False, "native_mouse_checked": False,
                                "narrow_new_block_checked": False,
                                "author_identity_verified": False, "automatic_pestle_forecast": False},
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(OUTPUT, "w", ZIP_DEFLATED) as archive:
        for name, data in sorted(members.items()):
            archive.writestr(name, data)
        archive.writestr("MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    with ZipFile(OUTPUT) as archive:
        if archive.testzip() is not None:
            raise ValueError("ZIP integrity failure")
        saved = json.loads(archive.read("MANIFEST.json"))
        if set(archive.namelist()) != {row["path"] for row in saved["members"]} | {"MANIFEST.json"}:
            raise ValueError("Unexpected ZIP contents")
        for row in saved["members"]:
            data = archive.read(row["path"])
            if len(data) != row["bytes"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
                raise ValueError("Member checksum/size mismatch")
    print(json.dumps({"path": str(OUTPUT), "members": len(members) + 1, "bytes": OUTPUT.stat().st_size,
                      "sha256": hashlib.sha256(OUTPUT.read_bytes()).hexdigest()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
