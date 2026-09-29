"""Read-only grouping of external records by declared identities, not similarity.

Source observations stay immutable. A group is a display family, never an
independent confirmation, a scientific research family or a funding total.
Only observed DOI endpoints are joined: no hidden records or network traversal.
"""
from __future__ import annotations

import re
from collections import defaultdict
from copy import deepcopy
from urllib.parse import urlsplit

from saia.hybrid import digest

VERSION = "external-material-grouping-v1"
MAX_RECORDS = 200
DOI_RELATIONS = frozenset({"IsVersionOf", "HasVersion", "IsNewVersionOf",
                           "IsPreviousVersionOf", "IsIdenticalTo"})
SOURCE_PRIORITY = {"nsf_awards": 0, "ukri_gtr": 0, "datacite": 0,
                   "openaire_projects": 10}


def doi(value: object) -> str | None:
    if (not isinstance(value, str) or len(value) > 500
            or any(ord(c) < 32 for c in value)
            or not re.fullmatch(r"10\.\d{4,9}/[^\s]+", value, re.IGNORECASE)):
        return None
    return value.casefold()


def _text(value: object, pattern: str, maximum: int = 300) -> str | None:
    if not isinstance(value, str) or len(value) > maximum:
        return None
    value = value.strip()
    return value if re.fullmatch(pattern, value, re.ASCII) else None


def _grant_key(scheme: str, value: object) -> dict | None:
    pattern = {"grant:US:NSF": r"\d{6,10}", "grant:GB:UKRI": r"[A-Za-z]{1,5}/[A-Za-z0-9]{3,20}/\d{1,3}",
               "grant:EU:EC": r"\d{5,9}"}[scheme]
    clean = _text(value, pattern)
    return {"scheme": scheme, "value": clean.upper()} if clean else None


def _openaire_grant(record: dict) -> dict | None:
    # Multiple funders, unknown jurisdictions and numeric UKRI internal codes
    # do not identify an exact grant. Do not infer the funder from a title.
    funders = record.get("funders")
    if not isinstance(funders, list) or len(funders) != 1 or not isinstance(funders[0], dict):
        return None
    funder = funders[0]
    agency = (funder.get("shortName"), funder.get("jurisdiction"))
    if not all(isinstance(v, str) for v in agency):
        return None
    scheme = {("NSF", "US"): "grant:US:NSF", ("UKRI", "GB"): "grant:GB:UKRI",
              ("EC", "EU"): "grant:EU:EC"}.get(agency)
    return _grant_key(scheme, record.get("grant_reference")) if scheme else None


def identity_metadata(source: str, record: dict) -> dict:
    """Project existing normalized metadata into a bounded identity passport."""
    keys, relations = [], []
    if source == "datacite":
        identifier = doi(record.get("doi"))
        if identifier:
            keys.append({"scheme": "doi", "value": identifier})
            related = record.get("related_dois")
            for entry in related[:20] if isinstance(related, list) else []:
                if not isinstance(entry, dict):
                    continue
                target = doi(entry.get("doi"))
                relation = entry.get("relation")
                if target and target != identifier and isinstance(relation, str) and relation in DOI_RELATIONS:
                    relations.append({"target": target, "relation": entry["relation"]})
    elif source == "nsf_awards":
        if key := _grant_key("grant:US:NSF", record.get("award_id")):
            keys.append(key)
    elif source in {"ukri_gtr", "openaire_projects"}:
        project = _text(record.get("project_id"), r"[\w:.-]{2,300}")
        if project:
            keys.append({"scheme": source + ":project", "value": project})
        key = (_grant_key("grant:GB:UKRI", record.get("grant_reference"))
               if source == "ukri_gtr" else _openaire_grant(record))
        if key:
            keys.append(key)
    return {"keys": keys, "relations": relations, "version": VERSION,
            "title_similarity_used": False, "doi_suffix_inference_used": False}


def _safe_url(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 4000 or any(ord(c) < 32 for c in value):
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.scheme in {"http", "https"} and parsed.hostname
                and parsed.username is None and parsed.password is None):
            return value  # Keep exact path, case, query and fragment: no URL guesses.
    except ValueError:
        pass
    return None


