"""Read committed vector readiness for an exact frozen input; never load a model."""
from saia import db, runs
from saia.cluster import resolve_quality_input


def summarize(total: int, completed: int, invalid: int, dimensions: list[int]) -> dict:
    if not 0 <= completed <= total or not 0 <= invalid <= completed:
        raise ValueError('Несогласованные числа работ и векторов.')
    if invalid or len(dimensions) > 1 or any(d < 1 for d in dimensions):
        state = 'invalid'
    elif total == 0:
        state = 'empty'
    elif completed == total:
        state = 'complete' if len(dimensions) == 1 else 'invalid'
    else:
        state = 'partial' if completed else 'pending'
    return {'state': state, 'eligible_works': total, 'committed_vectors': completed,
            'remaining_works': total - completed, 'invalid_dimensions': invalid,
            'dimensions': dimensions, 'ready_for_strict_clustering': state == 'complete'}


def read(mission_id: str, quality_generation_id: int, model: str) -> dict:
    if not isinstance(quality_generation_id, int) or isinstance(quality_generation_id, bool) or quality_generation_id < 1:
        raise ValueError('Нужно точное положительное поколение качества; latest не допускается.')
    if not isinstance(model, str) or not model.strip() or len(model) > 512:
        raise ValueError('Нужно точное имя версии модели.')
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        root, generation = resolve_quality_input(cur, mission_id, quality_generation_id)
        start, end, origin = runs.analysis_period(cur, root)
        cur.execute('SELECT count(*),count(e.work_id), '
                    'count(*) FILTER (WHERE e.work_id IS NOT NULL AND (e.dim<>vector_dims(e.embedding) OR e.dim<1)), '
                    'array_agg(DISTINCT e.dim) FILTER (WHERE e.work_id IS NOT NULL) '
                    'FROM work w JOIN quality_snapshot q USING(work_id) '
                    'LEFT JOIN work_embedding e ON e.work_id=w.work_id AND e.model=%s '
                    "WHERE w.run_id=%s AND q.generation_id=%s AND q.decision='include' "
                    'AND (%s::date IS NULL OR w.effective_date >= %s) AND w.effective_date < %s',
                    (model, root, generation, start, start, end))
        total, completed, invalid, dimensions = cur.fetchone()
    return {'mission_id': mission_id, 'normalize_run_id': root,
            'quality_generation_id': generation, 'embedding_model': model,
            'period_from': start.isoformat() if start else None, 'period_end_exclusive': end.isoformat(),
            'period_origin': origin, **summarize(total, completed, invalid, sorted(dimensions or [])),
            'limitations': 'Готовность векторов не подтверждает слабый сигнал, полноту мировой науки, качество кластеров или рынок. Проверяются сохранённые строки одной версии корпуса и модели, а не журнал процесса.'}
