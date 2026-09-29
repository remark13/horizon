"""Прогон шлюзов по нормализованному корпусу.

    python -m saia.probe gnn-fraud
    python -m saia.probe gnn-fraud --field "Computer Science"

ИСПРАВЛЕНО ПОСЛЕ РЕВИЗИИ. В первой версии порог объёма применялся к
медиане работ на окно. Это неверно: в обоих документах порог относится к
кандидату целиком. Аналитический отчёт — «Перед ранжированием кандидат
проходит hard gates: не менее 8 релевантных документов в основном канале».
Методика — «не менее 3 работ, 2 организаций и 2 периодов», где периоды
названы отдельно от работ. Медиана по окну осталась в выводе, но как
справка, а не как то, с чем сравнивается порог.

Не считаются здесь: новизна и связность (нужны эмбеддинги и кластеры),
рост в перцентилях (нужна выборка соседних тем). Помечены знаком вопроса.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

from saia import db, methodology

EPS = 1e-6


def load_field_baseline(mission_id: str) -> dict[int, int]:
    path = Path("data/raw") / mission_id / "openalex" / "field_baseline_by_year.json"
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        int(g["key"]): g["count"]
        for g in payload.get("group_by", []) if str(g["key"]).isdigit()
    }


def share_of(count: int, denominator: int | None) -> float | None:
    """Доля темы в области, либо None, если знаменателя нет.

    Дефект P1-4: при отсутствующем знаменателе возвращался 0.0, и дальше
    нуль вёл себя как измеренное значение — momentum считался, шлюз выглядел
    вычисленным. Паспорт при этом требует missing_is_not_zero. Отсутствие
    данных и нулевая доля — разные утверждения о мире.
    """
    if not denominator:
        return None
    return count / denominator


def independent_teams(authors_by_work: dict[int, set[str]]) -> int:
    ids = list(authors_by_work)
    index = {w: i for i, w in enumerate(ids)}
    parent = list(range(len(ids)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    by_author: dict[str, list[int]] = defaultdict(list)
    for work_id, authors in authors_by_work.items():
        for author in authors:
            by_author[author].append(index[work_id])
    for group in by_author.values():
        root = find(group[0])
        for other in group[1:]:
            ro = find(other)
            if ro != root:
                parent[ro] = root
    return len({find(i) for i in range(len(ids))})


def main() -> int:
    parser = argparse.ArgumentParser(description="Прогон шлюзов Horizon")
    parser.add_argument("mission_id")
    parser.add_argument("--field", default=None,
                        help="оставить только работы этой предметной области")
    args = parser.parse_args()

    mission_id = args.mission_id
    baseline = load_field_baseline(mission_id)

    # Паспорт через валидирующий загрузчик, а не yaml.safe_load: обход
    # проверок был дефектом P0-5.
    config = methodology.load_default()

    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT as_of_date, title FROM mission WHERE mission_id = %s", (mission_id,))
        row = cur.fetchone()
        if not row:
            print(f"миссии {mission_id} нет в базе")
            return 1
        as_of, title = row

        cur.execute(
            """
            SELECT work_id, publication_year, primary_field, effective_date
            FROM work_current
            WHERE mission_id = %s AND effective_date < %s
            ORDER BY effective_date
            """,
            (mission_id, as_of),
        )
        all_visible = cur.fetchall()

        if args.field:
            works = [w for w in all_visible if w[2] == args.field]
        else:
            works = all_visible
        ids = [w[0] for w in works]

        if not ids:
            print("на дату среза не осталось ни одной работы")
            return 0

        cur.execute(
            """
            SELECT wa.work_id, a.name_key FROM work_author wa
            JOIN author a USING (author_id) WHERE wa.work_id = ANY(%s)
            """,
            (ids,),
        )
        authors_by_work: dict[int, set[str]] = {w: set() for w in ids}
        for work_id, key in cur.fetchall():
            authors_by_work[work_id].add(key)

        cur.execute(
            """
            SELECT o.display_name, count(DISTINCT wa.work_id)
            FROM work_author wa JOIN organisation o USING (organisation_id)
            WHERE wa.work_id = ANY(%s) GROUP BY o.display_name
            """,
            (ids,),
        )
        org_counts = dict(cur.fetchall())

        cur.execute(
            """
            SELECT count(*) FROM work w WHERE w.work_id = ANY(%s) AND NOT EXISTS (
                SELECT 1 FROM work_author wa
                WHERE wa.work_id = w.work_id AND wa.organisation_id IS NOT NULL)
            """,
            (ids,),
        )
        without_affiliation = cur.fetchone()[0]

        cur.execute(
            """
            SELECT primary_field, count(*) FROM work_current
            WHERE work_id = ANY(%s) GROUP BY primary_field ORDER BY count(*) DESC
            """,
            ([w[0] for w in all_visible],),
        )
        field_counts = cur.fetchall()

    print("=" * 74)
    print(f"ШЛЮЗЫ ПО КАНОНИЧЕСКОМУ КОРПУСУ · срез {as_of}")
    print(title)
    print("=" * 74)

    print(f"\nработ видно на срезе:          {len(all_visible)}")
    if args.field:
        print(f"после фильтра «{args.field}»: {len(works)}"
              f"   (отсеяно {len(all_visible) - len(works)})")
    print(f"без аффилиаций:                {without_affiliation} из {len(ids)}"
          f" ({100 * without_affiliation / len(ids):.0f}%)")

    print("\nпредметные области в корпусе на срезе:")
    for field, count in field_counts:
        mark = "  ← оставлено" if args.field and field == args.field else ""
        print(f"  {count:>4}  {field or '(нет разметки)'}{mark}")

    teams = independent_teams(authors_by_work)
    total_links = sum(org_counts.values())
    eff_orgs = (1.0 / sum((n / total_links) ** 2 for n in org_counts.values())) if total_links else 0.0
    top_share = (max(org_counts.values()) / total_links) if total_links else 0.0

    print(f"\nнезависимых команд:            {teams}")
    print(f"эффективное число организаций: {eff_orgs:.1f}")
    print(f"доля крупнейшей организации:   {top_share:.0%}")

    # --- окна -----------------------------------------------------------
    by_year: dict[int, int] = defaultdict(int)
    for _, year, _, _ in works:
        if year:
            by_year[year] += 1
    as_of_year = int(str(as_of)[:4])
    years = sorted(y for y in by_year if y < as_of_year)

    # ИСПРАВЛЕНО ПОСЛЕ РЕВИЗИИ (дефект P1-4). При отсутствующем знаменателе
    # доля подставлялась нулём, и дальше нуль вёл себя как измеренное
    # значение: momentum считался, шлюз выглядел вычисленным. Паспорт при
    # этом требует missing_is_not_zero. Отсутствие знаменателя теперь даёт
    # None и распространяется дальше как «не вычислено».
    rows = []
    for year in years:
        denominator = baseline.get(year)
        rows.append({
            "year": year, "n": by_year[year],
            "share": share_of(by_year[year], denominator),
        })
    for i, row in enumerate(rows):
        previous = rows[i - 1]["share"] if i else None
        if i == 0 or row["share"] is None or previous is None:
            row["momentum"] = None
        else:
            row["momentum"] = math.log((row["share"] + EPS) / (previous + EPS))

    missing_denominator = [r["year"] for r in rows if r["share"] is None]

    print(f"\n{'-' * 74}")
    print(f"\n  год    работ   доля в области   momentum")
    for row in rows:
        momentum = f"{row['momentum']:+.2f}" if row["momentum"] is not None else "—"
        share = f"{row['share'] * 100:>13.4f}%" if row["share"] is not None else "не вычислена".rjust(14)
        print(f"  {row['year']}  {row['n']:>6}  {share}  {momentum:>9}")
    if missing_denominator:
        print(f"\n  нет знаменателя области для лет: "
              f"{', '.join(str(y) for y in missing_denominator)}"
              f"  — доля и momentum за эти годы НЕ вычислены (не равны нулю)")

    # Два определения устойчивости, потому что документы дают два разных.
    # Baseline: «присутствие и устойчивость минимум в двух последовательных
    # окнах». Методика 4.1: «число последовательных периодов роста».
    # Первое мягче и считает окна присутствия, второе строже и требует
    # неубывающей доли. Показываем оба — выбор между ними это решение,
    # а не деталь реализации.
    presence = len([r for r in rows if r["n"] > 0])
    growth_streak = 0
    for i in range(len(rows) - 1, 0, -1):
        if rows[i]["share"] is None or rows[i - 1]["share"] is None:
            break
        if rows[i]["share"] >= rows[i - 1]["share"]:
            growth_streak += 1
        else:
            break

    total_works = len(ids)
    median_n = sorted(r["n"] for r in rows)[len(rows) // 2] if rows else 0
    span_years = (as_of_year - years[0]) if years else 0

    print(f"\n  ОБЪЁМ КАНДИДАТА (с этим сравнивается порог): {total_works} работ")
    print(f"  медиана работ на окно (справочно, НЕ порог): {median_n}")
    print(f"  окон присутствия: {presence}")
    print(f"  окон подряд без падения доли: {growth_streak}")
    print(f"  возраст линии: {span_years} лет (с {years[0] if years else '—'})")

    # --- шлюзы ----------------------------------------------------------
    for name, cfg in config.raw["gates"]["configurations"].items():
        print(f"\n  ── конфигурация '{name}' ──")
        failed = []
        checks = []

        min_works = cfg.get("g0_min_canonical_works", 0)
        checks.append(("G0 объём кандидата", total_works >= min_works,
                       f"{total_works} работ, нужно ≥{min_works}"))

        if cfg.get("g0_min_independent_orgs"):
            checks.append(("G0 организации", eff_orgs >= cfg["g0_min_independent_orgs"],
                           f"эффективное число {eff_orgs:.1f}, нужно ≥{cfg['g0_min_independent_orgs']}"))

        checks.append(("G0 концентрация", top_share <= cfg.get("g0_max_single_org_share", 1.0),
                       f"крупнейшая организация {top_share:.0%}, порог {cfg.get('g0_max_single_org_share', 1.0):.0%}"))
        # Было: ("G1 зрелость", True, "доля в области крайне мала") — шлюз
        # объявлялся пройденным без расчёта. Зрелость задана перцентилем в
        # распределении тем, которого на этом этапе нет.
        maturity_computable = any(r["share"] is not None for r in rows)
        checks.append(("G1 зрелость", None,
                       f"порог перцентиль <={cfg.get('g1_maturity_percentile_max')} — "
                       + ("нужна выборка соседних тем"
                          if maturity_computable else "нет знаменателя области")))
        checks.append(("G2 новизна", None, "нужны эмбеддинги и кластеры"))

        min_windows = cfg.get("g3_min_windows", 2)
        checks.append(("G3 устойчивость", presence >= min_windows,
                       f"окон присутствия {presence}, нужно ≥{min_windows}"
                       f"  (по строгой трактовке — рост подряд: {growth_streak})"))
        checks.append(("G3 независимость", teams >= cfg.get("g3_min_independent_teams", 2),
                       f"команд {teams}, нужно ≥{cfg.get('g3_min_independent_teams', 2)}"))
        checks.append(("G4 рост", None,
                       f"порог перцентиль ≥{cfg.get('g4_momentum_percentile_min')} — нужна выборка соседних тем"))

        if cfg.get("g5_age_years_max"):
            limit = cfg["g5_age_years_max"]
            mode = cfg.get("g5_mode", "block")
            passed = span_years <= limit
            note = f"линии {span_years} лет, порог {limit}"
            if mode == "warn":
                checks.append(("G5 возраст", None, note + " (режим предупреждения)"))
            else:
                checks.append(("G5 возраст", passed, note))

        for gate, passed, note in checks:
            mark = "  ?" if passed is None else ("  +" if passed else "  −")
            print(f"  {mark} {gate:<20} {note}")
            if passed is False:
                failed.append(gate)

        print(f"      → {'ПРОХОДИТ проверяемые шлюзы' if not failed else 'ПАДАЕТ на: ' + ', '.join(failed)}")

    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
