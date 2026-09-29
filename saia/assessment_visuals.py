"""Escaped, accessible native SVG charts. Missing axes are never drawn as zero."""
from __future__ import annotations

from html import escape
from urllib.parse import urlsplit

COLORS = {"science": "#3156ad", "market": "#14836b", "patents": "#ae7424", "investment": "#8359b5"}


def esc(value) -> str:
    return escape(str(value if value is not None else "Недостаточно данных"), quote=True)


def fmt(value) -> str:
    return f"{value:.1f}".replace(".", ",") if isinstance(value, (int, float)) else "—"


def radar(profile: dict) -> str:
    axes = profile["axes"]
    order = ["science", "market", "patents", "investment"]
    coordinates = [(180, 60), (285, 165), (180, 270), (75, 165)]
    pieces = ['<svg viewBox="0 0 360 330" role="img" aria-label="Четыре источника данных: научные статьи, новостные публикации, патенты, инвестиционные сделки. Неизвестные оси не равны нулю.">']
    for level in (25, 50, 75, 100):
        points = " ".join(f"{180+(x-180)*level/100:g},{165+(y-165)*level/100:g}" for x, y in coordinates)
        pieces.append(f'<polygon points="{points}" fill="none" stroke="#dfe5ef"/><text x="185" y="{165-105*level/100+12:g}" fill="#8995a8" font-size="9">{level}</text>')
    known = []
    for key, (x, y) in zip(order, coordinates):
        value = axes[key]["score"]
        pieces.append(f'<line x1="180" y1="165" x2="{x}" y2="{y}" stroke="#dfe5ef" stroke-dasharray="3 4"/>')
        if value is not None:
            px, py = 180 + (x - 180) * value / 100, 165 + (y - 165) * value / 100
            known.append((px, py))
            pieces.append(f'<line x1="180" y1="165" x2="{px:g}" y2="{py:g}" stroke="{COLORS[key]}" stroke-width="3"/><circle cx="{px:g}" cy="{py:g}" r="5" fill="{COLORS[key]}"><title>{esc(axes[key]["label"])}: {fmt(value)} балла</title></circle>')
        tx, ty, align = {"science": (180, 28, "middle"), "market": (292, 154, "start"), "patents": (180, 303, "middle"), "investment": (68, 154, "end")}[key]
        pieces.append(f'<text x="{tx}" y="{ty}" text-anchor="{align}" fill="{COLORS[key]}" font-size="12" font-weight="700">{esc(axes[key]["label"])}</text><text x="{tx}" y="{ty+16}" text-anchor="{align}" fill="#68758d" font-size="10">{fmt(value) if value is not None else "нет данных"}</text>')
    # A partial polygon would falsely represent unavailable axes as zero.
    if len(known) == 4:
        pieces.insert(1, '<polygon points="' + " ".join(f"{x:g},{y:g}" for x, y in known) + '" fill="#3156ad18" stroke="#3156ad" stroke-width="2"/>')
    pieces.append('</svg>')
    return "".join(pieces)


