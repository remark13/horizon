"""Frozen diagnostic of local multilingual similarity on developer-reviewed pairs.

Scores are exploratory. No threshold or production filter is selected here.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from time import perf_counter

from saia.controlled_collection import sha256_file
from saia.embed import OllamaEmbedder
from saia.priority_catalog_vectors import MODEL
from saia.priority_relevance_review import evaluate
from saia.query_translation_pilot import LOCAL_API, _model_digest


VERSION = "priority-arxiv-pilot-semantic-diagnostic-v1"
EN_DRAFT_VERSION = "priority-arxiv-pilot-semantic-diagnostic-en-draft-v1"


def _english_drafts(catalog_path: Path) -> dict[str, str]:
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    by_title = {}
    for item in catalog["national_search_areas"] + catalog["customer_examples"]:
        title = item.get("title_ru") or item.get("title_original")
        terms = item.get("query_draft", {}).get("en_terms")
        if not title or not isinstance(terms, list) or not terms or title in by_title:
            raise ValueError("English draft map is missing or has ambiguous titles")
        if any(not isinstance(term, str) or not term.strip() for term in terms):
            raise ValueError("English draft term is empty")
        by_title[title] = "; ".join(terms)
    return by_title


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        raise ValueError("Incompatible embedding dimensions")
    denominator = math.sqrt(sum(x * x for x in a) * sum(x * x for x in b))
    if not math.isfinite(denominator) or denominator == 0:
        raise ValueError("Invalid embedding vector")
    return sum(x * y for x, y in zip(a, b)) / denominator


def _batch_embed(embedder: OllamaEmbedder, texts: list[str], batch_size: int) -> list[list[float]]:
    vectors = []
    for start in range(0, len(texts), batch_size):
        batch = embedder.embed(texts[start:start + batch_size])
        if len(batch) != len(texts[start:start + batch_size]):
            raise ValueError("Embedding response size mismatch")
        vectors.extend(batch)
    return vectors


def _pairwise(rows: list[dict]) -> dict:
    by_topic = defaultdict(list)
    for row in rows:
        by_topic[row["target_topic"]].append(row)
    comparisons = Counter()
    examples = []
    for topic, items in by_topic.items():
        for better in items:
            for worse in items:
                ranks = {"yes": 2, "partial": 1, "no": 0}
                if (better["label"] not in ranks or worse["label"] not in ranks
                        or ranks[better["label"]] <= ranks[worse["label"]]):
                    continue
                key = f"{better['label']}_over_{worse['label']}"
                comparisons[f"{key}_pairs"] += 1
                if better["cosine"] > worse["cosine"]:
                    comparisons[f"{key}_correct"] += 1
                elif better["cosine"] == worse["cosine"]:
                    comparisons[f"{key}_tied"] += 1
                elif len(examples) < 20:
                    examples.append({"topic": topic, "better_item_id": better["item_id"],
                                     "worse_item_id": worse["item_id"],
                                     "better_score": better["cosine"],
                                     "worse_score": worse["cosine"]})
    return {"counts": dict(sorted(comparisons.items())),
            "sample_inversions": examples,
            "only_within_same_topic": True,
            "no_threshold_or_calibration": True}


def run(*, packet_path: Path, review_path: Path, corpus_dir: Path,
        embedder: OllamaEmbedder | None = None, model_digest: str | None = None,
        batch_size: int = 16, catalog_path: Path | None = None) -> dict:
    import pyarrow.parquet as pq

    if not 1 <= batch_size <= 32:
        raise ValueError("Embedding batch size must be 1–32")
    audit = evaluate(packet_path=packet_path, review_path=review_path,
                     corpus_manifest_path=corpus_dir / "manifest.json")
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    review = json.loads(review_path.read_text(encoding="utf-8"))
    labels = {row[0]: row[1] for row in review["labels"]}
    ids = {item["document"]["arxiv_id"] for item in packet["items"]}
    rows = pq.read_table(corpus_dir / "documents.parquet",
                         columns=["arxiv_id", "title_current", "abstract_current"]).to_pylist()
    documents = {row["arxiv_id"]: row for row in rows if row["arxiv_id"] in ids}
    if set(documents) != ids:
        raise ValueError("Review documents missing from frozen corpus")
    topics = sorted({item["target_topic"] for item in packet["items"]})
    query_texts = topics
    if catalog_path is not None:
        drafts = _english_drafts(catalog_path)
        if set(topics) - set(drafts):
            raise ValueError("Pilot topic missing from English draft catalog")
        query_texts = [drafts[topic] for topic in topics]
    work_ids = sorted(ids)
    inputs = query_texts + [documents[identifier]["title_current"] + ". " +
                       (documents[identifier]["abstract_current"] or "")
                       for identifier in work_ids]
    embedder = embedder or OllamaEmbedder(model=MODEL, url=LOCAL_API)
    digest = model_digest or _model_digest(LOCAL_API, MODEL)
    if not digest:
        raise ValueError("Local model digest is missing")
    started = perf_counter()
    vectors = _batch_embed(embedder, inputs, batch_size)
    duration = perf_counter() - started
    if not vectors or any(len(vector) != len(vectors[0]) for vector in vectors):
        raise ValueError("Inconsistent embedding dimensions")
    topic_vectors = dict(zip(topics, vectors[:len(topics)]))
    document_vectors = dict(zip(work_ids, vectors[len(topics):]))
    scored = []
    for item in packet["items"]:
        identifier = item["document"]["arxiv_id"]
        scored.append({"item_id": item["item_id"], "arxiv_id": identifier,
                       "target_topic": item["target_topic"],
                       "label": labels[item["item_id"]],
                       "cosine": _cosine(topic_vectors[item["target_topic"]],
                                         document_vectors[identifier])})
    result = {"version": EN_DRAFT_VERSION if catalog_path else VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "packet_sha256": audit["packet_sha256"],
            "review_sha256": audit["review_sha256"],
            "corpus_manifest_sha256": audit["corpus_manifest_sha256"],
            "documents_sha256": sha256_file(corpus_dir / "documents.parquet"),
            "model": MODEL, "model_digest": digest,
            "embedding_dimension": len(vectors[0]),
            "input_policy": ("unreviewed English draft phrases vs current arXiv title plus abstract"
                             if catalog_path else
                             "RU target title vs current arXiv title plus abstract"),
            "embedded_texts": len(inputs), "embedding_seconds": duration,
            "pairwise": _pairwise(scored), "items": scored,
            "limits": {"developer_labels_only": True,
                       "no_independent_quality_claim": True,
                       "no_production_threshold_selected": True,
                       "selected_retrieval_sample_only": True,
                       "not_weak_signal_validity": True}}
    if catalog_path:
        result["catalog_sha256"] = sha256_file(catalog_path)
        result["limits"]["english_phrases_unreviewed"] = True
    return result