def _keys(material: dict) -> list[tuple[str, str]]:
    identity = material.get("identity_metadata")
    keys = identity.get("keys") if isinstance(identity, dict) else None
    result = []
    source = material.get("source")
    for key in keys[:4] if isinstance(keys, list) else []:
        if not isinstance(key, dict):
            continue
        scheme, value = key.get("scheme"), key.get("value")
        if not isinstance(scheme, str):
            continue
        clean = None
        if scheme == "doi" and source == "datacite" and material.get("group") == "software":
            clean = doi(value)
        elif (scheme in {"grant:US:NSF", "grant:GB:UKRI", "grant:EU:EC"}
              and material.get("group") == "funding"
              and source in {"nsf_awards", "ukri_gtr", "openaire_projects"}):
            # identity_metadata already checks the source's declared funder.
            allowed = {"nsf_awards": {"grant:US:NSF"}, "ukri_gtr": {"grant:GB:UKRI"},
                       "openaire_projects": {"grant:US:NSF", "grant:GB:UKRI", "grant:EU:EC"}}
            grant = _grant_key(scheme, value) if scheme in allowed[source] else None
            clean = grant["value"] if grant else None
        elif scheme == source + ":project" and source in {"ukri_gtr", "openaire_projects"}:
            clean = _text(value, r"[\w:.-]{2,300}")
        if clean:
            result.append((scheme, clean))
    return sorted(set(result))


def _record_ref(material: dict) -> dict:
    ref = {"source": material.get("source"), "observation_id": material.get("observation_id"), "url": material.get("url")}
    if material.get("record_id"):
        ref["record_id"] = material["record_id"]
    return ref


def _order(material: dict) -> tuple:
    return (SOURCE_PRIORITY.get(material.get("source"), 5), str(material.get("source", "")),
            str(material.get("url", "")), str(material.get("observation_id", "")))


