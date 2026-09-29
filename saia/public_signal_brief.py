"""Self-contained cited public-reference card; no invented scientific metrics."""
from html import escape

from saia.scout_public_signals import lookup
from saia import public_signals
from saia import public_signal_i18n
from saia import jrc_card_content
from saia.public_signal_content_web import PUBLIC_CONTENT_STYLE


def render(reference: dict, lang: str = "ru") -> str:
    public_signal_i18n.language(lang)
    def esc(value):
        return escape(str(value or ""), quote=True)
    links = ""
    for row in reference["references"]:
        year = public_signals.edition_year(row)
        archive = " · Архивная подборка до 2023 года" if year and year < 2023 else ""
        issued = ("Точный день публикации не указан" if row.get("source_date_precision") == "year"
                  else "Дата источника: " + esc(row["source_date"]))
        links += (f'<section><h2>{esc(row["source_label"])}</h2>'
                  f'<p>Год подборки: {esc(year)}{archive} · {esc(row.get("type_label", "Публичная подборка"))}</p>'
                  f'<p>{issued} · {esc(public_signals.locator(row))}</p>'
                  f'<p><a href="{esc(row["source_url"])}">Открыть первоисточник</a></p>'
                  f'<p>Тематический раздел: <span lang="{lang}">{esc(public_signal_i18n.category(row["category"], lang))}</span></p></section>')
    return ('<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Horizon — опубликованный сигнал</title><style>body{font:15px/1.6 Arial,sans-serif;color:#202b43;background:#f5f7fb;margin:0;padding:32px}main{max-width:850px;background:white;padding:32px;margin:auto;border:1px solid #dfe5ef;border-radius:14px}h1{font-size:28px;line-height:1.3}h2{font-size:17px}section{border-top:1px solid #dfe5ef;margin-top:22px;padding-top:14px}a{color:#3156ad}.badge{color:#3156ad;background:#edf2fc;border-radius:20px;padding:6px 10px;font-size:12px}.note{color:#68758d} @media print{body{background:white;padding:0}main{border:0}}</style></head><body><main>'
            '<span class="badge">Из публичного источника</span><h1 lang="' + lang + '">' + esc(public_signal_i18n.title(reference, lang)) + '</h1>'
            '<p class="note">Опубликованная внешняя запись, не результат автоматического обнаружения Horizon. Дата выпуска отчёта не является датой возникновения технологии.</p>'
            + ''.join(jrc_card_content.render(value, lang) for value in reference.get('source_explanations') or [])
            +
            '<section><h2>Оценка и динамика</h2><p>Научные статьи, новости, патенты и инвестиционные сделки по этой записи отдельно не анализировались. Научный балл и динамика не рассчитаны; неизвестные значения не заменены нулями.</p><p>Наличие в подборке не подтверждает, что тема остаётся слабым сигналом сегодня. Экспертная проверка необязательна.</p></section>'
            + links + '<section><p class="note">Закреплённый каталог: ' + esc(reference['catalog_version']) + '</p></section></main></body></html>').replace('</style>', PUBLIC_CONTENT_STYLE + '</style>', 1)


def build(identifier: str, lang: str = "ru") -> str:
    return render(lookup(identifier), lang)
