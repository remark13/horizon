"""Консервативная группировка работ, не доказательство лабораторной независимости."""
from __future__ import annotations


def author_identity_disagreement(payloads: list[dict]) -> bool:
    """Conflicting complete author-ID sets for one already canonical work.

    Flags ambiguity, not additional teams or a name-based identity resolution.
    Incomplete bylines cannot establish disagreement by missing IDs alone.
    """
    complete_sets = set()
    for payload in payloads:
        byline = payload.get('authorships') or []
        ids = [(entry.get('author') or {}).get('id') for entry in byline]
        if ids and all(ids):
            complete_sets.add(frozenset(ids))
    return len(complete_sets) > 1


def team_proxy(identities: list[dict], min_overlap: float,
               min_identity_coverage: float) -> dict:
    if not identities:
        return {'teams': None, 'identity_coverage': 0., 'affiliation_coverage': 0.}
    reliable = [r for r in identities if r['authors'] and not r.get('identity_conflict')]
    coverage = len(reliable) / len(identities)
    affiliation = sum(bool(r['organisations']) for r in identities) / len(identities)
    if coverage < min_identity_coverage:
        return {'teams': None, 'identity_coverage': coverage, 'affiliation_coverage': affiliation}
    parent = list(range(len(reliable)))
    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i, first in enumerate(reliable):
        for j in range(i):
            second = reliable[j]
            a, b = set(first['authors']), set(second['authors'])
            common_org = bool(set(first['organisations']) & set(second['organisations']))
            # Общая организация консервативно объединяет; один общий автор
            # среди большой команды не объединяет всю область транзитивно.
            if common_org or len(a & b) / len(a | b) >= min_overlap:
                parent[root(i)] = root(j)
    return {'teams': len({root(i) for i in range(len(reliable))}),
            'identity_coverage': coverage, 'affiliation_coverage': affiliation}
