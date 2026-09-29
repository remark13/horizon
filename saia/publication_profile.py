"""Descriptive JRC-inspired indicators, never extra score or novelty evidence."""
from __future__ import annotations

from datetime import date
from html import escape

VERSION = "publication-profile-v1"


def _day(value):
    try:
        return date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def build(card: dict, *, today: date) -> dict:
    metrics = card.get("metrics") or {}
    observed = metrics.get("observed") or {}
    series = metrics.get("publication_series") or {}
    cutoff = _day(series.get("as_of_date")) or _day(observed.get("assessed_at")) or today
    cutoff = min(cutoff, today)
    last_year = cutoff.year - 1
    recent_start, recent_end = date(last_year - 2, 1, 1), date(last_year + 1, 1, 1)
    points = []
    invalid = False
    for row in series.get("points") or []:
        start, end, count = _day(row.get("start")), _day(row.get("end")), _count(row.get("topic_works"))
        if not start or not end or start >= end or end > cutoff or count is None:
            invalid = True
            continue
        points.append({"start": start, "end": end, "count": count, "complete": row.get("complete") is True})
    points.sort(key=lambda row: row["start"])
    if any(a["end"] != b["start"] for a, b in zip(points, points[1:])):
        invalid = True
    full = [row for row in points if row["complete"]]
    eligible = [row for row in full if row["end"] <= recent_end]
    recent = [row for row in eligible if row["start"] >= recent_start]
    recent_covered = (not invalid and bool(recent) and recent[0]["start"] == recent_start
                      and recent[-1]["end"] == recent_end
                      and all(a["end"] == b["start"] for a, b in zip(recent, recent[1:]))
                      and all(row["end"] <= recent_start or row["start"] >= recent_start for row in eligible))
    denominator = sum(row["count"] for row in eligible) if recent_covered else None
    numerator = sum(row["count"] for row in recent) if recent_covered else None
    share = round(100 * numerator / denominator, 2) if denominator else None
    active = [row for row in points if row["count"] > 0]
    # The bounds are publication intervals, not guessed dates of individual works.
    trail_start = active[0]["start"].isoformat() if active and not invalid else None
    trail_end = active[-1]["end"].isoformat() if active and not invalid else None
    note = ("Доля рассчитана только внутри найденной выборки. Это не темп роста, не вероятность и не доказательство новизны."
            if share is not None else "Для доли нужны три полностью покрытых календарных года и ненулевой знаменатель. Недостающие периоды не заменяются нулями.")
    if share is not None and denominator < 10:
        note += " Малая выборка: меньше 10 работ в знаменателе."
    if series.get("coverage_comparable") is not True:
        note += " Сопоставимость покрытия не подтверждена."
    return {"version": VERSION, "scope": "retrieved_topic_sample_only", "used_for_score": False,
            "found_works": _count(observed.get("doc_count")),
            "recent_years": [last_year - 2, last_year], "recent_start": recent_start.isoformat(),
            "recent_end_exclusive": recent_end.isoformat(), "recent_works": numerator,
            "eligible_works": denominator, "recent_share_percent": share,
            "small_sample": denominator < 10 if denominator else None,
            "recent_window_fully_covered": recent_covered, "coverage_comparable": series.get("coverage_comparable"),
            "active_period_from": trail_start, "active_period_end_exclusive": trail_end,
            "trail_is_technology_birth": False, "note": note,
            "formula": "100 × работы темы за последние три завершённых календарных года / все найденные работы темы до конца того же года"}


def render(profile: dict) -> str:
    def esc(value):
        return escape(str(value), quote=True)
    share = profile["recent_share_percent"]
    percentage = f"{share:.1f}".replace(".", ",") + "%" if share is not None else "не рассчитана"
    start, end = profile["active_period_from"], profile["active_period_end_exclusive"]
    # An exclusive January boundary belongs to the preceding calendar year.
    last = _day(end)
    trail = (start[:4] + "–" + str(last.year - int(last.month == 1 and last.day == 1))) if start and last else "не установлен"
    years = "–".join(map(str, profile["recent_years"]))
    tiles = [("Найдено научных работ", profile["found_works"] if profile["found_works"] is not None else "не установлено"),
             (f"Доля работ {years}", percentage), ("Активные периоды выборки", trail)]
    result = '<section class="publication-profile"><h3>Профиль публикационной активности</h3><dl style="display:grid;grid-template-columns:repeat(auto-fit,minmax(135px,1fr));gap:12px">'
    for label, value in tiles:
        result += f'<div style="background:#f5f7fb;border-radius:9px;padding:12px"><dt style="font-size:11px;color:#68758d">{esc(label)}</dt><dd style="margin:5px 0 0;font-size:19px;font-weight:700">{esc(value)}</dd></div>'
    caption = "В доступной выборке. Периоды активности не означают дату возникновения технологии."
    if share is not None:
        caption = f'{profile["recent_works"]} из {profile["eligible_works"]} работ до конца {profile["recent_years"][1]}. Текущий год в доле не учитывается. '
        caption += "Малая выборка. " if profile["small_sample"] else ""
        caption += "Это не подтверждённый рост темы."
    result += '</dl><p class="form-note">' + esc(caption) + '</p><details class="compact-details"><summary>Как читать эти показатели</summary><p class="form-note">' + esc(profile["note"]) + '</p>'
    if share is not None:
        result += f'<p class="form-note">{esc(profile["recent_works"])} / {esc(profile["eligible_works"])} × 100 = {esc(percentage)}. Текущий год исключён из числителя и знаменателя.</p>'
    result += '<p class="form-note">' + esc(profile["formula"]) + '</p></details></section>'
    return result
