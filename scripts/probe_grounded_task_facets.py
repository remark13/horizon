"""Diagnostic extraction of grounded task/method facets from saved abstracts.

Closed-set task labels and developer judgements make this an intentionally
easier pilot than arbitrary open-world discovery. No output changes SAIA cards.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from time import perf_counter
from urllib.error import URLError

from saia.controlled_collection import sha256_file
from saia.query_translation_pilot import _model_digest, _post


ROOT = Path(__file__).resolve().parents[1]
VERSION = "grounded-task-facets-diagnostic-v1"
LOCAL_ROOTS = {"http://127.0.0.1:11434", "http://host.docker.internal:11434"}


def _read(config: dict, name: str) -> dict:
    path = (ROOT / config[name]).resolve()
    if not path.is_relative_to(ROOT) or sha256_file(path) != config[name + "_sha256"]:
        raise ValueError(f"Frozen {name} differs")
    return json.loads(path.read_text(encoding="utf-8"))


def _items(config: dict) -> list[dict]:
    bas = _read(config, "bas_source")
    cross = _read(config, "cross_source")
    bas_works = {str(work["work_id"]): work for card in bas["cards"]
                 for work in card["works"]}
    cross_items = {item["item_id"]: item for item in cross["items"]}
    rows = config.get("rows") or []
    if len(rows) != 12 or len({(row["source"], row["id"]) for row in rows}) != 12:
        raise ValueError("Pilot must contain twelve unique frozen papers")
    chosen = []
    for row in rows:
        if row["developer_task_family"] not in config["allowed_task_families"]:
            raise ValueError("Unknown developer task family")
        if row["source"] == "bas":
            work = bas_works.get(row["id"])
            if work is None:
                raise ValueError("Missing frozen BAS work")
            title, abstract = work["title"], work.get("abstract")
            identifiers = work.get("identifiers") or []
            arxiv = next((i["value"] for i in identifiers if i["kind"] == "arxiv"), None)
            url = "https://arxiv.org/abs/" + arxiv if arxiv else None
        elif row["source"] == "cross":
            item = cross_items.get(row["id"])
            if item is None:
                raise ValueError("Missing frozen cross-domain item")
            title = item["document"]["title"]
            abstract = item["document"].get("abstract")
            url = item["document"]["url"]
        else:
            raise ValueError("Unknown source")
        if not title or not abstract:
            raise ValueError("Selected paper lacks title or abstract")
        chosen.append({**row, "title": title, "abstract": abstract, "url": url})
    return chosen


def _schema(families: list[str]) -> dict:
    return {
        "type": "object", "additionalProperties": False,
        "properties": {
            "task_family": {"type": "string", "enum": families},
            "research_object": {"type": "string"},
            "task_quote": {"type": "string"},
            "method_name": {"type": "string"},
            "method_quote": {"type": "string"},
        },
        "required": ["task_family", "research_object", "task_quote",
                     "method_name", "method_quote"],
    }


def _prompt(item: dict, families: list[str]) -> str:
    definitions = {
        "uav_self_localization": "estimate the UAV's own pose or position",
        "aerial_object_localization": "detect or locate a target object in aerial imagery",
        "uav_mission_search_planning": "plan a high-level UAV search mission or task sequence",
        "uav_navigation_path_planning": "plan a flight path or route to a destination",
        "robotic_art_navigation": "navigate a flying art installation or artistic companion",
        "llm_compression": "compress a large language model",
        "agent_identity_access_control": "authenticate or authorize AI agents",
        "composite_material_design": "design a composite material structure",
        "breath_biometrics": "authenticate a person using breath or blowing",
        "other_or_unclear": "none of the above is supported by this text",
    }
    choices = "\n".join(f"- {name}: {definitions[name]}" for name in families)
    return (
        "Read this scientific title and abstract. Select the article's MAIN "
        "research task, not a possible future use or background motivation. "
        "Do not classify the whole technology field. Return JSON only.\n"
        "Allowed task families:\n" + choices + "\n"
        "task_quote and method_quote must be short VERBATIM contiguous "
        "substrings of the supplied title or abstract, not paraphrases. "
        "Use method_quote='' and method_name='unknown' only if the article "
        "does not state a method. research_object and method_name are short "
        "plain-language phrases. If the main task is not in the list, choose "
        "other_or_unclear.\n\nTitle: " + item["title"] +
        "\nAbstract: " + item["abstract"] + "\n"
    )


def _prompt_v2(item: dict, families: list[str]) -> str:
    """Revised only after the v1 diagnostic; evaluate on untouched rows."""
    return _prompt(item, families) + (
        "\nImportant distinctions: locating the UAV's OWN pose by matching an "
        "aerial view to a map is uav_self_localization. Detecting a SEPARATE "
        "person, vessel or object in an aerial image is aerial_object_localization. "
        "A benchmark of cognition, general swarm control, and end-to-end "
        "instruction-following navigation are not automatically path planning. "
        "Use other_or_unclear when the main task is outside the named families. "
        "For EACH nonempty quote, copy at least three consecutive words and "
        "at least twelve characters with the original punctuation exactly. "
        "Before returning JSON, check that each quote occurs verbatim as a "
        "contiguous substring of the supplied title or abstract.\n"
    )


def _grounded(quote: str, source: str) -> bool:
    quote = " ".join(quote.split()).casefold()
    source = " ".join(source.split()).casefold()
    return len(quote) >= 12 and len(quote.split()) >= 3 and quote in source


def _validate(raw: dict, item: dict, allowed: list[str]) -> dict:
    value = json.loads(raw["response"])
    if not isinstance(value, dict) or set(value) != {
            "task_family", "research_object", "task_quote",
            "method_name", "method_quote"}:
        raise ValueError("Missing or unexpected facet keys")
    if value["task_family"] not in allowed:
        raise ValueError("Task family outside frozen taxonomy")
    if any(not isinstance(value[key], str) for key in value):
        raise ValueError("Facet values must be strings")
    source = item["title"] + " " + item["abstract"]
    if not _grounded(value["task_quote"], source):
        raise ValueError("Task quote is not verbatim grounded")
    if value["method_quote"]:
        if not _grounded(value["method_quote"], source):
            raise ValueError("Method quote is not verbatim grounded")
    elif value["method_name"] != "unknown":
        raise ValueError("Method without evidence quote")
    if not value["research_object"].strip() or not value["method_name"].strip():
        raise ValueError("Empty object or method")
    return value


def run(config_path: Path, *, api_root: str | None = None,
        post=_post, model_digest=_model_digest) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    protocol = config.get("version")
    if (protocol not in {"grounded-task-facets-pilot-v1",
                         "grounded-task-facets-holdout-v2"}
            or config.get("model_role") != "development_diagnostic_only"):
        raise ValueError("Unknown facet pilot")
    root = (api_root or os.environ.get("SAIA_OLLAMA_URL")
            or "http://127.0.0.1:11434").rstrip("/")
    if root not in LOCAL_ROOTS:
        raise ValueError("Only local Ollama is permitted")
    items = _items(config)
    model = config["model"]
    digest = model_digest(root, model)
    if not digest:
        raise ValueError("Frozen local model is unavailable")
    schema = _schema(config["allowed_task_families"])
    prompt_factory = _prompt_v2 if protocol.endswith("v2") else _prompt
    rows = []
    for item in items:
        row = {"source": item["source"], "id": item["id"],
               "title": item["title"], "url": item["url"],
               "developer_task_family": item["developer_task_family"]}
        started = perf_counter()
        try:
            raw = post(root + "/api/generate", {
                "model": model,
                "prompt": prompt_factory(item, config["allowed_task_families"]),
                "format": schema, "stream": False, "think": False,
                "options": {"temperature": 0, "num_predict": 350},
                "keep_alive": "5m",
            }, timeout=150)
            row["raw_response"] = raw.get("response")
            facets = _validate(raw, item, config["allowed_task_families"])
            row.update({"status": "valid_grounded", "facets": facets,
                        "agrees_with_developer":
                        facets["task_family"] == item["developer_task_family"]})
        except (ValueError, KeyError, TypeError, TimeoutError, URLError) as error:
            row.update({"status": "invalid", "error_type": type(error).__name__,
                        "error": str(error)[:250]})
        row["seconds"] = perf_counter() - started
        rows.append(row)
    valid = [row for row in rows if row["status"] == "valid_grounded"]
    agreement = sum(row["agrees_with_developer"] for row in valid)
    critical_pairs = {
        ("aerial_object_localization", "uav_self_localization"),
        ("robotic_art_navigation", "uav_mission_search_planning"),
    }
    if protocol.endswith("v2"):
        critical_pairs.add(("uav_self_localization", "aerial_object_localization"))
    critical = sum((row["developer_task_family"],
                    row["facets"]["task_family"]) in critical_pairs
                   for row in valid)
    gate = config["gate"]
    return {
        "version": (VERSION if protocol.endswith("v1")
                    else "grounded-task-facets-diagnostic-v2"),
        "protocol": protocol,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "config_sha256": sha256_file(config_path),
        "bas_source_sha256": config["bas_source_sha256"],
        "cross_source_sha256": config["cross_source_sha256"],
        "model": model, "model_digest": digest, "rows": rows,
        "counts": {"total": len(rows), "valid_grounded_json": len(valid),
                   "task_family_agreement_with_developer": agreement,
                   "critical_errors": critical},
        "gate_passed": (len(valid) >= gate["valid_grounded_json_min"]
                        and agreement >= gate["task_family_agreement_min"]
                        and critical <= gate["critical_errors_max"]),
        "limitations": config["limitations"],
        "production_changed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Facet diagnostic report is immutable")
    result = run(args.config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                      indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"counts": result["counts"], "gate_passed": result["gate_passed"]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
