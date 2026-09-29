"""Fresh assessment of exact hybrid compositions; never inherit topic scores.

Read-only DB input and exclusive report export. Unknown scientific evidence
is not inferred from absence of review words or from mirrored metadata.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import date
from pathlib import Path

import numpy as np
import yaml

from saia import db, hybrid, methodology, runs, terminology
from saia.candidates import parse_vector, percentile, prevalence_ranks
from saia.cluster import window_of
from saia.measurement import POLICY_PATH as MEASUREMENT_PATH, WindowCounts, analyze_series
from saia.research_identity import team_proxy
from saia.scoring import CandidateMetrics, assess_candidate

POLICY_PATH = Path(__file__).resolve().parents[1] / 'config' / 'composition-assessment.v0.4.yaml'


def unit_vectors(vectors: list) -> np.ndarray | None:
    if not vectors:
        return None
    try:
        matrix = np.asarray(vectors, dtype=np.float64)
    except (ValueError, TypeError):
        raise ValueError('Векторы должны иметь одинаковую размерность.') from None
    if matrix.ndim != 2 or matrix.shape[1] == 0 or not np.isfinite(matrix).all():
        raise ValueError('Векторы должны быть конечными и непустыми.')
    norms = np.linalg.norm(matrix, axis=1)
    if not np.isfinite(norms).all() or (norms == 0).any():
        raise ValueError('Нулевой или некорректный вектор не является измерением связности.')
    return matrix / norms[:, None]


def exact_coherence(vectors: list) -> float | None:
    """Exact off-diagonal mean in O(n*d), without a quadratic n*n matrix."""
    matrix = unit_vectors(vectors)
    if matrix is None or len(matrix) < 2:
        return None
    n = len(matrix)
    total = matrix.sum(axis=0)
    return float(np.clip((total @ total - n) / (n * (n - 1)), -1., 1.))


def text_hash(docs: list[terminology.PublicationText]) -> str:
    return hashlib.sha256(json.dumps([
        [d.work_id, d.published_at.isoformat(), d.title, d.abstract] for d in docs
    ], ensure_ascii=False).encode()).hexdigest()


def prepare(snapshot, docs, evidence, measurement_policy):
    by_id = {d.work_id: d for d in docs}
    if len(by_id) != len(docs) or set(by_id) != set(snapshot['eligible_work_ids']) or set(evidence) != set(by_id):
        raise ValueError('Вход оценки должен точно совпасть с допущенным корпусом снимка.')
    if text_hash(docs) != snapshot['input_text_hash']:
        raise ValueError('Тексты или порядок входа изменились относительно снимка.')
    as_of = date.fromisoformat(snapshot['as_of_date'])
    end = date.fromisoformat(snapshot['period_end_exclusive'])
    start = date.fromisoformat(snapshot['period_from']) if snapshot['period_from'] else None
    if end > as_of or any(d.published_at >= end or (start and d.published_at < start) for d in docs):
        raise ValueError('В корпус попала будущая или внепериодная работа.')
    vectors, dimension = {}, None
    for work_id, row in evidence.items():
        if row.get('vector') is not None:
            unit = unit_vectors([row['vector']])[0]
            if dimension is not None and len(unit) != dimension:
                raise ValueError('Размерности векторов корпуса различаются.')
            dimension = len(unit)
            vectors[work_id] = unit
        quality = row['source_quality']
        if not np.isfinite(quality) or not 0 <= quality <= 1:
            raise ValueError('Качество метаданных должно быть в пределах 0–1.')
    prepared, seen = [], set()
    dates = Counter(d.published_at for d in docs)
    for c in snapshot['candidates']:
        ids, cid, channels = c['work_ids'], c['candidate_id'], c['channels']
        if (cid in seen or not ids or len(set(ids)) != len(ids) or not set(ids) <= set(by_id)
                or cid != 'hybrid:' + hybrid.digest(sorted(ids))[:20]
                or not channels or len(set(channels)) != len(channels)
                or set(channels) - {'lexical', 'semantic'}):
            raise ValueError('Некорректный ID, канал или состав кандидата.')
        seen.add(cid)
        intervals, used_dates = [], set()
        for p in c['publication_series']['points']:
            a, b = date.fromisoformat(p['start']), date.fromisoformat(p['end'])
            count = sum(a <= by_id[i].published_at < b for i in ids)
            total = sum(n for day, n in dates.items() if a <= day < b)
            if count != p['topic_works'] or total != p['corpus_works']:
                raise ValueError('Числитель или знаменатель снимка не совпал с входом.')
            used_dates.update(day for day in dates if a <= day < b)
            intervals.append(WindowCounts(a, b, count, total, p['complete'], p['coverage_comparable']))
        if used_dates != set(dates):
            raise ValueError('Ряд не покрывает все даты входа.')
        series = analyze_series(intervals, as_of, measurement_policy)
        first = min(by_id[i].published_at for i in ids)
        first_window = window_of(first, snapshot['window_step'])
        seed_ids = [i for i in ids if window_of(by_id[i].published_at, snapshot['window_step']) == first_window]
        seed = None
        if all(i in vectors for i in seed_ids):
            mean = np.mean([vectors[i] for i in seed_ids], axis=0)
            if np.linalg.norm(mean) > 0:
                seed = unit_vectors([mean])[0]
        full = [p for p in series['points'] if p['complete']]
        recent = full[-int(measurement_policy['history_windows']):]
        embedded = []
        for p in recent:
            a, b = date.fromisoformat(p['start']), date.fromisoformat(p['end'])
            n = sum(a <= by_id[i].published_at < b for i in vectors)
            embedded.append({**{k: p[k] for k in ('start', 'end', 'corpus_works')},
                             'embedded_works': n, 'coverage': n / p['corpus_works'] if p['corpus_works'] else None})
        embedding_coverage = (min(p['coverage'] for p in embedded)
                              if len(embedded) == int(measurement_policy['history_windows'])
                              and all(p['coverage'] is not None for p in embedded) else None)
        prepared.append({'candidate': c, 'cohort': tuple(sorted(channels)), 'series': series,
                         'first': first, 'first_window': first_window, 'seed': seed,
                         'seed_work_ids': seed_ids, 'embedding_coverage': embedding_coverage,
                         'embedding_coverage_by_window': embedded, 'share': full[-1]['share'] if full else None})
    return prepared, by_id, vectors, as_of


def assess(snapshot, docs, evidence, config=None, policy=None, measurement_policy=None,
           external_evidence_provenance=None):
    config = config or methodology.load_default()
    policy = policy if policy is not None else yaml.safe_load(POLICY_PATH.read_text())
    measurement_policy = measurement_policy if measurement_policy is not None else yaml.safe_load(MEASUREMENT_PATH.read_text())
    if (policy['peer_scope'] != 'exact_channel_set'
            or policy['novelty_basis'] != 'first_calendar_window_centroid_vs_earlier_candidate_seeds'
            or policy['primary_sources_basis'] != 'unknown_without_verified_content_labels'
            or policy['coherence_basis'] != 'exact_mean_pairwise_cosine_of_all_available_vectors'):
        raise ValueError('Неподдерживаемый способ оценки состава.')
    weights = policy['data_completeness_weights']
    floor = policy['min_last_full_window_share_for_widespread']
    if not np.isfinite(floor) or not 0 < floor <= 1:
        raise ValueError('Минимальная доля для распространённости должна быть в (0, 1].')
    if (set(weights) != {'abstract', 'affiliation'} or any(not np.isfinite(v) or v < 0 for v in weights.values())
            or not np.isclose(sum(weights.values()), 1.)):
        raise ValueError('Некорректные веса полноты данных.')
    prepared, by_id, vectors, as_of = prepare(snapshot, docs, evidence, measurement_policy)
    cfg = config.raw['publication_measurement']
    min_peers, rank = int(cfg['min_percentile_peers']), cfg['percentile_rank']
    rows = []
    for cohort in sorted({r['cohort'] for r in prepared}):
        peers = [r for r in prepared if r['cohort'] == cohort]
        for r in peers:
            previous = [p['seed'] for p in peers if p['first_window'] < r['first_window'] and p['seed'] is not None]
            r['novelty_raw'] = (1. - float(np.max(np.asarray(previous) @ r['seed']))
                                if previous and r['seed'] is not None else None)
        novelties = [r['novelty_raw'] for r in peers if r['novelty_raw'] is not None]
        slopes = [r['series']['share_slope_per_window'] for r in peers if r['series']['share_slope_per_window'] is not None]
        prevalences, prevalence_count = prevalence_ranks(
            {r['candidate']['candidate_id']: r['share'] for r in peers}, min_peers, rank, cfg['prevalence_peer_scope'])
        for r in peers:
            c, series = r['candidate'], r['series']
            ids = c['work_ids']
            identities = [{'authors': evidence[i]['authors'], 'organisations': evidence[i]['organisations'],
                           'identity_conflict': bool(evidence[i].get('author_identity_conflict'))
                           if policy.get('reject_author_identity_conflicts', False) else False} for i in ids]
            identity = team_proxy(identities, float(cfg['team_overlap_jaccard_min']), float(cfg['min_author_identity_coverage']))
            org_counts = Counter(o for v in identities for o in set(v['organisations']))
            affiliated = sum(bool(v['organisations']) for v in identities)
            single_org_share = max(org_counts.values()) / affiliated if affiliated else None
            orgs = len(org_counts) if identity['affiliation_coverage'] >= float(cfg['min_affiliation_coverage']) else None
            teams = identity['teams']
            diffusion = (min(1., teams / int(cfg['independent_team_full_credit'])) * (1. - single_org_share)
                         if teams is not None and single_org_share is not None else None)
            novelty_pct = percentile(r['novelty_raw'], novelties, min_peers, rank)
            momentum_pct = percentile(series['share_slope_per_window'], slopes, min_peers, rank)
            available = [vectors[i] for i in ids if i in vectors]
            observed_coherence = exact_coherence(available)
            coherence = observed_coherence if len(available) == len(ids) else None
            abstract_ratio = sum(bool(by_id[i].abstract) for i in ids) / len(ids)
            persistence = (min(1., series['consecutive_active_full_windows'] / int(cfg['persistence_full_credit_windows']))
                           if series['consecutive_active_full_windows'] is not None else None)
            normalized = {'novelty': novelty_pct / 100 if novelty_pct is not None else None,
                          'momentum': momentum_pct / 100 if momentum_pct is not None else None,
                          'persistence': persistence, 'independent_diffusion': diffusion, 'coherence': coherence}
            penalties = {'missing_abstract': config.penalties['missing_abstract']} if abstract_ratio < float(cfg['missing_abstract_threshold']) else {}
            metrics = CandidateMetrics(
                doc_count=len(ids), independent_orgs=orgs, independent_teams=teams, single_org_share=single_org_share,
                maturity_percentile=prevalences[c['candidate_id']], novelty_percentile=novelty_pct,
                windows_present=series['consecutive_active_full_windows'], momentum_percentile=momentum_pct,
                primary_sources=None, age_years=(as_of - r['first']).days / 365.25,
                normalized=normalized, penalties=penalties,
                share_slope=series['share_slope_per_window'], share_change=series['share_change'],
                consecutive_active_windows=series['consecutive_active_full_windows'],
                # false here means no verified coverage passport, not proof of
                # incompatibility. Preserve the missing evidence as unknown.
                coverage_comparable=True if series['coverage_comparable'] else None,
                coherence_calibrated=True if snapshot['provenance']['embedding_model'] in cfg['coherence_calibrated_models'] else None,
                embedding_coverage=r['embedding_coverage'],
                publication_share=r['share'],
                confidence_components={
                    'data_completeness': weights['abstract'] * abstract_ratio + weights['affiliation'] * identity['affiliation_coverage'],
                    'source_quality': sum(evidence[i]['source_quality'] for i in ids) / len(ids),
                    'confirmation_independence': (diffusion or 0.) * identity['identity_coverage']})
            assessment = assess_candidate(metrics, config,
                                          policy['min_last_full_window_share_for_widespread']).to_dict()
            rows.append({'candidate_id': c['candidate_id'], 'label': c['label'], 'work_ids': ids,
                         'channels': c['channels'], 'composition_sha256': hybrid.digest(sorted(ids)),
                         'assessment': assessment, 'normalized': normalized,
                         'observed': {'document_support': len(ids), 'coherence': coherence,
                                      'available_vector_coherence': observed_coherence, 'vector_works': len(available),
                                      'independent_teams_proxy': teams, 'independent_orgs_proxy': orgs,
                                      'single_org_share_on_affiliated_works': single_org_share, **identity,
                                      **({'author_identity_conflict_works': sum(v['identity_conflict'] for v in identities)}
                                         if policy.get('reject_author_identity_conflicts', False) else {}),
                                      'abstract_completeness': abstract_ratio, 'primary_sources': None,
                                      'novelty_raw': r['novelty_raw'], 'novelty_percentile': novelty_pct,
                                      'novelty_seed_work_ids': r['seed_work_ids'], 'novelty_seed_window': r['first_window'],
                                      'peer_cohort': list(cohort), 'peer_compositions': len(peers),
                                      'prevalence_percentile': prevalences[c['candidate_id']], 'active_prevalence_peers': prevalence_count,
                                      'momentum_percentile': momentum_pct, 'publication_share': r['share'],
                                      'embedding_coverage': r['embedding_coverage'],
                                      'embedding_coverage_by_window': r['embedding_coverage_by_window']},
                         'publication_series': series,
                         'blocking_unknowns': [g['gate'] for g in assessment['gates'] if g['severity'] == 'block' and g['passed'] is None],
                         'blocking_failures': [g['gate'] for g in assessment['gates'] if g['severity'] == 'block' and g['passed'] is False]})
    return report(snapshot, evidence, rows, config, policy, measurement_policy,
                  external_evidence_provenance)


def report(snapshot, evidence, rows, config, policy, measurement_policy,
           external_evidence_provenance=None):
    rows.sort(key=lambda r: r['candidate_id'])
    result = {'version': policy['version'], 'snapshot_id': snapshot['snapshot_id'],
              'snapshot_content_sha256': hybrid.digest(snapshot), 'provenance': snapshot['provenance'],
              'as_of_date': snapshot['as_of_date'], 'effective_policy': policy, 'policy_hash': hybrid.digest(policy),
              'effective_methodology': config.raw, 'methodology_hash': config.config_hash,
              'effective_measurement_policy': measurement_policy, 'measurement_policy_hash': hybrid.digest(measurement_policy),
              'input_text_hash': snapshot['input_text_hash'], 'input_evidence_sha256': hybrid.digest([
                  {'work_id': i, **{k: evidence[i][k] for k in ('source_quality', 'authors', 'organisations')},
                   **({'author_identity_conflict': bool(evidence[i].get('author_identity_conflict'))}
                      if policy.get('reject_author_identity_conflicts', False) else {}),
                   'vector_sha256': hashlib.sha256(np.asarray(evidence[i]['vector'], dtype='<f8').tobytes()).hexdigest()
                                    if evidence[i]['vector'] is not None else None} for i in sorted(evidence)]),
              'code_version': runs.code_version(), 'counts': dict(Counter(r['assessment']['status'] for r in rows)),
              'candidates': rows, 'limitations': [policy['interpretation'],
                  'Никакие score/status семантических тем не перенесены; исходный снимок и экспертные мнения не изменены.',
                  'Первичность результатов неизвестна без проверенной содержательной разметки; отсутствие survey в названии не доказательство.',
                  'Новизна — сходство первого календарного окна с более ранними семенами кандидатов своего канала, не доказательство нового механизма.',
                  'Подбор составов современным генератором и современные метаданные: ретроспективная реконструкция, не прогноз на историческую дату.',
                  'Процентили зависимых и пересекающихся кандидатов экспериментальны, не вероятность истинности или промышленная калибровка.',
                  'Связность, авторские идентификаторы и качество метаданных не подтверждают воспроизведение результата или рыночный спрос.']}
    if external_evidence_provenance is not None:
        result['external_evidence_provenance'] = external_evidence_provenance
        result['limitations'].append(
            'OpenAlex-обогащение сопоставлено только по точному arXiv ID и '
            'влияет на библиографические proxy авторов/организаций; оно не '
            'переносит тексты, темы, цитирования, первичность или статус другой миссии.'
        )
    result = json.loads(json.dumps(result, ensure_ascii=False))
    result['assessment_content_sha256'] = hybrid.digest(result)
    return result


def apply_external_evidence(evidence: dict, matches: dict[int, int],
                            source_rows: dict[int, dict]) -> dict:
    """Add exact-ID identity proxies without changing candidate membership."""
    counters = Counter(target_works=len(evidence), exact_arxiv_matches=len(matches))
    for target_id, source_id in matches.items():
        if target_id not in evidence or source_id not in source_rows:
            raise ValueError('Внешнее сопоставление ссылается на неизвестную работу.')
        row = source_rows[source_id]
        if not row.get('has_openalex_version'):
            counters['without_openalex_version'] += 1
            continue
        counters['matched_with_openalex_version'] += 1
        if row.get('quality_decision') != 'include':
            counters['source_quality_not_include'] += 1
            continue
        target = evidence[target_id]
        target['authors'] = sorted(set(row.get('authors') or []))
        target['organisations'] = sorted(set(row.get('organisations') or []))
        target['author_identity_conflict'] = bool(
            target.get('author_identity_conflict')
            or row.get('author_identity_conflict')
        )
        counters['adopted'] += 1
        counters['with_author_ids'] += bool(target['authors'])
        counters['with_organisations'] += bool(target['organisations'])
        counters['with_identity_conflict'] += bool(target['author_identity_conflict'])
    return dict(counters)


def external_identity_evidence(cur, *, target_normalize_run_id: int,
                               target_work_ids: list[int], target_as_of: date,
                               source_normalize_run_id: int,
                               source_quality_generation_id: int,
                               evidence: dict) -> dict:
    """Load authors and affiliations from a sealed OpenAlex-enriched run.

    Matching is exact arXiv identifier only.  Text, dates, vectors, citations,
    candidate membership and statuses always remain those of the target run.
    """
    if source_normalize_run_id == target_normalize_run_id:
        raise ValueError('Внешний evidence-run должен отличаться от целевого корпуса.')
    cur.execute(
        'SELECT mission_id,query_version_id,as_of_date,notes FROM analysis_run '
        "WHERE run_id=%s AND kind='normalize' AND status='done'",
        (source_normalize_run_id,),
    )
    source = cur.fetchone()
    if not source or source[2] != target_as_of:
        raise ValueError('OpenAlex evidence-run отсутствует или имеет другую дату среза.')
    source_mission, source_query, source_as_of, source_notes = source
    cur.execute(
        'SELECT policy_version,status FROM quality_generation '
        'WHERE generation_id=%s AND normalize_run_id=%s',
        (source_quality_generation_id, source_normalize_run_id),
    )
    quality = cur.fetchone()
    if not quality or quality[1] != 'done':
        raise ValueError('Нужно явное завершённое поколение качества evidence-run.')
    batch_id = (source_notes or {}).get('collection_batch_id')
    cur.execute(
        'SELECT status,seal_status FROM collection_batch WHERE batch_id=%s '
        'AND mission_id=%s AND query_version_id=%s',
        (batch_id, source_mission, source_query),
    )
    batch = cur.fetchone()
    if not batch or batch != ('complete', 'sealed'):
        raise ValueError('OpenAlex evidence-run должен происходить из complete/sealed пакета.')

    cur.execute(
        "SELECT lower(i.value),i.work_id FROM identifier i JOIN work w USING(work_id) "
        "WHERE w.run_id=%s AND i.kind='arxiv' AND w.work_id=ANY(%s)",
        (target_normalize_run_id, target_work_ids),
    )
    target_by_arxiv = {}
    for identifier, work_id in cur.fetchall():
        if identifier in target_by_arxiv and target_by_arxiv[identifier] != work_id:
            raise ValueError('Один arXiv ID неоднозначен внутри целевого run.')
        target_by_arxiv[identifier] = work_id
    cur.execute(
        "SELECT lower(i.value),i.work_id FROM identifier i JOIN work w USING(work_id) "
        "WHERE w.run_id=%s AND i.kind='arxiv' AND lower(i.value)=ANY(%s)",
        (source_normalize_run_id, list(target_by_arxiv)),
    )
    source_by_arxiv = {}
    for identifier, work_id in cur.fetchall():
        if identifier in source_by_arxiv and source_by_arxiv[identifier] != work_id:
            raise ValueError('Один arXiv ID неоднозначен внутри evidence-run.')
        source_by_arxiv[identifier] = work_id
    matches = {target_by_arxiv[identifier]: source_id
               for identifier, source_id in source_by_arxiv.items()}
    source_ids = sorted(set(matches.values()))
    source_rows = {work_id: {'authors': [], 'organisations': []}
                   for work_id in source_ids}
    if source_ids:
        cur.execute(
            "SELECT w.work_id,q.decision,"
            "COALESCE((q.flags->>'author_identity_conflict')::boolean,false),"
            "EXISTS(SELECT 1 FROM work_version v WHERE v.work_id=w.work_id "
            "AND v.source='openalex') "
            "FROM work w JOIN quality_snapshot q ON q.work_id=w.work_id "
            "AND q.generation_id=%s WHERE w.run_id=%s AND w.work_id=ANY(%s)",
            (source_quality_generation_id, source_normalize_run_id, source_ids),
        )
        for work_id, decision, conflict, has_openalex in cur.fetchall():
            source_rows[work_id].update(
                quality_decision=decision,
                author_identity_conflict=conflict,
                has_openalex_version=has_openalex,
            )
        cur.execute(
            'SELECT wa.work_id,a.external_id,o.ror,o.display_name '
            'FROM work_author wa JOIN author a USING(author_id) '
            'LEFT JOIN organisation o USING(organisation_id) '
            'WHERE wa.work_id=ANY(%s)',
            (source_ids,),
        )
        for work_id, author, ror, organisation in cur.fetchall():
            if author:
                source_rows[work_id]['authors'].append(author)
            if ror or organisation:
                source_rows[work_id]['organisations'].append(
                    'ror:' + ror if ror else 'name:' + organisation.casefold()
                )
    counts = apply_external_evidence(evidence, matches, source_rows)
    return {
        'role': 'bibliographic_identity_enrichment_not_independent_discovery',
        'match_rule': 'exact_arxiv_identifier_only',
        'source_normalize_run_id': source_normalize_run_id,
        'source_quality_generation_id': source_quality_generation_id,
        'source_mission_id': source_mission,
        'source_query_version_id': source_query,
        'source_as_of_date': source_as_of.isoformat(),
        'source_collection_batch_id': batch_id,
        'source_collection_status': batch[0],
        'source_collection_seal_status': batch[1],
        'source_quality_policy_version': quality[0],
        'counts': counts,
        'affects_only': ['authors', 'organisations', 'author_identity_conflict'],
        'texts_imported': False,
        'vectors_imported': False,
        'citations_used': False,
        'candidate_membership_changed': False,
        'current_metadata_not_historical_identity_truth': True,
    }


def analyze(snapshot_id: str, config=None, policy=None, measurement_policy=None,
            external_normalize_run_id: int | None = None,
            external_quality_generation_id: int | None = None) -> dict:
    snapshot = hybrid.read(snapshot_id)
    p = snapshot['provenance']
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        docs, as_of, start, end, comparable, provenance = terminology.read_corpus(cur, p['mission_id'], p['quality_generation_id'])
        for key in ('normalize_run_id', 'quality_generation_id', 'query_version_id', 'collection_batch_id'):
            if provenance[key] != p[key]:
                raise ValueError('Происхождение входа оценки не совпало со снимком.')
        if (as_of.isoformat() != snapshot['as_of_date'] or end.isoformat() != snapshot['period_end_exclusive']
                or (start.isoformat() if start else None) != snapshot['period_from']):
            raise ValueError('Период входа изменился относительно снимка.')
        # Same order as the generator, not a query-plan-dependent SQL order.
        docs = sorted(docs, key=lambda d: (d.published_at, d.work_id))
        ids = [d.work_id for d in docs]
        cur.execute('SELECT w.work_id, q.source_quality, e.embedding::text, '
                    "COALESCE((q.flags->>'author_identity_conflict')::boolean, false) FROM work w "
                    'JOIN quality_snapshot q ON q.work_id = w.work_id AND q.generation_id = %s '
                    'LEFT JOIN work_embedding e ON e.work_id = w.work_id AND e.model = %s '
                    'WHERE w.work_id = ANY(%s)', (p['quality_generation_id'], p['embedding_model'], ids))
        evidence = {i: {'source_quality': float(q), 'vector': parse_vector(v).tolist() if v else None,
                        'authors': [], 'organisations': [], 'author_identity_conflict': conflict}
                    for i, q, v, conflict in cur.fetchall()}
        cur.execute('SELECT wa.work_id, a.external_id, wa.organisation_id FROM work_author wa '
                    'JOIN author a USING (author_id) WHERE wa.work_id = ANY(%s)', (ids,))
        for i, author, org in cur.fetchall():
            if author: evidence[i]['authors'].append(author)
            if org is not None: evidence[i]['organisations'].append(org)
        for row in evidence.values():
            for key in ('authors', 'organisations'):
                row[key] = sorted(set(row[key]))
        external_provenance = None
        if ((external_normalize_run_id is None)
                != (external_quality_generation_id is None)):
            raise ValueError('Для внешнего evidence нужны одновременно normalize и quality ID.')
        if external_normalize_run_id is not None:
            external_provenance = external_identity_evidence(
                cur,
                target_normalize_run_id=p['normalize_run_id'],
                target_work_ids=ids,
                target_as_of=as_of,
                source_normalize_run_id=external_normalize_run_id,
                source_quality_generation_id=external_quality_generation_id,
                evidence=evidence,
            )
        if any(point['coverage_comparable'] != comparable for c in snapshot['candidates'] for point in c['publication_series']['points']):
            raise ValueError('Паспорт покрытия изменился относительно снимка.')
    return assess(snapshot, docs, evidence, config, policy, measurement_policy,
                  external_provenance)


def replay(previous: dict) -> dict:
    """Verify saved report; replay its policies and exact scientific inputs."""
    previous = json.loads(json.dumps(previous, ensure_ascii=False))
    expected_sha = previous.pop('assessment_content_sha256')
    if hybrid.digest(previous) != expected_sha:
        raise ValueError('Отпечаток сохранённой оценки не совпал.')
    config = methodology.from_mapping(previous['effective_methodology'])
    if (config.config_hash != previous['methodology_hash']
            or hybrid.digest(previous['effective_policy']) != previous['policy_hash']
            or hybrid.digest(previous['effective_measurement_policy']) != previous['measurement_policy_hash']):
        raise ValueError('Хеш встроенных паспортов не совпал.')
    external = previous.get('external_evidence_provenance') or {}
    result = analyze(
        previous['snapshot_id'], config, previous['effective_policy'],
        previous['effective_measurement_policy'],
        external.get('source_normalize_run_id'),
        external.get('source_quality_generation_id'),
    )
    def scientific_content(value):
        return {k: v for k, v in value.items()
                if k not in {'code_version', 'assessment_content_sha256'} and not k.startswith('replay_')}
    if scientific_content(result) != scientific_content(previous):
        raise ValueError('Повтор не совпал: входы или научный результат изменились. Старый файл сохранён.')
    result.pop('assessment_content_sha256')
    result.update(replay_from_assessment_sha256=expected_sha,
                  replay_previous_code_version=previous['code_version'],
                  replay_code_version_equal=result['code_version'] == previous['code_version'],
                  replay_scientific_content_equal=True)
    result['assessment_content_sha256'] = hybrid.digest(result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description='Оценка точных гибридных составов без переноса чужих статусов')
    parser.add_argument('snapshot_id')
    parser.add_argument('--export', type=Path, required=True)
    parser.add_argument('--replay-from', type=Path, help='Повтор по встроенным паспортам прежней оценки')
    parser.add_argument('--external-normalize-run', type=int,
                        help='Завершённый OpenAlex-enriched normalize run с тем же as-of')
    parser.add_argument('--external-quality-generation', type=int,
                        help='Явное quality-поколение external normalize run')
    args = parser.parse_args()
    if args.export.exists():
        parser.error('Экспорт уже существует: укажите новый файл, старый результат не перезаписываем.')
    if args.replay_from:
        previous = json.loads(args.replay_from.read_text())
        if previous['snapshot_id'] != args.snapshot_id:
            parser.error('Номер снимка не совпал с файлом для повтора.')
        result = replay(previous)
    else:
        result = analyze(
            args.snapshot_id,
            external_normalize_run_id=args.external_normalize_run,
            external_quality_generation_id=args.external_quality_generation,
        )
    with args.export.open('x') as output:
        output.write(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({'snapshot_id': result['snapshot_id'], 'counts': result['counts'],
                      'assessment_content_sha256': result['assessment_content_sha256']}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
