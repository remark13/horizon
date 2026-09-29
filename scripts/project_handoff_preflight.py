#!/usr/bin/env python3
"""Read-only preflight for the NEXT portable handoff, not the old review ZIP.

No archive, database dump, external download or model upload is created here.
The manifest is a source-package plan, not a claim of full TZ acceptance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = ("README.md", "CURRENT_STATE.md", "RELEASE_STATE.md", "pyproject.toml", ".env.example", ".dockerignore")
SOURCE_DIRS = ("saia", "scripts", "migrations", "config", "infra", "docs", "tests")
REQUIRED = ("README.md", "pyproject.toml", "infra/Dockerfile", "infra/docker-compose.yml",
            "infra/constraints.docker-arm64-py312.txt", ".env.example", "saia/api.py", "saia/db.py",
            "config/external-sources.v0.4.41.yaml", "config/source-adoption.v0.4.50.yaml",
            "migrations/065_large_aggregators.sql")
SUFFIXES = {".py", ".sql", ".yaml", ".yml", ".toml", ".json", ".md", ".txt", ".html", ".css", ".js", ".ipynb"}
DENIED_PARTS = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".codex", ".agents", "secrets"}
KEY_NAMES = ("OPENALEX_API_KEY", "EPO_OPS_CONSUMER_KEY", "EPO_OPS_CONSUMER_SECRET", "SEMANTIC_SCHOLAR_API_KEY",
             "EVENT_REGISTRY_API_KEY", "MEDIACLOUD_API_KEY", "LENS_API_TOKEN")
MAX_FILE_BYTES = 5_000_000


def version(root: Path) -> str:
    value = (root / "pyproject.toml").read_text(encoding="utf-8")
    section = re.search(r"(?ms)^\[project\]\n(.*?)(?=^\[|\Z)", value)
    found = re.search(r'(?m)^version\s*=\s*"([^"\n]+)"', section.group(1)) if section else None
    if not found:
        raise ValueError("Не найдена версия проекта.")
    return found.group(1)


def inspect(root: Path = ROOT) -> dict:
    root = root.resolve(strict=True)
    missing = [name for name in REQUIRED if not (root / name).is_file()]
    paths = [root / name for name in ROOT_FILES if (root / name).exists()]
    for name in SOURCE_DIRS:
        if (root / name).is_dir():
            paths.extend(p for p in (root / name).rglob("*") if p.is_file())
    members, excluded, findings = [], [], []
    known_secrets = [value.encode() for key in KEY_NAMES if (value := os.environ.get(key)) and len(value) >= 8]
    # A private .env may contain credentials absent from the host environment.
    # Read only named key values in memory; never output their values or hashes.
    private_env = root / ".env"
    if private_env.is_file() and not private_env.is_symlink():
        for line in private_env.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator and key.strip() in KEY_NAMES:
                clean = value.strip().strip("\"'")
                if len(clean) >= 8:
                    known_secrets.append(clean.encode())
    for path in sorted(set(paths)):
        relative = path.relative_to(root)
        name = relative.as_posix()
        if path.is_symlink() or any(part in DENIED_PARTS for part in relative.parts):
            excluded.append({"path": name, "reason": "symlink_or_private_runtime_directory"})
            continue
        if path.name.startswith(".env") and path.name != ".env.example":
            excluded.append({"path": name, "reason": "private_environment"})
            continue
        if path.suffix not in SUFFIXES and name not in ROOT_FILES and name not in REQUIRED:
            excluded.append({"path": name, "reason": "not_source_artifact"})
            continue
        if path.stat().st_size > MAX_FILE_BYTES:
            excluded.append({"path": name, "reason": "large_artifact_requires_separate_manifest"})
            continue
        raw = path.read_bytes()
        if any(secret in raw for secret in known_secrets):
            findings.append({"path": name, "kind": "known_private_credential_detected"})
            continue
        # Detect explicit personal-machine paths for relocation review, not
        # just URLs. These warnings are not assertions that every file fails.
        absolute = sorted(set(re.findall(rb"/Users/[^\s\"'<>`]+", raw)))
        if absolute:
            findings.append({"path": name, "kind": "personal_machine_path_requires_review", "occurrences": len(absolute)})
        members.append({"path": name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
    planned_names = {row["path"] for row in members}
    missing_members = [name for name in REQUIRED if name not in planned_names]
    return {"version": "project-handoff-preflight-v1", "application_version": version(root),
            "checked_at": datetime.now(timezone.utc).isoformat(), "package_scope": "planned_source_and_runtime_package_without_private_data_or_weights",
            "missing_required_source_files": missing, "missing_required_package_members": missing_members,
            "members": members, "excluded": excluded, "findings": findings,
            "planned_source_files": len(members), "planned_source_bytes": sum(m["bytes"] for m in members),
            "known_secret_check_passed": not any(f["kind"] == "known_private_credential_detected" for f in findings),
            "database_exported": False, "model_weights_exported": False, "corpora_exported": False,
            "clean_machine_restore_verified": False, "complete_tz_compliance_proven": False,
            "final_package_created": False,
            "next_acceptance_checks": [
                "Freeze the UI and runtime version; use a NEW builder, never overwrite the frozen 0.4.49 review addendum.",
                "Write START_HERE with Docker/PostgreSQL setup, exact dependency constraints and optional private key setup.",
                "Create a separate authorized PostgreSQL custom-format snapshot with no owners/privileges; restore to an isolated empty database and verify counts/hashes.",
                "Declare separately required arXiv/OpenAlex corpus and model files with license, size, SHA, version and relative mount/download instructions.",
                "Review personal-machine paths and secret patterns; known-key scan alone is not complete secret detection.",
                "Pack full source/config lineage/migrations/tests and current docs; put historical research and reviews in a separate archive section.",
                "Validate ZIP member paths, CRC, manifest SHA, empty-folder behavior and clean-machine Docker launch without access to the original checkout.",
                "Provide a public/private repository as agreed, README/methodology, demo instructions and a PDF/PPTX presentation of no more than 15 slides.",
                "Do not claim hidden-test accuracy, trained classifier delivery or SLA until separately measured."],
            "excluded_by_design": ["actual .env and API keys", "database volumes", "user corpora", "model cache and weights", "git history", "unrequested personal attachments"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/large-aggregators-0.4.55-2026-09-29/handoff-preflight.json")
    args = parser.parse_args()
    report = inspect()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("application_version", "planned_source_files", "planned_source_bytes", "missing_required_source_files", "known_secret_check_passed", "final_package_created")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
