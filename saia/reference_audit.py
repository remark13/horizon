"""Source identity audit, isolated from collection and detection.

Only official arXiv citation metadata are saved; no abstract/PDF/full text.
Expected titles are authored separately, not copied into assertions by the fetcher.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

import httpx

from saia import hybrid, runs


def title_key(text: str) -> str:
    return re.sub(r'[^\w]+', ' ', text.casefold()).strip()


class MetadataParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta = {}
        self.subjects = []
        self.in_subjects = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'meta' and attrs.get('name') in (
                'citation_title', 'citation_arxiv_id', 'citation_date', 'citation_online_date'):
            self.meta.setdefault(attrs['name'], []).append(attrs.get('content', ''))
        if tag == 'td' and 'subjects' in attrs.get('class', '').split():
            self.in_subjects = True

    def handle_endtag(self, tag):
        if tag == 'td':
            self.in_subjects = False

    def handle_data(self, data):
        if self.in_subjects:
            self.subjects.append(data)


def parse(html: str, requested_id: str, expected_title: str) -> dict:
    p = MetadataParser()
    p.feed(html)
    def one(name):
        values = p.meta.get(name, [])
        if len(values) != 1 or not values[0]:
            raise ValueError('Missing or ambiguous citation metadata: ' + name)
        return values[0]
    identifier, title = one('citation_arxiv_id'), one('citation_title')
    created = date.fromisoformat(one('citation_date').replace('/', '-'))
    online_values = p.meta.get('citation_online_date', [])
    if len(online_values) > 1:
        raise ValueError('Ambiguous citation_online_date')
    updated = date.fromisoformat(online_values[0].replace('/', '-')) if online_values else created
    categories = re.findall(r'\(([a-z-]+(?:\.[A-Za-z-]+)?)\)', ' '.join(p.subjects))
    if not categories or updated < created:
        raise ValueError('Missing categories or invalid chronology')
    return {'requested_id': requested_id, 'arxiv_id': identifier, 'title': title,
            'expected_title': expected_title, 'first_submission': created.isoformat(),
            'current_revision': updated.isoformat(), 'primary_category': categories[0],
            'categories': sorted(set(categories)),
            'status': 'verified_identity' if identifier == requested_id and title_key(title) == title_key(expected_title)
                      else 'identity_mismatch'}


def validate_report(report: dict, catalog: dict) -> None:
    expected = catalog.get('reference_titles') or {}
    if not expected or report.get('catalog_hash') != hybrid.digest(catalog):
        raise ValueError('Reference audit belongs to a different catalog or lacks expected titles')
    records = report.get('records', [])
    if len(records) != len(expected) or len({r['requested_id'] for r in records}) != len(records):
        raise ValueError('Reference audit is incomplete or duplicated')
    if {r['requested_id'] for r in records} != set(expected):
        raise ValueError('Reference audit does not cover catalog references')
    for r in records:
        ref = r['requested_id']
        if (r.get('status') != 'verified_identity' or r.get('arxiv_id') != ref
                or title_key(r.get('title', '')) != title_key(expected[ref])
                or r.get('request_url') != 'https://arxiv.org/abs/' + ref
                or r.get('http_status') != 200
                or not re.fullmatch(r'[0-9a-f]{64}', r.get('response_sha256', ''))):
            raise ValueError('Unverified primary source identity: ' + ref)
        created = date.fromisoformat(r['first_submission'])
        updated = date.fromisoformat(r['current_revision'])
        if updated < created or not r.get('categories') or r.get('primary_category') not in r['categories']:
            raise ValueError('Invalid primary source chronology or categories: ' + ref)
    by_id = {r['requested_id']: r for r in records}
    for c in catalog['cases']:
        if any(by_id[ref]['first_submission'] >= c['as_of_date'] for ref in c['arxiv_ids']):
            raise ValueError('Case seed was not published before its cutoff: ' + c['case_id'])


def collect(catalog: dict, output: Path, delay: float = 3.0) -> dict:
    expected = catalog.get('reference_titles') or {}
    if not expected:
        raise ValueError('Catalog requires independently specified reference_titles')
    report = {'version': 'reference-identity-audit-0.4.1', 'catalog_hash': hybrid.digest(catalog),
              'code_version': runs.code_version(), 'runtime': runs.runtime_snapshot(),
              'purpose': 'metadata_identity_only_not_detection_or_historical_text_recovery', 'records': []}
    with httpx.Client(follow_redirects=True, timeout=30, headers={'User-Agent': 'SAIA/0.4 metadata identity audit'}) as client:
        for i, (ref, title) in enumerate(sorted(expected.items())):
            if i:
                time.sleep(max(3.0, delay))
            url = 'https://arxiv.org/abs/' + ref
            row = {'requested_id': ref, 'request_url': url,
                   'fetched_at': datetime.now(timezone.utc).isoformat()}
            try:
                response = client.get(url)
                row.update(http_status=response.status_code,
                           response_sha256=hashlib.sha256(response.content).hexdigest())
                response.raise_for_status()
                row.update(parse(response.text, ref, title))
            except (httpx.HTTPError, ValueError) as error:
                row.update(status='source_or_parse_error', error=type(error).__name__)
            report['records'].append(row)
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(f"{i + 1}/{len(expected)} {ref}: {row['status']}", flush=True)
    return report


def revalidate(original: dict, catalog: dict) -> dict:
    """Recheck saved metadata after explicit expected-title edits; no refetch or truth labels."""
    report = json.loads(json.dumps(original))
    report.update(catalog_hash=hybrid.digest(catalog), parent_audit_hash=hybrid.digest(original),
                  revalidated_at=datetime.now(timezone.utc).isoformat(), code_version=runs.code_version())
    for r in report['records']:
        expected = catalog['reference_titles'].get(r['requested_id'])
        r['previous_identity_status'] = r['status']
        r['expected_title'] = expected
        if r.get('arxiv_id') and r.get('title'):
            r['status'] = ('verified_identity' if r['arxiv_id'] == r['requested_id']
                           and expected and title_key(r['title']) == title_key(expected)
                           else 'identity_mismatch')
    validate_report(report, catalog)
    return report


def main() -> int:
    from saia.evaluation_catalog import load
    parser = argparse.ArgumentParser(description='Audit official arXiv identities; never invoke detector')
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--export', type=Path, required=True)
    parser.add_argument('--revalidate', type=Path, help='Saved metadata; requires explicitly reviewed expected titles')
    args = parser.parse_args()
    catalog = load(args.catalog)
    if args.revalidate:
        if args.revalidate.resolve() == args.export.resolve():
            raise ValueError('Keep the original audit; use a different export path')
        report = revalidate(json.loads(args.revalidate.read_text()), catalog)
        args.export.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    else:
        report = collect(catalog, args.export)
    try:
        validate_report(report, catalog)
    except ValueError as error:
        print(error)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
