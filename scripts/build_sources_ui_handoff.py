#!/usr/bin/env python3
"""Build an explicit, secret-free review addendum, not a full distribution."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "handoff/SAIA_2026-09-28/SAIA_SOURCES_UI_0.4.49_2026-09-28.zip"
FILES = (
    "CURRENT_STATE.md", "RELEASE_STATE.md", "pyproject.toml",
    "docs/sources-ui-scope-2026-09-28.md", "docs/TZ-comparison-2026-09-28.md",
    "reports/generated/SAIA_sources_interface_2026-09-28.md",
    "handoff/SAIA_2026-09-28/SOURCES_UI_START_HERE.md",
    "saia/rss_evidence.py", "saia/source_context.py", "saia/card_presentation.py",
    "saia/scout_web.py", "saia/scout_sources_web.py", "saia/scout_presentation_web.py",
    "saia/api.py", "saia/source_registry.py", "saia/external_sources.py", "saia/external_evidence_store.py",
    "migrations/059_official_rss_sources.sql", "migrations/060_candidate_presentation.sql",
    "tests/test_rss_evidence.py", "tests/test_source_context.py", "tests/test_card_presentation.py",
    "tests/test_scout_sources_ui.py", "tests/test_source_registry.py", "tests/test_external_sources.py", "tests/test_api.py", "tests/test_integration.py",
    "scripts/source_ui_live_check.py", "scripts/export_sources_ui_checkpoint.py", "scripts/build_sources_ui_handoff.py",
    "outputs/sources-ui-live-check-2026-09-28/probes.json",
    "outputs/sources-ui-live-check-2026-09-28/checkpoint.json",
)


def project_version(path: Path) -> str:
    """Read the one trusted project field without a Python 3.11-only import."""
    content = path.read_text(encoding="utf-8")
    section = re.search(r"(?ms)^\[project\][ \t]*\n(.*?)(?=^\[|\Z)", content)
    version = re.search(r"""(?m)^version[ \t]*=[ \t]*["']([^"']+)["'][ \t]*(?:#.*)?$""", section.group(1)) if section else None
    if version is None:
        raise ValueError("Missing [project].version in the trusted package file.")
    return version.group(1)


def main() -> None:
    installed = project_version(ROOT / "pyproject.toml")
    if installed != "0.4.49":
        raise ValueError("The frozen 0.4.49 archive must not be overwritten with newer runtime files; use the matching builder.")
    paths = [ROOT / name for name in FILES]
    # These exact filename families contain public source policy/lineage only.
    paths += sorted((ROOT / "config").glob("external-sources.v0.4.*.yaml"))
    paths += sorted((ROOT / "config").glob("source-adoption.v0.4.*.yaml"))
    paths += sorted((ROOT / "outputs/sources-ui-live-check-2026-09-28/screens").glob("*.png"))
    members = {}
    for path in paths:
        real = path.resolve(strict=True)
        if not real.is_relative_to(ROOT) or real.suffix not in {".md", ".py", ".sql", ".yaml", ".toml", ".json", ".png"}:
            raise ValueError(f"Unexpected review member: {path.name}")
        name = str(path.relative_to(ROOT))
        if path.name == "SOURCES_UI_START_HERE.md":
            name = "START_HERE.md"
        data = real.read_bytes()
        if name == "reports/generated/SAIA_sources_interface_2026-09-28.md":
            # Local app previews need absolute image paths; the review ZIP uses
            # relative paths so the same included PNGs work after extraction.
            data = data.replace(str(ROOT).encode(), b"../..")
        members[name] = data
    manifest = {"package_scope": "sources/UI review addendum; requires existing SAIA base", "version": "0.4.49",
                "members": [{"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                            for name, data in sorted(members.items())],
                "relative_image_paths_in_archive": True,
                "exclusions": [".env", "secrets", "database", "corpora", "model weights", "git history"]}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(OUTPUT, "w", ZIP_DEFLATED) as archive:
        for name, data in sorted(members.items()):
            archive.writestr(name, data)
        archive.writestr("MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    with ZipFile(OUTPUT) as archive:
        if archive.testzip() is not None:
            raise ValueError("ZIP integrity failure")
        read_manifest = json.loads(archive.read("MANIFEST.json"))
        if set(archive.namelist()) != {row["path"] for row in read_manifest["members"]} | {"MANIFEST.json"}:
            raise ValueError("Unexpected ZIP contents")
        for row in read_manifest["members"]:
            if hashlib.sha256(archive.read(row["path"])).hexdigest() != row["sha256"]:
                raise ValueError("Member checksum mismatch")
    print(json.dumps({"path": str(OUTPUT), "members": len(members) + 1, "bytes": OUTPUT.stat().st_size,
                      "sha256": hashlib.sha256(OUTPUT.read_bytes()).hexdigest()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
