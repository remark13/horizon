"""File-only development regression on previously exported identity evidence.

No database/model access, no discovery or scientific accuracy evaluation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone, date
from pathlib import Path

from saia.hybrid import digest
from saia.normalize import canonical_identifiers, doi_conflicts, identity_policy, parse_arxiv
from saia.quality import POLICY_VERSION, decide
from saia import methodology, runs


def replay(evidence_path: Path, as_of: str) -> dict:
    date.fromisoformat(as_of)
    raw_bytes = evidence_path.read_bytes()
    evidence = json.loads(raw_bytes)
    body = {k: v for k, v in evidence.items() if k != 'report_payload_sha256'}
    if digest(body) != evidence['report_payload_sha256']:
        raise ValueError('Input evidence checksum mismatch.')
    if evidence['version'] != 'identity-family-metadata-audit-0.4.4':
        raise ValueError('Unsupported evidence schema.')
    deposits = [deposit for family in evidence['families'] for deposit in family['deposits']]
    if len(deposits) != evidence['deposit_count']:
        raise ValueError('Input evidence roster mismatch.')
    parsed = [parse_arxiv(d['payload']) for d in deposits]
    conflicts = doi_conflicts(parsed)
    effective_quality = methodology.load_default()
    implementation_tree_version = runs.source_tree_version()
    observed_at = datetime.now(timezone.utc).isoformat()
    rows = []
    ids = set()
    for deposit, record in zip(deposits, parsed, strict=True):
        canonical = canonical_identifiers(record, conflicts)
        native = [v for k, v in canonical['identifiers'] if k == 'arxiv']
        if len(native) != 1 or native[0] != deposit['source_record_id'] or native[0] in ids:
            raise ValueError('Distinct native arXiv identity required for this replay.')
        ids.add(native[0])
        withheld = [conflicts[doi] for doi in canonical['_withheld_dois']]
        q = decide(title=record['title'], abstract=record['abstract'], publication_year=record['publication_year'],
                   date_is_imprecise=False, terms=[], sources={'arxiv'}, openalex_payloads=[],
                   arxiv_payloads=[deposit['payload']], categories=['cs.AI', 'cs.LG', 'stat.ML', 'cs.NE', 'cs.CL', 'cs.CV'],
                   as_of_date=as_of, identity_conflicts=withheld, status_observed_at=observed_at,
                   policy=effective_quality.raw['publication_quality'])
        rows.append({'source_record_id': native[0], 'input_raw_record_id': deposit['raw_record_id'],
                     'canonical_identifiers': canonical['identifiers'], 'withheld_assertions': withheld,
                     'quality_decision': q.decision, 'flags': q.flags, 'reasons': list(q.reasons)})
    result = {'version': 'identity-assertion-development-replay-0.4.5', 'exported_at': observed_at,
              'as_of_date': as_of, 'normalization_policy': identity_policy('conservative'),
              'quality_policy_version': POLICY_VERSION, 'input_file_bytes_sha256': hashlib.sha256(raw_bytes).hexdigest(),
              'methodology_hash': effective_quality.config_hash,
              'effective_quality_config': effective_quality.raw['publication_quality'],
              'implementation_tree_version': implementation_tree_version,
              'input_payload_sha256': evidence['report_payload_sha256'],
              'replay_code_bytes_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'input_families': evidence['family_count'], 'deposits': len(rows),
              'native_source_identities_preserved': len(ids), 'ambiguous_dois': len(conflicts),
              'quality_counts': {decision: sum(r['quality_decision'] == decision for r in rows)
                                 for decision in ['include', 'quarantine', 'exclude']},
              'rows': rows, 'database_accessed': False, 'embedding_computed': False,
              'scientific_accuracy_evaluated': False,
              'limitations': ['Native source identities are not adjudicated independent research publications.',
                              'A withdrawal request is not verified withdrawal; current official-page evidence is not incorporated by this replay.',
                              'This development regression does not test weak-signal precision or a new user query.']}
    if runs.source_tree_version() != implementation_tree_version:
        raise ValueError('Implementation changed during replay; no completed output.')
    result['report_payload_sha256'] = digest(result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--as-of', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Output exists; choose a new versioned path.')
    report = replay(args.evidence, args.as_of)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    print(json.dumps({k: report[k] for k in ['deposits', 'native_source_identities_preserved', 'ambiguous_dois', 'quality_counts', 'report_payload_sha256']}))


if __name__ == '__main__':
    main()
