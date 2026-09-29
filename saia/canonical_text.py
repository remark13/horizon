"""Field-level canonical abstract selection with identity and revision provenance.

Not a truth classifier. Prefer native arXiv text only when its own identity,
canonical title and revision/source capture date bind it before the cutoff.
Other cases retain the historical source-order fallback, with unknowns explicit.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import re
from urllib.parse import urlsplit


VERSION = "canonical-abstract-native-bound-v1"
LEGACY = "legacy-first-nonempty"
BARE = re.compile(r"(?:[0-9]{4}\.[0-9]{4,5}|[A-Za-z][A-Za-z.-]*/[0-9]{7})(?:v[1-9][0-9]*)?")


def policy(mode=VERSION):
    if mode not in {VERSION, LEGACY}:
        raise ValueError("Unsupported canonical text policy")
    return {"version": mode, "native_preference_requires_same_id_and_title": mode == VERSION,
            "native_preference_requires_revision_or_observation_before_cutoff": mode == VERSION,
            "legacy_mirror_synthesized_updated_is_not_a_revision_date": True,
            "different_texts_are_not_automatically_errors": True,
            "fallback": "original_source_order_first_nonempty", "rewrites_completed_runs": False}


def text_sha256(text):
    return hashlib.sha256(text.encode()).hexdigest() if text is not None else None


def native_id(value):
    value = str(value or "").strip()
    if value.startswith(("http://", "https://")):
        parsed = urlsplit(value)
        if parsed.hostname not in {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}:
            return None
        if not parsed.path.startswith(("/abs/", "/pdf/")):
            return None
        value = parsed.path[5:]
        if value.endswith(".pdf"):
            value = value[:-4]
    return re.sub(r"v[1-9][0-9]*$", "", value) if BARE.fullmatch(value) else None


def _day(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        if len(value) == 10:
            return date.fromisoformat(value)
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            return None
        return stamp.astimezone(timezone.utc).date()
    except ValueError:
        return None


def _native_revision(metadata):
    if metadata.get("_arxiv_versions") is not None:
        try:
            from saia.arxiv_metadata import version_dates
            first, latest = version_dates(metadata["_arxiv_versions"])
        except ValueError:
            return None, "invalid_version_history"
        updated = _day(metadata.get("updated"))
        if updated and updated != date.fromisoformat(latest):
            return None, "conflicting_version_dates"
        return date.fromisoformat(latest), "native_version_history"
    if (metadata.get("_source_format") == "hf-arxiv-parquet-snapshot"
            and "_submission_date_raw" in metadata):
        # The legacy adapter fills updated from submission_date even when
        # no separate revision is recorded. A v1 date cannot date its text.
        return None, "legacy_snapshot_revision_not_verified"
    updated = _day(metadata.get("updated"))
    first = _day(metadata.get("created") or metadata.get("published"))
    if updated is not None and first is not None:
        return (updated, "native_updated_field") if updated >= first else (None, "conflicting_version_dates")
    return None, "revision_date_unknown"


def select_abstract(candidates: list[dict], *, canonical_title_key: str, cutoff: str,
                    mode: str = VERSION) -> dict:
    """Input candidates are already bound to one work by normalization.

    Each carries raw_record_id, source, source_record_id, title_key, abstract,
    arxiv_ids and native metadata. No titles or abstracts are synthesized.
    """
    policy(mode)
    end = date.fromisoformat(cutoff)
    if len({row["raw_record_id"] for row in candidates}) != len(candidates):
        raise ValueError("Duplicate raw candidates")
    ordered = sorted(candidates, key=lambda row: (0 if row["source"] == "openalex" else 1,
                                                   row["raw_record_id"]))
    expected_ids = {identifier for row in candidates for identifier in row.get("arxiv_ids", [])}
    audits = []
    preferred = []
    for row in ordered:
        value = row.get("abstract")
        if value is not None and not isinstance(value, str):
            raise ValueError("Abstract must be text or null")
        abstract = value or None
        audit = {"raw_record_id": row["raw_record_id"], "source": row["source"],
                 "source_record_id": row["source_record_id"], "abstract_sha256": text_sha256(abstract),
                 "has_abstract": abstract is not None, "native_preference_eligible": False,
                 "historical_text_available": None}
        if row["source"] == "arxiv":
            metadata = row.get("metadata") or {}
            own_id, record_id = native_id(metadata.get("id")), native_id(row["source_record_id"])
            revised, basis = _native_revision(metadata)
            observed = _day(row.get("observed_at"))
            date_conflict = basis in {"invalid_version_history", "conflicting_version_dates"}
            historical = (None if date_conflict else ((revised < end) if revised else
                          ((observed < end) if observed else None)))
            historical_basis = ("native_revision" if revised else
                                ("conflicting_metadata" if date_conflict else
                                 ("source_capture" if observed else "unknown")))
            identity_ok = (own_id is not None and own_id == record_id and expected_ids == {own_id}
                           and set(row.get("arxiv_ids", [])) == {own_id})
            title_ok = bool(canonical_title_key and row.get("title_key") == canonical_title_key)
            audit.update(native_id=own_id, native_identity_bound=identity_ok,
                         canonical_title_matches=title_ok, revision_date_basis=basis,
                         text_revision_date=revised.isoformat() if revised else None,
                         source_observed_date=observed.isoformat() if observed else None,
                         historical_text_available=historical,
                         historical_text_date_basis=historical_basis)
            if abstract is not None and identity_ok and title_ok and historical is True:
                audit["native_preference_eligible"] = True
                preferred.append((revised or observed, row))
        audits.append(audit)
    first = next((row for row in ordered if row.get("abstract")), None)
    chosen = first
    basis = "original_source_order_fallback" if first else "no_available_abstract"
    if mode == VERSION and preferred:
        chosen = sorted(preferred, key=lambda item: (-item[0].toordinal(), item[1]["raw_record_id"]))[0][1]
        basis = "native_same_identity_title_and_eligible_text_date"
    chosen_audit = next((row for row in audits if chosen and row["raw_record_id"] == chosen["raw_record_id"]), None)
    selected = chosen.get("abstract") if chosen else None
    return {"abstract": selected, "policy_version": mode,
            "chosen_raw_record_id": chosen["raw_record_id"] if chosen else None,
            "chosen_source": chosen["source"] if chosen else None,
            "abstract_sha256": text_sha256(selected), "basis": basis,
            "chosen_historical_text_available": chosen_audit["historical_text_available"] if chosen_audit else None,
            "different_abstract_values": len({row["abstract_sha256"] for row in audits if row["has_abstract"]}) > 1,
            "changed_from_original_first_abstract": bool(chosen and first
                                                        and chosen.get("abstract") != first.get("abstract")),
            "candidates": audits, "abstract_correctness_verified": None}
