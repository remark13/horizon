"""Diagnostic local-model RU→EN retrieval bridge, not an executable user plan.

Model phrases are unreviewed candidates. Never inject them into approved search
plans or score them as evidence without explicit validation and a durable audit.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from time import perf_counter
from urllib.request import Request, urlopen

from saia.arxiv_metadata import ORTHOGRAPHIC_MATCHING_VERSION
from saia.arxiv_trigram_index import exact_search, supports_plan
from saia.controlled_collection import sha256_file


VERSION = "ru-query-translation-diagnostic-v1"
MODEL = "qwen3:4b-instruct"
LOCAL_API = "http://127.0.0.1:11434"
QUERIES = (
    ("inside", "малые модульные реакторы"),
    ("boundary", "нейроморфные чипы для граничных устройств"),
    ("outside", "биоразлагаемые полимеры для медицинских имплантов"),
)
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["translation_en", "core_phrases_en", "uncertainty"],
    "properties": {
        "translation_en": {"type": "string"},
        "core_phrases_en": {"type": "array", "minItems": 1, "maxItems": 3,
                            "items": {"type": "string"}},
        "uncertainty": {"type": "string"},
    },
}


def _post(url: str, payload: dict, timeout: int = 90) -> dict:
    request = Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                      headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


def _model_digest(api_root: str, model: str) -> str | None:
    with urlopen(api_root + "/api/tags", timeout=10) as response:
        models = json.load(response).get("models") or []
    return next((item.get("digest") for item in models
                 if item.get("name") == model or item.get("model") == model), None)


def _validated_response(response: dict) -> dict:
    parsed = json.loads(response["response"])
    if not isinstance(parsed, dict) or set(parsed) != set(SCHEMA["required"]):
        raise ValueError("Local model did not return the requested fields")
    translation = parsed["translation_en"]
    phrases = parsed["core_phrases_en"]
    uncertainty = parsed["uncertainty"]
    if (not isinstance(translation, str) or not translation.strip()
            or not isinstance(phrases, list) or not 1 <= len(phrases) <= 3
            or any(not isinstance(phrase, str) or not phrase.strip() or len(phrase) > 160
                   for phrase in phrases)
            or not isinstance(uncertainty, str)):
        raise ValueError("Local model returned invalid translation candidates")
    return {"translation_en": " ".join(translation.split()),
            "core_phrases_en": list(dict.fromkeys(" ".join(phrase.split())
                                                   for phrase in phrases)),
            "uncertainty": uncertainty.strip()}


def diagnose(*, index_dir: Path, output: Path, api_root: str = LOCAL_API,
             model: str = MODEL) -> dict:
    if output.exists():
        raise FileExistsError("Translation diagnostic is immutable")
    if api_root != LOCAL_API:
        raise ValueError("Diagnostic may call only the local Ollama endpoint")
    digest = _model_digest(api_root, model)
    if not digest:
        raise ValueError("Selected local model is not installed")
    rows = []
    for role, query in QUERIES:
        prompt = (
            "Translate the Russian technology query faithfully for English scientific "
            "title/abstract retrieval. Return a concise full translation and 1–3 shorter "
            "contiguous English noun phrases that preserve its distinctive concepts. "
            "Do not add technologies, materials, applications, or qualifiers absent from "
            "the Russian. If a term is ambiguous, say so in uncertainty. Output JSON only.\n"
            "Russian query: " + query
        )
        started = perf_counter()
        raw = _post(api_root + "/api/generate", {
            "model": model, "prompt": prompt, "format": SCHEMA, "stream": False,
            "think": False, "options": {"temperature": 0, "num_predict": 180},
            "keep_alive": "5m",
        })
        translation_seconds = perf_counter() - started
        proposed = _validated_response(raw)
        observations = []
        for phrase in [query, *proposed["core_phrases_en"]]:
            plan = {"included_terms": [phrase], "exclusions": [],
                    "date_from": "2021-09-01", "as_of_date": "2026-09-01",
                    "matching_version": ORTHOGRAPHIC_MATCHING_VERSION}
            if not supports_plan(plan):
                observations.append({"phrase": phrase, "safe_index_supported": False,
                                     "exact_matches": None})
                continue
            started = perf_counter()
            found = exact_search(index_dir, plan)
            observations.append({"phrase": phrase, "safe_index_supported": True,
                                 "exact_matches": found["audit"]["exact_unique_ids"],
                                 "coarse_matches": found["audit"][
                                     "coarse_hits_with_possible_or_repetition"],
                                 "index_seconds": perf_counter() - started})
        rows.append({"case_role": role, "query_ru": query,
                     "proposal_unreviewed": proposed,
                     "translation_seconds": translation_seconds,
                     "model_total_duration_ns": raw.get("total_duration"),
                     "model_load_duration_ns": raw.get("load_duration"),
                     "observations": observations,
                     "relevance_or_translation_accuracy_measured": False})
    report = {"version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
              "model": model, "model_digest": digest,
              "index_manifest_sha256": sha256_file(index_dir / "manifest.json"),
              "queries": rows,
              "policy": {"model_phrases_executed_in_user_route": False,
                         "candidate_translation_requires_review": True,
                         "more_matches_do_not_prove_relevance": True,
                         "pilot_not_generalizable": True,
                         "no_external_model_api_used": True}}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    return report
