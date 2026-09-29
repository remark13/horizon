"""Read-only smoke check of saved card publication pages; no detector runs."""
from __future__ import annotations

import argparse
import json
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import urlopen


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mission')
    parser.add_argument('score', type=int)
    parser.add_argument('--base', default='http://127.0.0.1:8080')
    args = parser.parse_args()

    def get(path):
        with urlopen(args.base + path, timeout=30) as response:
            return json.load(response)

    mission = quote(args.mission, safe='')
    packet = get(f'/triage/{mission}?score_run_id={args.score}&limit=100')
    counts = []
    for item in packet['queue']:
        card = item['card']
        path = f'/signals/{mission}/{card["candidate_id"]}/publications?score_run_id={args.score}&limit=20'
        page = get(path)
        ids = []
        while True:
            assert page['composition_sha256'] == card['composition_sha256']
            assert page['ranking_changed'] is False
            ids.extend(work['work_id'] for work in page['works'])
            if page['next_offset'] is None:
                break
            page = get(path + f'&offset={page["next_offset"]}')
        assert len(ids) == page['total'] == len(set(ids))
        counts.append(page['total'])
    assert counts, 'No saved cards to check'
    candidate = packet['queue'][0]['candidate_id']
    for path, status in (
        (f'/signals/wrong-mission/{candidate}/publications?score_run_id={args.score}', 404),
        (f'/signals/{mission}/{candidate}/publications?score_run_id={args.score}&limit=101', 422),
    ):
        try:
            get(path)
            raise AssertionError(f'Expected HTTP {status}')
        except HTTPError as error:
            assert error.code == status
    print(json.dumps({'health': get('/health'), 'cards_checked': len(counts),
                      'composition_counts': counts, 'pagination_unique': True,
                      'wrong_mission_rejected': True, 'invalid_limit_rejected': True}))


if __name__ == '__main__':
    main()