def group_materials(materials: list[dict]) -> dict:
    """Add deterministic display groups, retaining every raw record and link.

    Connected components are used only for exact keys/declared DOI relations.
    Conflicting grant identities or dataset/software types reject the entire
    proposed component, avoiding order-dependent partial merges.
    """
    records = sorted(deepcopy(materials), key=_order)
    parents = list(range(len(records)))
    edges = []
    identity_keys = [_keys(m) for m in records]
    def root(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i
    def join(a, b, reason):
        if a == b or records[a].get("group") != records[b].get("group"):
            return
        parents[root(b)] = root(a)
        edges.append({"a": a, "b": b, **reason})
    within_limit = len(records) <= MAX_RECORDS
    if within_limit:
        by_key, by_url, by_doi = defaultdict(list), defaultdict(list), defaultdict(list)
        for i, material in enumerate(records):
            for scheme, value in identity_keys[i]:
                by_key[(material.get("group"), scheme, value)].append(i)
                if scheme == "doi":
                    by_doi[value].append(i)
            # A provider landing page does not identify a particular deal.
            if material.get("original_record_url_available") is False:
                continue
            if exact := _safe_url(material.get("url")):
                by_url[(material.get("group"), exact)].append(i)
        for (_, scheme, value), members in sorted(by_key.items()):
            for i in members[1:]:
                join(members[0], i, {"kind": "exact_identifier", "scheme": scheme, "value": value})
        for (_, exact), members in sorted(by_url.items()):
            for i in members[1:]:
                if (records[i].get("group") == "funding"
                        and records[members[0]].get("source") != records[i].get("source")
                        and not set(identity_keys[members[0]]) & set(identity_keys[i])):
                    continue  # A programme/contract pointing to a project is not its grant.
                join(members[0], i, {"kind": "exact_url", "url": exact})
        for i, material in enumerate(records):
            identity = material.get("identity_metadata") or {}
            if material.get("source") != "datacite" or material.get("group") != "software" or not any(k[0] == "doi" for k in identity_keys[i]):
                continue
            related = identity.get("relations") if isinstance(identity, dict) else None
            for entry in related[:20] if isinstance(related, list) else []:
                if not isinstance(entry, dict) or not isinstance(entry.get("relation"), str) or entry["relation"] not in DOI_RELATIONS:
                    continue
                target = doi(entry.get("target"))
                for j in by_doi.get(target, []):
                    join(i, j, {"kind": "declared_doi_relation", "relation": entry["relation"],
                                "target": target, "declared_by": _record_ref(material)})
    proposed = defaultdict(list)
    for i in range(len(records)):
        proposed[root(i)].append(i)
    conflicts, accepted = [], []
    rejected_indices = set()
    for members in proposed.values():
        reasons = []
        grants = defaultdict(set)
        for i in members:
            for scheme, value in identity_keys[i]:
                if scheme.startswith("grant:"):
                    grants[scheme].add(value)
        if any(len(values) > 1 for values in grants.values()) or len(grants) > 1:
            reasons.append("conflicting_grant_identity")
        types = {records[i].get("record_type") for i in members
                 if records[i].get("source") == "datacite"
                 and records[i].get("record_type") in {"research_dataset", "research_software"}}
        if len(types) > 1:
            reasons.append("conflicting_artifact_type")
        # Shared landing pages alone cannot identify different DOI resources.
        dois = {value for i in members for scheme, value in identity_keys[i] if scheme == "doi"}
        if len(dois) > 1:
            adjacency = defaultdict(set)
            for edge in edges:
                if edge["kind"] == "declared_doi_relation" and edge["a"] in members and edge["b"] in members:
                    for left in (v for s, v in identity_keys[edge["a"]] if s == "doi"):
                        for right in (v for s, v in identity_keys[edge["b"]] if s == "doi"):
                            adjacency[left].add(right)
                            adjacency[right].add(left)
            reached, pending = set(), [min(dois)]
            while pending:
                current = pending.pop()
                if current not in reached:
                    reached.add(current)
                    pending.extend(adjacency[current] - reached)
            if reached != dois:
                reasons.append("conflicting_doi_identity")
        if len(members) > 1 and reasons:
            conflicts.append({"reasons": reasons, "records": [_record_ref(records[i]) for i in members]})
            rejected_indices.update(members)
            accepted.extend([[i] for i in members])
        else:
            accepted.append(members)
    groups = []
    for members in accepted:
        selected = [records[i] for i in members]
        selected_indices = set(members)
        proofs = []
        for edge in edges:
            if edge["a"] in selected_indices and edge["b"] in selected_indices:
                proof = {k: v for k, v in edge.items() if k not in {"a", "b"}}
                if proof not in proofs:
                    proofs.append(proof)
        identifiers = [{"reference": _record_ref(records[i]), "keys": identity_keys[i],
                        "record_type": records[i].get("record_type")} for i in members]
        groups.append({"group_id": "external-group:" + digest({"version": VERSION, "records": identifiers}),
                       "group": selected[0].get("group"), "primary": selected[0], "records": selected,
                       "record_count": len(selected), "source_count": len({m.get("source") for m in selected}),
                       "status": "identity_conflict_not_merged" if members[0] in rejected_indices else "grouped_by_declared_identity" if len(selected) > 1 else "single_record",
                       "merge_evidence": sorted(proofs, key=digest), "funding_amounts_combined": False,
                       "independent_confirmation_count": None, "scientific_score_modified": False})
    groups.sort(key=lambda g: _order(g["primary"]))
    return {"version": VERSION, "groups": groups, "raw_record_count": len(records),
            "display_group_count": len(groups), "collapsed_record_count": len(records) - len(groups),
            "merged_group_count": sum(g["record_count"] > 1 for g in groups),
            "conflicts": sorted(conflicts, key=digest), "grouping_limit_exceeded": not within_limit,
            "global_deduplication_complete": False, "independent_confirmation_count": None,
            "funding_amounts_combined": False, "source_records_modified": False,
            "scientific_score_modified": False, "title_similarity_used": False,
            "unobserved_related_records_fetched": False}
