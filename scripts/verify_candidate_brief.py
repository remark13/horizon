#!/usr/bin/env python3
"""Bounded local checkpoint. Optional genuine developer hypothesis, not a verdict.

Existing user notes are never overwritten. The example only uses the saved
Bubble Planner reference, whose original abstract was inspected separately.
No scientific analysis job or external source collection is started here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from pathlib import Path
from urllib.request import Request, urlopen


def normalized_headers(headers) -> dict[str, str]:
    return {key.lower(): value for key, value in headers.items()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default='http://127.0.0.1:8082')
    parser.add_argument('--mission', default='universal-monthly-5177517d-5c5b-4fb8-b987-a66589771915')
    parser.add_argument('--score', type=int, default=9509)
    parser.add_argument('--candidate', type=int, default=7061)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--record-example', action='store_true')
    args = parser.parse_args()
    if args.base not in {'http://127.0.0.1:8082', 'http://127.0.0.1:8080'}:
        raise ValueError('Use only the explicitly allowed local application.')
    prefix = f'/signals/{args.mission}/{args.candidate}'
    params = f'?score_run_id={args.score}'
    def fetch(path: str, payload: dict | None = None, raw: bool = False):
        data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
        request = Request(args.base + path, data=data, headers={'Content-Type': 'application/json'} if data else {})
        with urlopen(request, timeout=25) as response:
            body = response.read(10_000_001)
            if len(body) > 10_000_000:
                raise ValueError('Checkpoint response exceeds size bound.')
            return (body, normalized_headers(response.headers)) if raw else json.loads(body)
    before = fetch(f'/signals/{args.mission}{params}')
    initial = fetch(prefix + '/analysis-note' + params)
    catalogue = fetch(prefix + '/analysis-references' + params)
    action = 'read_only'
    if args.record_example and initial['revision'] == 0:
        reference = next((r for r in catalogue['references']
                          if r['kind'] == 'publication' and r['url'] == 'https://arxiv.org/abs/2202.12177'), None)
        if reference is None:
            raise ValueError('The inspected example publication is not in this card; do not substitute another source.')
        content = {
            'author': 'SAIA · аналитическая заметка разработчика',
            'summary': 'В карточке собраны разные подходы к автономному полёту. Для предметного вывода стоит отдельно проверить планирование траекторий и состав исследований, а не считать весь кластер одной новой технологией. Ниже — условная гипотеза по Bubble Planner, не подтверждение слабого сигнала или внедрения.',
            'pestle': [{'dimension': 'technological',
                'text': 'Если планирование траекторий в загромождённой среде окажется устойчивым за пределами испытаний авторов, оно может расширить задачи автономного полёта. Работа Bubble Planner — основание для постановки этой проверки, а не доказательство зрелости всей исследовательской темы.',
                'effect': 'opportunity', 'horizon': 'not_assessed',
                'dependencies': 'Независимое воспроизведение; проверка на других платформах, при движущихся препятствиях и изменяющихся условиях; отдельная оценка надёжности.',
                'evidence_ids': [reference['id']]}],
            'industry_impacts': [{'industry': 'Промышленная инспекция',
                'text': 'Возможная цепочка влияния: устойчивое автономное планирование → полёт среди препятствий → меньше ручного управления при осмотре объектов. Это гипотеза применения; публикация не подтверждает такое внедрение или экономический эффект.',
                'effect': 'opportunity', 'horizon': 'not_assessed',
                'dependencies': 'Испытания на объектах отрасли, требования к безопасности, стоимость оборудования и допустимые условия эксплуатации.',
                'evidence_ids': [reference['id']]}],
        }
        operation = str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps({'binding': initial['binding'], 'content': content}, sort_keys=True, ensure_ascii=False)))
        result = fetch(prefix + '/analysis-note' + params, {**content, 'expected_revision': 0, 'operation_id': operation})
        if result['scientific_results_modified'] is not False:
            raise ValueError('Example must not modify science.')
        action = 'developer_hypothesis_recorded'
    elif args.record_example:
        action = 'existing_note_preserved'
    latest = fetch(prefix + '/analysis-note' + params)
    brief_bytes, headers = fetch(prefix + '/brief' + params, raw=True)
    after = fetch(f'/signals/{args.mission}{params}')
    scientific_sha = lambda value: hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    text = brief_bytes.decode('utf-8')
    required = ['PESTLE: возможные последствия', 'Возможное влияние на отрасли', 'Полнота оснований, не вероятность']
    if any(term not in text for term in required) or '<script' in text.lower() or scientific_sha(before) != scientific_sha(after):
        raise ValueError('Brief content or scientific preservation check failed.')
    if 'attachment;' not in headers.get('content-disposition', ''):
        raise ValueError('Brief response is not downloadable.')
    args.output.mkdir(parents=True, exist_ok=True)
    brief_path = args.output / f'card-{args.candidate}.html'
    brief_path.write_bytes(brief_bytes)
    report = {'scope': 'source/UI checkpoint, not accuracy or SLA benchmark', 'action': action,
              'health': fetch('/health'), 'mission_id': args.mission, 'score_run_id': args.score,
              'candidate_id': args.candidate, 'note': latest, 'reference_catalog': catalogue,
              'scientific_cards_sha256': scientific_sha(after), 'scientific_cards_unchanged': True,
              'brief_sha256': hashlib.sha256(brief_bytes).hexdigest(), 'brief_bytes': len(brief_bytes),
              'headers': headers, 'external_sources_collected': False, 'scientific_jobs_started': False}
    (args.output / 'checkpoint.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'action': action, 'version': report['health']['version'], 'note_revision': latest['revision'],
                      'brief': str(brief_path.resolve()), 'scientific_cards_unchanged': True,
                      'brief_bytes': len(brief_bytes)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