def line_chart(points: list[dict], key: str, *, share: bool = False) -> str:
    usable = [p for p in points if p.get("complete") is not False and isinstance(p.get("share" if share else "count"), (int, float))]
    if not usable:
        return '<div class="empty">Нет ряда для построения. Линия не дорисовывается.</div>'
    usable = usable[-12:]
    values = [(p["share"] * 100) if share else p["count"] for p in usable]
    maximum = max(1, *values)
    width, height = 450, 150
    coords = [(42 + i * 380 / max(1, len(usable)-1), 113 - v * 87 / maximum) for i, v in enumerate(values)]
    pieces = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{esc(key)}: динамика {"доли публикаций" if share else "найденных материалов"}">']
    for fraction in (0, .5, 1):
        y = 113 - 87 * fraction
        pieces.append(f'<line x1="42" y1="{y:g}" x2="425" y2="{y:g}" stroke="#e5eaf3"/><text x="35" y="{y+4:g}" text-anchor="end" font-size="10" fill="#68758d">{fmt(maximum*fraction)}{"%" if share else ""}</text>')
    if len(coords) > 1:
        # Scientific intervals are explicit. External dates are a sparse sample:
        # connect only neighbouring months, never bridge unobserved months.
        all_months = [str(p.get("period") or "")[:7] for p in points]
        month_indices = [int(p[:4])*12+int(p[5:7]) for p in all_months if len(p) == 7 and p[:4].isdigit() and p[5:7].isdigit()]
        differences = [b-a for a, b in zip(month_indices, month_indices[1:]) if b > a]
        scientific_step = min(differences) if differences else 3
        for index in range(1, len(coords)):
            prev, current = usable[index-1], usable[index]
            a, b = str(prev["period"])[:7], str(current["period"])[:7]
            month_diff = (int(b[:4])-int(a[:4]))*12 + int(b[5:7])-int(a[5:7])
            if month_diff != (scientific_step if key == "science" else 1):
                continue
            x1, y1 = coords[index-1]; x2, y2 = coords[index]
            dash = ' stroke-dasharray="5 3"' if key != "science" else ''
            pieces.append(f'<line x1="{x1:g}" y1="{y1:g}" x2="{x2:g}" y2="{y2:g}" stroke="{COLORS[key]}" stroke-width="2.5"{dash}/>')
    for (x, y), point, value in zip(coords, usable, values):
        pieces.append(f'<circle cx="{x:g}" cy="{y:g}" r="4" fill="{COLORS[key]}"><title>{esc(point["period"])}: {fmt(value)}{"%" if share else " материалов"}</title></circle>')
    for index in {0, len(usable)//2, len(usable)-1}:
        pieces.append(f'<text x="{coords[index][0]:g}" y="139" text-anchor="middle" font-size="10" fill="#68758d">{esc(str(usable[index]["period"])[:7])}</text>')
    pieces.append('</svg>')
    return "".join(pieces)


def render(profile: dict) -> str:
    total, base = profile["overall_score"], profile["scientific_baseline"]
    parts = [f'<div class="assessment-summary"><div><span class="assessment-caption">Общая оценка кандидата</span><strong>{fmt(total)} <small>/ 100 баллов</small></strong><p>Рабочий приоритет, не вероятность успеха.<br>Измерено {fmt(profile["axes"]["science"]["known_weight_percent"])}% веса научных признаков.</p></div><span class="badge amber">Автоматическая оценка</span></div>',
             '<div class="assessment-radar">' + radar(profile) + '</div><p class="form-note">Четыре источника: научные статьи, новости, патенты и инвестиционные сделки. Лепестки показывают силу доступных оснований (0–100). «Нет данных» не равно нулю. Новости — это публикации, а не объём рынка или продажи.</p>',
             '<h3>Из чего складывается оценка</h3><div class="contribution-list">']
    for key, axis in profile["axes"].items():
        value = profile["contributions"][key]
        parts.append(f'<div class="contribution-item"><span>{esc(axis["label"])}</span><span class="contribution-track"><i style="width:{value if value is not None else 0:g}%;background:{COLORS[key]}"></i></span><strong>{("+" if key != "science" and value is not None else "") + fmt(value)}</strong></div>')
    parts.append('</div><p class="form-note">' + esc(profile["formula"]) + '</p>')
    parts.append('<details class="compact-details"><summary>Показатели и формулы расчёта</summary>')
    for key, axis in profile["axes"].items():
        parts.append(f'<h4>{esc(axis["label"])} · {fmt(axis["score"])} / 100</h4><p class="form-note">{esc(axis["meaning"])}</p><dl class="assessment-metrics">')
        for label, value in axis["metrics"].items():
            parts.append(f'<dt>{esc(label)}</dt><dd>{esc(value)}</dd>')
        parts.append('</dl>')
        if key == "science":
            parts.append('<p class="form-note">Вес роста 40%, новизны 25%, устойчивости 15%, независимых групп 10%, объёма публикаций 10%. Объём = min(число работ / 20; 1), не признак роста. Балл — сумма известных взвешенных вкладов без пересчёта весов. Измерено ' + fmt(axis["known_weight_percent"]) + '%; неизвестно ' + fmt(axis["unmeasured_weight_percent"]) + '% веса. Один только объём работ даёт не более 10 баллов.</p>')
            for component in axis["components"]:
                parts.append(f'<p class="form-note">{esc(component["label"])}: {fmt(component["value"])}; вклад в научный балл {fmt(component["weighted_points"])}.</p>')
        else:
            parts.append('<p class="form-note">Автоматическое совпадение терминов имеет вес 0,35; явная привязка скаута 0,65. Без известного патентного семейства вес записи делится пополам.</p>')
            formula = '70 × min(взвешенные записи / 4; 1) + 30 × min(заявители / 3; 1)' if key == 'patents' else '60 × min(взвешенные записи / 4; 1) + 20 × min(издатели / 3; 1) + 20 × min(взвешенные записи текущего года / 4; 1)'
            parts.append('<p class="form-note">Балл блока: ' + esc(formula) + '. Вклад в итог = балл блока × ' + ('0,05' if key == 'investment' else '0,10') + '.</p>')
            for label, value in axis["components"].items():
                parts.append(f'<p class="form-note">{esc(label)}: {fmt(value)}.</p>')
    parts.append('</details><div class="assessment-validation"><strong>' + esc(profile["validation"]["label"]) + '</strong><p>' + esc(profile["validation"]["note"]) + '</p></div>')
    if profile.get("publication_profile"):
        from saia.publication_profile import render as render_publication_profile
        parts.append(render_publication_profile(profile["publication_profile"]))
    parts.append('<h3 style="margin-top:20px">Динамика по периодам</h3><div class="assessment-trends">')
    for key, axis in profile["axes"].items():
        history = profile["histories"][key]
        parts.append(f'<section class="assessment-trend"><h4><span style="color:{COLORS[key]}">●</span> {esc(axis["label"])} · {"публикации" if key == "science" else "учтённые записи"}</h4>' + line_chart(history["points"], key) + '<p class="form-note">' + esc(history["note"]) + '</p>')
        if key == "science" and any(p.get("share") is not None for p in history["points"]):
            parts.append('<h4>Доля научных публикаций в собранном корпусе</h4>' + line_chart(history["points"], key, share=True))
        if history["points"]:
            parts.append('<details class="compact-details"><summary>Исходные значения по периодам</summary><table class="assessment-values"><thead><tr><th>Период</th><th>Количество</th><th>Доля / статус</th></tr></thead><tbody>')
            for point in history["points"]:
                status = 'неполный период' if point.get("complete") is False else (fmt(point.get("share") * 100) + '%' if point.get("share") is not None else 'ограниченная выборка')
                parts.append(f'<tr><td>{esc(point["period"])}</td><td>{esc(point["count"])}</td><td>{esc(status)}</td></tr>')
            parts.append('</tbody></table></details>')
        parts.append('</section>')
    parts.append('</div><details class="compact-details"><summary>Проверка научных оснований</summary>')
    for check in profile["scientific_checks"]:
        verdict = "Пройдено" if check["passed"] is True else "Не пройдено" if check["passed"] is False else "Не проверено"
        parts.append(f'<p class="form-note">{esc(check["label"])} — {verdict}; значение {esc(check["observed"])}; порог {esc(check["threshold"])}.</p>')
    parts.append('</details><details class="compact-details"><summary>Какие внешние материалы повлияли на оценку</summary>')
    if not profile["records"]:
        parts.append('<p class="form-note">Дополнительные материалы ещё не собраны. Базовый научный балл сохранён.</p>')
    for record in profile["records"]:
        target = urlsplit(str(record.get("url") or ""))
        label = esc(record["title"])
        if target.scheme in {"http", "https"} and target.hostname and not target.username:
            label = f'<a href="{esc(record["url"])}" target="_blank" rel="noopener noreferrer">{label}</a>'
        verdict = "Учтено с понижающим весом " + fmt(record["quality_weight"]) if record["included"] else "Не учтено: " + esc(record["exclusion_reason"])
        parts.append(f'<p class="form-note">{label}<br>{esc(record["source"])} · {esc(record["record_date"])} · {verdict}<br>Совпавшие термины: {esc(", ".join(record["matched_terms"]) or "не установлены")}.</p>')
    parts.append('</details><p class="form-note">Правила ' + esc(profile["version"]) + '. Шкала пока не откалибрована на размеченных примерах. Экспертиза необязательна.</p>')
    return "".join(parts)
