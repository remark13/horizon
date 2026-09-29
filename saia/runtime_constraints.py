"""Read-only validation of pinned runtime package versions."""
from __future__ import annotations

import argparse
import json
import platform
import re
import sys
from importlib import metadata
from pathlib import Path


DEFAULT_CONSTRAINTS = (
    Path(__file__).resolve().parents[1]
    / "infra"
    / "constraints.docker-arm64-py312.txt"
)


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse(text: str) -> dict[str, str]:
    pins: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(
            r"([A-Za-z0-9][A-Za-z0-9_.-]*)==([A-Za-z0-9][A-Za-z0-9_.+!-]*)",
            line,
        )
        if not match:
            raise ValueError(
                "Требуется отдельная точная версия name==version: " + line
            )
        name, version = canonical(match[1]), match[2]
        if name in pins:
            raise ValueError("Повтор имени пакета: " + name)
        pins[name] = version
    if not pins:
        raise ValueError("Пустой список проверяемых версий.")
    return pins


def check(
    pins: dict[str, str],
    lookup=metadata.version,
) -> list[dict[str, str | None]]:
    mismatches: list[dict[str, str | None]] = []
    for name, expected in pins.items():
        try:
            installed = lookup(name)
        except metadata.PackageNotFoundError:
            installed = None
        if installed != expected:
            mismatches.append(
                {"package": name, "expected": expected, "installed": installed}
            )
    return mismatches


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Проверить установленные версии, ничего не устанавливая"
    )
    parser.add_argument("--constraints", type=Path, default=DEFAULT_CONSTRAINTS)
    args = parser.parse_args(argv)
    pins = parse(args.constraints.read_text(encoding="utf-8"))
    mismatches = check(pins)
    tested_platform = (
        platform.system() == "Linux"
        and platform.machine() in ("aarch64", "arm64")
        and sys.version_info[:2] == (3, 12)
    )
    print(
        json.dumps(
            {
                "package_pins_match": not mismatches,
                "packages_checked": len(pins),
                "python": platform.python_version(),
                "system": platform.system(),
                "architecture": platform.machine(),
                "matches_tested_platform": tested_platform,
                "mismatches": mismatches,
                "limitations": (
                    "Совпадение версий не доказывает переносимость на другую "
                    "платформу, тождество колёс, ОС, моделей или научных "
                    "результатов."
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
