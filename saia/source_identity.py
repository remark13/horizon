"""Primary arXiv DOI identity is distinct from bibliographic location links."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

ARXIV_DOI = re.compile(r'^10\.48550/arxiv\.([0-9]{4}\.[0-9]{4,5})$', re.I)
ARXIV_PATH = re.compile(r'^/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5})(?:v\d+)?(?:\.pdf)?/?$', re.I)


def openalex_arxiv_identity(payload: dict) -> dict:
    ids = payload.get('ids') or {}
    doi = str(payload.get('doi') or ids.get('doi') or '').strip().lower()
    doi = re.sub(r'^https?://(?:dx\.)?doi\.org/', '', doi)
    match = ARXIV_DOI.fullmatch(doi)
    primary = match.group(1) if match else None
    values = list(ids.values())
    for location in payload.get('locations') or []:
        values.extend([location.get('landing_page_url'), location.get('pdf_url')])
    linked = set()
    for value in values:
        if not isinstance(value, str):
            continue
        try:
            url = urlsplit(value)
        except ValueError:
            continue
        found = ARXIV_PATH.fullmatch(url.path) if url.scheme in {'http', 'https'} and url.hostname in {'arxiv.org', 'www.arxiv.org', 'export.arxiv.org'} else None
        if found:
            linked.add(found.group(1))
    # Location order is not an identity resolution policy.
    selected = primary if primary else next(iter(linked)) if len(linked) == 1 else None
    conflict = bool(linked - {primary}) if primary else len(linked) > 1
    return {'canonical_arxiv_id': selected, 'primary_arxiv_doi_id': primary,
            'linked_arxiv_ids': sorted(linked), 'conflicting_arxiv_links': conflict,
            'identity_basis': 'arxiv_doi' if primary else 'unique_location' if selected else 'unresolved'}
