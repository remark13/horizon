"""Conservative deposit-level status evidence, not a verdict on an entire field."""
from __future__ import annotations

import re

WITHDRAWN = re.compile(r'\bthis\s+(?:paper|submission|article)\s+has\s+been\s+withdrawn\b', re.I)
REQUEST = re.compile(r'\b(?:we|i)\s+(?:would\s+like|wish)\s+to\s+withdraw\b', re.I)


def status_evidence(arxiv_payloads: list[dict], openalex_payloads: list[dict]) -> list[dict]:
    evidence = []
    for payload in arxiv_payloads:
        status, field, fragment = 'not_reported', None, None
        comment = str(payload.get('arxiv_comment') or payload.get('comments') or '')
        # A comment about another study is not a withdrawal of this deposit.
        for key, text in [('arxiv_comment', comment), ('summary', str(payload.get('summary') or '')[:200])]:
            match = WITHDRAWN.search(text)
            if match:
                status, field, fragment = 'withdrawn', key, text[max(0, match.start() - 40):match.end() + 160]
                break
        if status == 'not_reported' and REQUEST.search(comment):
            status, field, fragment = 'withdrawal_requested', 'arxiv_comment', comment[:300]
        evidence.append({'source': 'arxiv', 'source_record_id': payload.get('id'),
                         'raw_record_id': payload.get('_status_origin_raw_record_id'),
                         'status': status, 'evidence_field': field, 'evidence_fragment': fragment,
                         'status_effective_date': None,
                         'date_note': 'Дата revision не используется как дата отзыва.'})
    for payload in openalex_payloads:
        retracted = payload.get('is_retracted') is True
        evidence.append({'source': 'openalex', 'source_record_id': payload.get('id'),
                         'raw_record_id': payload.get('_status_origin_raw_record_id'),
                         'status': 'retracted' if retracted else 'not_reported',
                         'evidence_field': 'is_retracted' if retracted else None,
                         'evidence_fragment': 'is_retracted=true' if retracted else None,
                         'status_effective_date': None,
                         'date_note': 'Отсутствие флага не доказывает валидность исследования.'})
    return evidence
