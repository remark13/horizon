#!/usr/bin/env python3
"""Small live probe of two keyless sources, outside science and model pipelines.

Only six fixed bounded requests (three subjects, two sources). Normalized
metadata is saved, never raw article bodies, abstracts, files or model input.
No PostgreSQL writes, expert responses or scientific jobs in this probe.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from saia import osti_evidence, rss_evidence
from saia.external_evidence_store import verify


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Use a new output directory; do not overwrite a checkpoint.')
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=5*366)
    pairs = [(source, text) for text in ('battery', 'nuclear', 'quantum') for source in ('osti_gov','nist_news_rss')]
    def probe(pair):
        source, text = pair
        tick = time.monotonic()
        scope = 'source-probe-0.4.53:'+text
        if source == 'osti_gov':
            result = osti_evidence.fetch(osti_evidence.OSTIQuery(scope,text,start,today,today,5),timeout=12)
        else:
            result = rss_evidence.fetch(rss_evidence.RSSQuery(scope,text,start,today,today,source,5),timeout=12)
        verify(result)
        if result['scientific_score_modified'] or result.get('stores_full_text') or result.get('stores_article_text'):
            raise ValueError('No full text, models or scientific-score changes permitted.')
        return {'source':source,'query':text,'elapsed_seconds':round(time.monotonic()-tick,3),'report':result}
    with ThreadPoolExecutor(max_workers=2) as executor:
        probes = list(executor.map(probe,pairs))
    summary = {'version':'live-osti-nist-probe-0.4.53','retrieved_at':datetime.now(timezone.utc).isoformat(),
        'source_count':2,'requested_subjects':3,'requested_source_calls':6,
        'scientific_jobs_started':False,'models_called':False,'database_writes':False,
        'accuracy_improvement_measured':False,'results':[
            {'source':p['source'],'query':p['query'],'status':p['report']['status'],
             'records':len(p['report']['observations']) if isinstance(p['report']['observations'],list) else None,
             'elapsed_seconds':p['elapsed_seconds'],'request_url':p['report']['request']['url'],
             'titles':[r['title'] for r in p['report']['observations'] or []],
             'urls':[r['url'] for r in p['report']['observations'] or []],
             'rejected_records':p['report'].get('rejected_records')} for p in probes]}
    args.output.mkdir(parents=True)
    for probe_result in probes:
        (args.output/(probe_result['source']+'-'+probe_result['query']+'.json')).write_text(
            json.dumps(probe_result['report'],ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (args.output/'checkpoint.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False))


if __name__ == '__main__':
    main()
