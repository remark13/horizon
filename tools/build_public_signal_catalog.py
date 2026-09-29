"""Extract source-labelled titles from pinned, public EU PDF editions.

Run with the bundled document Python runtime (pdfplumber is not an app dependency).
This is an extraction aid, not an assertion that every source item was recovered.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

import pdfplumber


ROOT = Path(__file__).resolve().parents[1]
JRC = ROOT / "data/reference/jrc/JRC140959-weak-signals-2024.pdf"
JRC_2021 = ROOT / "data/reference/public_signals/jrc-weak-signals-2021.pdf"
ESPAS = ROOT / "data/reference/public_signals/espas-signal-cards-2026.pdf"
OUTPUT = ROOT / "data/reference/public_signals/catalog.v5.json"

JRC_CLUSTERS = [
    "Advanced Manufacturing and advanced materials", "Aerospace",
    "Mobility and Transport", "Digital Twins", "AI and Machine Learning",
    "ICT", "Medical Imaging", "Therapeutics and Biotechnologies",
    "e-Health", "Environment and Agriculture", "Energy",
    "Quantum and Cryptography",
]
ESPAS_CATEGORIES = (
    "CONTRADICTION", "INFLECTION", "EMERGING", "HACK", "EXTREME",
    "NOVELTY", "DISRUPTION", "TRANSFORMATIVE",
)
JRC_TITLE_NORMALIZATIONS = {
    23: "Photothermal superhydrophobic coating",
    24: "Piezo photocatalysis",
    25: "Polyetherimide for energy storage",
    34: "Urea electrosynthesis",
    40: "Edge computing for satellites",
    43: "Space-Air-Ground Integrated Network",
    54: "Marine fuel - ammonia",
    100: "Intrusion Detection BoT-IoT",
    117: "Vesical Imaging-Reporting and Data System",
    118: "Line-field confocal optical coherence tomography",
    119: "Optoretinography",
    133: "Intermittently scanned continuous glucose monitoring",
    146: "AI-driven neurodegenerative disease detection",
    147: "AI for abdominal lesion detection and characterization",
    170: "Edge computing in agriculture",
    210: "Quantum support vector machines",
}
JRC_2021_SECTIONS = {
    "2.1": "Engineering & Physics",
    "2.2": "Information & Communication technologies",
    "2.3": "Materials",
    "2.4": "Agriculture & Environment",
    "2.5": "Medicine and Biotechnology",
    "2.6": "Societal issues",
    "2.7": "Energy",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def line_text(words: list[dict]) -> str:
    lines: list[list[dict]] = []
    for word in sorted(words, key=lambda w: (round(w["top"] / 2), w["x0"])):
        if not lines or abs(lines[-1][0]["top"] - word["top"]) > 2:
            lines.append([])
        lines[-1].append(word)
    return " ".join(" ".join(w["text"] for w in line) for line in lines).replace("- ", "-").strip()


def jrc_records() -> list[dict]:
    records = []
    cluster = None
    with pdfplumber.open(JRC) as pdf:
        for index in range(105, 145):
            page = pdf.pages[index]
            words = page.extract_words()
            headings = []
            for heading in JRC_CLUSTERS:
                for match in page.search(re.escape(heading), regex=True):
                    if match["x0"] < 100:
                        headings.append((match["top"], heading))
            starts = sorted(
                (word for word in words if word["text"] == "a)" and 125 <= word["x0"] <= 170),
                key=lambda w: w["top"],
            )
            for start in starts:
                preceding = [name for top, name in headings if top < start["top"]]
                if preceding:
                    cluster = preceding[-1]
                if cluster is None:
                    raise ValueError(f"No JRC cluster before PDF page {index + 1}")
                following = [
                    word["top"] for word in words
                    if word["text"].startswith("b)") and word["top"] > start["top"]
                    and abs(word["x0"] - start["x0"]) < 12
                ]
                stop = min(following) if following else start["top"] + 80
                first_column = [
                    word for word in words
                    if start["top"] - 2 <= word["top"] < stop
                    and 65 <= word["x0"] < start["x0"] - 9
                    and word["text"] not in {"a)", "b)"}
                ]
                title = line_text(first_column)
                if not title:
                    # A handful of table rows place their title immediately
                    # above the a) paragraph rather than alongside it.
                    previous_column = [
                        word for word in words
                        if start["top"] - 34 <= word["top"] < start["top"] - 2
                        and 65 <= word["x0"] < start["x0"] - 9
                    ]
                    title = line_text(previous_column)
                # This row uses a full-width bold heading above the description
                # instead of the first table column.
                if not title and index + 1 == 116 and start == starts[0]:
                    title = "Lidar Odometry"
                if not title or len(title) > 150:
                    raise ValueError(f"Suspicious JRC title on PDF page {index + 1}: {title!r}")
                sequence = len(records) + 1
                displayed = JRC_TITLE_NORMALIZATIONS.get(sequence, title)
                row = {
                    "id": f"jrc-2024-p{index + 1}-{sequence:03d}",
                    "source_id": "jrc_weak_signals_2024",
                    "title": displayed,
                    "category": cluster,
                    "source_page": index + 1,
                    "source_url": "https://publications.jrc.ec.europa.eu/repository/bitstream/JRC140959/JRC140959_01.pdf#page=" + str(index + 1),
                    "source_date": "2025-02-17",
                    "source_label": "JRC, Weak signals in Science and Technologies 2024",
                    "record_kind": "published_weak_signal",
                }
                if displayed != title:
                    row["title_as_extracted"] = title
                    row["title_normalized"] = True
                records.append(row)
            if headings:
                cluster = max(headings)[1]
    return records


def jrc_2021_records() -> list[dict]:
    """Use 93 individually described bold item headings from the report body."""
    records = []
    category = None
    with pdfplumber.open(JRC_2021) as pdf:
        for index in range(13, 65):
            words = pdf.pages[index].extract_words(extra_attrs=["fontname", "size"])
            lines: dict[float, list[dict]] = {}
            for word in words:
                normal_heading = abs(word["size"] - 12) < 0.2 and "Bold" in word["fontname"]
                special_heading = (
                    index == 23 and abs(word["top"] - 345.1) < 0.3
                    and abs(word["size"] - 10) < 0.2
                    and "Verdana-Bold" in word["fontname"]
                )
                if normal_heading or special_heading:
                    lines.setdefault(round(word["top"], 1), []).append(word)
            for _, line in sorted(lines.items()):
                title = " ".join(word["text"] for word in sorted(line, key=lambda w: w["x0"]))
                section = re.match(r"^(2\.[1-7])\s", title)
                if section:
                    category = JRC_2021_SECTIONS[section.group(1)]
                    continue
                if (not category or title.startswith(("Radar ", "From publishing"))
                        or title in {"Dashboard:"}):
                    continue
                if not 3 <= len(title) <= 150:
                    raise ValueError(f"Suspicious JRC 2021 item on PDF page {index + 1}: {title!r}")
                sequence = len(records) + 1
                records.append({
                    "id": f"jrc-2021-p{index + 1}-{sequence:03d}",
                    "source_id": "jrc_weak_signals_2021",
                    "title": title,
                    "category": category,
                    "source_page": index + 1,
                    "source_url": "https://publications.jrc.ec.europa.eu/repository/bitstream/JRC129501/kjna31171enn_1.pdf#page=" + str(index + 1),
                    "source_date": "2022-08-22",
                    "source_label": "JRC, Weak signals in Science and Technologies in 2021",
                    "record_kind": "published_weak_signal",
                })
    return records


def espas_records() -> list[dict]:
    records = []
    with pdfplumber.open(ESPAS) as pdf:
        for page_number in range(7, len(pdf.pages) + 1):
            raw = subprocess.run(
                ["pdftotext", "-f", str(page_number), "-l", str(page_number),
                 "-layout", str(ESPAS), "-"],
                check=True, capture_output=True, text=True,
            ).stdout
            lines = [line.strip() for line in raw.splitlines()]
            title_lines = []
            for line in lines:
                if not line:
                    if title_lines:
                        break
                    continue
                if not line.isupper():
                    break
                title_lines.append(line)
            title = re.sub(r"\s+", " ", " ".join(title_lines)).replace(" - ", "-")
            found = [category for category in ESPAS_CATEGORIES
                     if re.search(rf"\b{category}\b", raw)]
            if not title or len(found) != 1:
                raise ValueError(f"ESPAS PDF page {page_number}: title={title!r}, categories={found}")
            records.append({
                "id": f"espas-2026-p{page_number:03d}",
                "source_id": "espas_signal_cards_2026",
                "title": title,
                "category": found[0].title(),
                "source_page": page_number,
                "source_url": "https://knowledge4policy.ec.europa.eu/sites/default/files/Signal%20Cards_Web_July%202026.pdf#page=" + str(page_number),
                "source_date": "2026-09-15",
                "source_label": "ESPAS Horizon Scanning, Signal Cards 2026",
                "record_kind": "published_signal_of_change",
            })
    return records


def main() -> None:
    if OUTPUT.exists():
        raise FileExistsError(f"Do not overwrite a pinned catalog: {OUTPUT}")
    jrc = jrc_records()
    espas = espas_records()
    jrc_2021 = jrc_2021_records()
    if len(jrc) != 216 or len(espas) != 92 or len(jrc_2021) != 93:
        raise ValueError(f"Unexpected extraction counts: JRC {len(jrc)}, ESPAS {len(espas)}, JRC 2021 {len(jrc_2021)}")
    records = jrc + espas + jrc_2021
    if len({row["id"] for row in records}) != len(records):
        raise ValueError("Duplicate public-signal ID")
    catalog = {
        "version": "public-signals-catalog-v5",
        "source_documents": [
            {
                "source_id": "jrc_weak_signals_2024", "publisher": "European Commission JRC",
                "title": "Weak signals in Science and Technologies - 2024",
                "url": "https://publications.jrc.ec.europa.eu/repository/handle/JRC140959",
                "pdf_sha256": sha256(JRC), "published_at": "2025-02-17",
                "license": "CC BY 4.0", "reported_total": 221,
                "extracted_total": len(jrc),
                "coverage_note": "216 of 221 reported items parsed as individually labelled appendix rows; five are not claimed or inferred.",
            },
            {
                "source_id": "espas_signal_cards_2026", "publisher": "ESPAS / European Commission JRC",
                "title": "ESPAS Horizon Scanning Signal Cards, 2026 edition",
                "url": "https://knowledge4policy.ec.europa.eu/file/signal-cards_en",
                "pdf_sha256": sha256(ESPAS), "published_at": "2026-09-15",
                "license": "Creative Commons (edition states this without a version)",
                "reported_total": 113, "extracted_total": len(espas),
                "coverage_note": "92 card pages in the pinned 98-page 2026 PDF; the site describes a larger evolving deck. No missing cards are inferred.",
            },
            {
                "source_id": "jrc_weak_signals_2021", "publisher": "European Commission JRC",
                "title": "Weak signals in Science and Technologies in 2021",
                "url": "https://publications.jrc.ec.europa.eu/repository/handle/JRC129501",
                "pdf_sha256": sha256(JRC_2021), "published_at": "2022-08-22",
                "license": "CC BY 4.0", "reported_total": 93,
                "extracted_total": len(jrc_2021),
                "coverage_note": "93 individually described body headings. One heading uses a different font; two detailed items do not appear in the overview figure. The published report states 93 overall.",
            },
        ],
        "records": records,
        "interpretation": "External foresight references, not SAIA detections, gold labels or evidence of future success.",
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "jrc": len(jrc), "espas": len(espas), "jrc_2021": len(jrc_2021)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
