"""Deterministic branch- and source-balanced merge without scientific scoring."""

from __future__ import annotations

from collections import deque
from saia.preview_identity import same_publication


VERSION = "balanced-branch-source-merge-0.4.51"


def _branch_lane(source_rows: dict[str, list[dict]]) -> deque[dict]:
    sources = list(source_rows)
    queues = {source: deque(rows) for source, rows in source_rows.items()}
    lane = deque()
    while any(queues[source] for source in sources):
        for source in sources:
            if queues[source]:
                row = dict(queues[source].popleft())
                row["_lane_source"] = source
                lane.append(row)
    return lane


def merge(branches: list[dict], max_results: int = 15) -> dict:
    if not 1 <= max_results <= 300:
        raise ValueError("Лимит объединения должен быть от 1 до 300.")
    branch_ids = [branch.get("branch_id") for branch in branches]
    if not branch_ids or any(not value for value in branch_ids):
        raise ValueError("Нужна хотя бы одна именованная ветвь.")
    if len(branch_ids) != len(set(branch_ids)):
        raise ValueError("Идентификаторы ветвей не должны повторяться.")
    lanes = {}
    nonempty = []
    for branch in branches:
        source_rows = branch.get("sources") or {}
        if not isinstance(source_rows, dict) or any(not isinstance(rows, list) for rows in source_rows.values()):
            raise ValueError(f"Некорректные источники ветви {branch['branch_id']}.")
        for rows in source_rows.values():
            if any(not row.get("canonical_key") for row in rows):
                raise ValueError(f"Публикация без canonical_key в ветви {branch['branch_id']}.")
        lanes[branch["branch_id"]] = _branch_lane(source_rows)
        if lanes[branch["branch_id"]]:
            nonempty.append(branch["branch_id"])
    selected = []
    cross_key_duplicates = 0
    branch_contributions = {branch_id: 0 for branch_id in branch_ids}
    source_contributions = {}
    while len(selected) < max_results and any(lanes.values()):
        progress = False
        for branch_id in branch_ids:
            lane = lanes[branch_id]
            while lane:
                row = lane.popleft()
                source = row.pop("_lane_source")
                existing = next((candidate for candidate in selected if same_publication(candidate, row)), None)
                if existing is not None:
                    if existing["canonical_key"] != row["canonical_key"]:
                        cross_key_duplicates += 1
                    existing["branch_provenance"] = sorted(set(existing["branch_provenance"] + [branch_id]))
                    existing["source_provenance"] = sorted(set(existing["source_provenance"] + [source]))
                    for field in ("source_ids", "urls", "authors"):
                        existing[field] = list(dict.fromkeys(
                            [*(existing.get(field) or []), *(row.get(field) or [])]
                        ))
                    if existing.get("retrieval_origins") or row.get("retrieval_origins"):
                        existing["retrieval_origins"] = sorted(set(
                            [*(existing.get("retrieval_origins") or []),
                             *(row.get("retrieval_origins") or [])]
                        ))
                    if not existing.get("doi") and row.get("doi"):
                        existing["doi"] = row["doi"]
                        existing["canonical_key"] = row["canonical_key"]
                    if not existing.get("abstract") and row.get("abstract"):
                        existing["abstract"] = row["abstract"]
                    if not existing.get("openalex_type") and row.get("openalex_type"):
                        existing["openalex_type"] = row["openalex_type"]
                    if (row.get("published_at") and existing.get("published_at")
                            and row["published_at"] < existing["published_at"]):
                        existing["published_at"] = row["published_at"]
                    continue
                row["branch_provenance"] = [branch_id]
                row["source_provenance"] = [source]
                selected.append(row)
                branch_contributions[branch_id] += 1
                source_contributions[source] = source_contributions.get(source, 0) + 1
                progress = True
                break
            if len(selected) >= max_results:
                break
        if not progress:
            break
    # A branch whose only publication was merged into another branch's result
    # is still represented. Contributions count first placements, not coverage.
    represented_in_results = {
        branch_id for row in selected for branch_id in row["branch_provenance"]
    }
    represented = [branch_id for branch_id in branch_ids if branch_id in represented_in_results]
    complete_coverage = set(represented) == set(nonempty)
    warnings = []
    if not complete_coverage:
        warnings.append(
            "Лимит результата не позволил представить каждую непустую ветвь; "
            "это ограничение выдачи, а не отсутствие данных."
        )
    return {
        "version": VERSION,
        "max_results": max_results,
        "results": selected,
        "branch_contributions": branch_contributions,
        "source_contributions": source_contributions,
        "cross_key_duplicates_collapsed": cross_key_duplicates,
        "nonempty_branches": nonempty,
        "represented_branches": represented,
        "complete_nonempty_branch_coverage": complete_coverage,
        "scientific_score_calculated": False,
        "within_lane_order_preserved": True,
        "interpretation": (
            "Round-robin предотвращает вытеснение узких ветвей массовой выдачей. "
            "Он не измеряет релевантность, новизну или силу слабого сигнала."
        ),
        "warnings": warnings,
    }
