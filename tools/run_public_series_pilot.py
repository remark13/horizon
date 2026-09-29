"""Reproduce isolated benchmark from downloaded public CSVs, stdlib only."""
import csv
import hashlib
import json
from pathlib import Path

from saia.public_series_benchmark import AI_TERMS, features, outcomes, metrics

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/external_benchmark_pilot"
RAW = OUT / "raw"
REPO = "https://github.com/ddofer/Trends"
SHA = "81711b94181bb8d1f5bd93379670bf3250dfc343"
PINNED_INPUT_SHA256 = {
    "a_terms_PubMed by Year.csv": "5718c583fe5e7624439b97874801988ce83c0ec6f6e6f379db6498bec8a31496",
    "b_terms_PubMed by Year.csv": "27bbb55fbae5833803243750a2b78ca48c6850408e687a95bd8ea08a2ee1963f",
    "more_terms_PubMed by Year.csv": "3da15d181597a5325a6f71ad6acff311ec833dddc12f7dd6317b702907b14ce5",
    "unique_terms_v6_output.csv": "79bef1ce08516534f2a9340a33506a03312aa59b3c5af59c207f2b56a9320344",
}


def run():
    for name, expected in PINNED_INPUT_SHA256.items():
        actual = hashlib.sha256((RAW/name).read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"Input differs from verified pinned commit: {name}")
    terms = [r["variable"] for r in csv.DictReader(
        (RAW / "unique_terms_v6_output.csv").open())]
    series = {}
    for name in ["a_terms_PubMed by Year.csv", "b_terms_PubMed by Year.csv",
                 "more_terms_PubMed by Year.csv"]:
        with (RAW/name).open() as stream:
            reader = csv.DictReader(stream)
            for row in reader:
                year = int(row["Year"][:4])
                for term, value in row.items():
                    if term != "Year" and value != "":
                        if year in series.setdefault(term, {}):
                            raise ValueError(f"Duplicate term/year: {term}/{year}")
                        series[term][year] = float(value)
    rows, excluded = [], []
    for freeze in (2010, 2015):
        for term in terms:
            try:
                f = features(series[term], freeze)
                o = outcomes(series[term], freeze, f["recent_mean_per_100k"])
            except (ValueError, KeyError) as error:
                excluded.append({"term": term, "freeze": freeze, "reason": str(error)})
                continue
            row = {"term": term, "freeze": freeze, "ai_related": term in AI_TERMS,
                   **f, **o, "always_positive": True,
                   "source": REPO + "/tree/" + SHA,
                   "search": "https://pubmed.ncbi.nlm.nih.gov/?term=" + term.replace(" ", "+")}
            if row["eligible"]:
                row["classification"] = ("TP" if o["grew_mean_50pct"] else "FP") if f["candidate"] else ("FN" if o["grew_mean_50pct"] else "TN")
                rows.append(row)
            else:
                excluded.append({"term": term, "freeze": freeze,
                                 "reason": "recent_mean_below_1_per_100k"})
    summary = []
    for freeze in (2010, 2015):
        for subset in ("all", "low_visibility", "ai_related"):
            selected = [r for r in rows if r["freeze"] == freeze
                        and (subset == "all" or r[subset])]
            for label in ("grew_mean_50pct", "grew_endpoint_15pct"):
                for prediction in ("candidate", "last_year_baseline", "always_positive"):
                    summary.append({"freeze": freeze, "subset": subset,
                                    "label": label, "prediction": prediction,
                                    **metrics(selected, prediction, label)})
    # Mutating every future value must not change any calculated feature.
    for freeze in (2010, 2015):
        for term in terms:
            s = series[term]
            modified = {y: (v + 999999 if y > freeze else v) for y, v in s.items()}
            assert features(s, freeze) == features(modified, freeze)
    OUT.mkdir(exist_ok=True)
    def save_csv(name, values):
        with (OUT/name).open("w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(values[0]))
            writer.writeheader()
            writer.writerows(values)
    save_csv("topic_outcomes.csv", rows)
    save_csv("ai_topic_outcomes.csv", [r for r in rows if r["ai_related"]])
    for classification in ("TP", "FP", "FN", "TN"):
        save_csv(f"cases_{classification}.csv", [r for r in rows if r["classification"] == classification])
    save_csv("metrics.csv", summary)
    if excluded:
        save_csv("excluded.csv", excluded)
    manifest = {"source": REPO, "snapshot_commit": SHA,
                "download_date": "2026-09-16", "protocol_version": 1,
                "terms": len(terms), "evaluated_rows": len(rows),
                "excluded_rows": len(excluded), "future_feature_invariance": True,
                "benchmark_input_files_match_pinned_commit": True,
                "license": "No explicit repository-wide dataset license found; clarify before redistribution",
                "files": [{"name": p.name, "bytes": p.stat().st_size,
                           "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                          for p in sorted(RAW.iterdir()) if p.is_file()]}
    (OUT/"manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    (OUT/"results.json").write_text(json.dumps(summary, indent=2))
    build_report(rows, summary, manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    for result in summary:
        if result["label"] == "grew_mean_50pct" and result["prediction"] != "always_positive":
            print(json.dumps(result))


def build_report(rows, summary, manifest):
    def pct(v):
        return "не определено" if v is None else f"{v:.1%}"
    def metric(freeze, subset, pred="candidate", label="grew_mean_50pct"):
        return next(r for r in summary if (r["freeze"], r["subset"], r["prediction"], r["label"])
                    == (freeze, subset, pred, label))
    table = ["| Данные до конца года | Темы | Кандидаты | Рост подтвердился | Ложные срабатывания | Precision | Recall |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for freeze in (2010, 2015):
        m = metric(freeze, "all")
        table.append(f"| {freeze} | {m['n']} | {m['TP']+m['FP']} | {m['TP']} | {m['FP']} | {pct(m['precision'])} | {pct(m['recall'])} |")
    ai = ["| Поисковая тема | Год | Ранее: средняя доля | Будущие пять лет: средняя доля | Рост | Кандидат | Исход |",
          "|---|---:|---:|---:|---:|---|---|"]
    for r in rows:
        if r["ai_related"]:
            ai.append(f"| {r['term']} | {r['freeze']} | {r['recent_mean_per_100k']:.2f} | {r['future_mean_per_100k']:.2f} | {r['future_mean_ratio']:.2f}× | {'да' if r['candidate'] else 'нет'} | {r['classification']} |")
    fp = [r for r in rows if r["classification"] == "FP"]
    positives = [r for r in rows if r["classification"] == "TP"]
    text = f"""# SAIA: первый пилот на открытом наборе публикационных рядов

Дата: 16 сентября 2026. Протокол: версия 1.

## 1. Итог

Пилот выполнен на публичных таблицах SciTrends. Проверены {manifest['terms']} поисковых
тем в двух исторических периодах. В анализ вошли {manifest['evaluated_rows']} строк «тема + период»;
{manifest['excluded_rows']} исключены из-за крайне малой средней доли. Это {manifest['terms']} названий
из файла репозитория, а не заявленные в аннотации статьи 125 тем: разницу сохраняем явно.

Простое правило отобрало 16 кандидатов за два периода. Заданный порог последующего
роста достигли 12. Это не 16 независимых технологий: некоторые темы повторяются.
Результат нельзя интерпретировать как «точность SAIA 75%». Полная SAIA здесь не
запускалась; проверен отдельный модуль на заранее заданных поисковых рядах PubMed.

AI Phase Transitions скачать не удалось. Zenodo вернул HTTP 403, в браузере пояснил
блокировку из-за необычного трафика сети. Матрица авторов этого исследования нами
не воспроизведена. Поэтому использован доступный резервный набор SciTrends.

## 2. Источники и что мы действительно скачали

- [Статья Dan Ofer и Michal Linial, What’s next? Forecasting scientific research trends](https://arxiv.org/abs/2305.04133).
- [Репозиторий со скачанными данными](https://github.com/ddofer/Trends/tree/{SHA}).
- [Таблица a_terms](https://github.com/ddofer/Trends/blob/{SHA}/a_terms_PubMed%20by%20Year.csv),
  [b_terms](https://github.com/ddofer/Trends/blob/{SHA}/b_terms_PubMed%20by%20Year.csv),
  [more_terms](https://github.com/ddofer/Trends/blob/{SHA}/more_terms_PubMed%20by%20Year.csv).
- [Список тем](https://github.com/ddofer/Trends/blob/{SHA}/unique_terms_v6_output.csv).
- [Подготовка авторских признаков](https://github.com/ddofer/Trends/blob/{SHA}/PrepData_Trends.ipynb)
  и [дополнительные авторские критерии исходов](https://github.com/ddofer/Trends/blob/{SHA}/Make%20alt%20targets.ipynb).
- [PubMed by Year](https://esperr.github.io/pubmed-by-year/): источник таблиц показывает
  результаты пропорционально объёму всей базы. [Код расчёта](https://github.com/esperr/pubmed-by-year/blob/master/index.html)
  подтверждает единицу «результатов поиска на 100 000 публикаций», а не число статей.
- Недоступный первоочередной набор: [AI Phase Transitions](https://zenodo.org/records/20635335),
  [его код и заявленные результаты](https://github.com/KurbanIntelligenceLab/ai-phase-transitions).

Оригиналы лежат в raw/. В manifest.json записаны размеры и SHA-256. Четыре файла,
непосредственно использованные для расчёта, побайтово сверены с URL фиксированного
коммита. Другие скачанные файлы сохранены для аудита, но в расчёт не подавались.
Чужие ноутбуки не выполнялись. Явной лицензии на весь набор в репозитории нет:
локальный эксперимент не заменяет уточнение прав на коммерческое распространение.

## 3. Как проводилась проверка

Для первого периода алгоритм получил только 2005–2010 годы, для второго —
2010–2015. Будущие пять лет использовались только для вычисления исхода.
Входные таблицы содержат нули; пропущенные годы не заменялись нулями.

Тема участвует, если средняя доля за три последних года ≥1 на 100 000.
Кандидат должен одновременно выполнить три условия:

1. Текущая доля ≤100 на 100 000: тема ещё относительно малозаметна.
2. Средняя доля за последние три года выросла минимум в 1.5 раза относительно
   предшествующих трёх лет: один случайный пик меньше влияет на результат.
3. Есть хотя бы два положительных годовых изменения из последних трёх:
   рост проявляется более одного раза.

Нулевая предыдущая средняя не считается бесконечным ростом. Такие темы требуют
отдельного правила «появление новой темы» и не получают положительный прогноз.

Положительный исход: средняя доля за будущие пять лет минимум в 1.5 раза выше
средней за три последних известных года. Отрицательный: порог не достигнут.
Это наши фиксированные исследовательские допущения, не экспертная разметка
слабых сигналов и не независимо предзарегистрированный эксперимент.

Дополнительно посчитан один критерий из авторского ноутбука: доля через пять лет
выросла не менее чем на 15%. Он даёт другую разметку и хранится отдельно.
Значения готовых y_pct_bins не использовались как метки будущего: они описывают
годовые изменения, а не автоматически прогноз на следующие пять лет.

## 4. Результаты и сравнение с простым ориентиром

{chr(10).join(table)}

Precision — доля кандидатов, у которых подтвердился заданный рост. Recall — доля
всех выросших тем, которые попали в кандидаты. Для полного набора recall низок,
поскольку правило намеренно исключает уже заметные темы. Такие пропуски не все
являются ошибками поиска именно слабых сигналов.

На малозаметном поднаборе:

- 2010: {metric(2010, 'low_visibility')['n']} тема, precision {pct(metric(2010, 'low_visibility')['precision'])},
  recall {pct(metric(2010, 'low_visibility')['recall'])}.
- 2015: {metric(2015, 'low_visibility')['n']} тем, precision {pct(metric(2015, 'low_visibility')['precision'])},
  recall {pct(metric(2015, 'low_visibility')['recall'])}.

Сравнение: правило «последний год вырос ≥15%» на полном наборе имеет F1
{metric(2010, 'all', 'last_year_baseline')['F1']:.3f} и {metric(2015, 'all', 'last_year_baseline')['F1']:.3f},
а наша эвристика — {metric(2010, 'all')['F1']:.3f} и {metric(2015, 'all')['F1']:.3f}.
Наша эвристика не превосходит простой ориентир по всем показателям.
На малозаметных темах её F1 выше: {metric(2010, 'low_visibility')['F1']:.3f} против
{metric(2010, 'low_visibility', 'last_year_baseline')['F1']:.3f} и
{metric(2015, 'low_visibility')['F1']:.3f} против {metric(2015, 'low_visibility', 'last_year_baseline')['F1']:.3f}.
Порогов после просмотра этих результатов мы не меняли.

## 5. Что получилось на AI-поднаборе

Доли приведены на 100 000 публикаций PubMed. TP — кандидат и рост подтвердился;
FP — кандидат, порог роста не достигнут; FN — рост без отбора; TN — нет отбора
и порог роста не достигнут.

{chr(10).join(ai)}

Machine learning на срезе 2010: средняя доля выросла с 43.40 до 67.70 на 100 000
за два трёхлетних окна; все три последних годовых перехода положительные.
Текущая доля 75.69, ниже ограничения 100. Поэтому правило отбирает кандидата.
В следующие пять лет средняя достигает 175.42, рост 2.59×: заданный исход положительный.
В 2015 та же тема уже превышает ограничение малозаметности. Её место — среди
развивающихся трендов, а не обязательно среди новых слабых сигналов.

Graph neural network отобран в обоих периодах и затем вырос в 1.73× и 1.91×.
Но без статей и точного поискового запроса нельзя подтвердить, что этот ряд
в ранние годы обозначает современные GNN. Это пример математически положительного
исхода с непроверенной тематической принадлежностью.

Artificial neural networks в 2015: предыдущая динамика снижалась, но будущая
средняя выросла в 1.80×. Правило ускорения пропустило последующий разворот.
Для системы это аргумент за отдельный режим «возобновление интереса», но сам
этот тест не доказывает, какой дополнительный признак позволил бы предсказать разворот.

Language model уже имеет большую долю в 2010. Словосочетание слишком широкое,
а способ построения запроса в исходной таблице не сохранён полностью. Такой ряд
нельзя представлять как историческую разметку тренда LLM. Neural networks также
может обозначать биологические сети. AI-поднабор из восьми названий очень мал,
темы перекрываются. 100% precision на двух и одном срабатывании не означает
подтверждённой точности модели.

## 6. Положительные и отрицательные примеры

Положительные кандидаты по выбранному правилу: {', '.join(sorted(set(r['term'] for r in positives)))}.

Четыре ложных срабатывания:

{chr(10).join(f"- {r['term']}, {r['freeze']}: будущая средняя / известная средняя = {r['future_mean_ratio']:.2f}×." for r in fp)}

Это не примеры провала технологий. Nanopore и crypto здесь выросли примерно
на 33%, но не достигли нашего порога 50%. Carbon dating вырос примерно на 10%,
savant снизился. Их можно использовать как контрпримеры именно заданному правилу
роста. Не следует обучать классификатор «шум» на всех этих отрицательных метках.

cases_TP.csv и cases_FP.csv — отобранные кандидаты с положительным и отрицательным
исходом; cases_FN.csv и cases_TN.csv — неотобранные темы. topic_outcomes.csv
содержит все {manifest['evaluated_rows']} строки; ai_topic_outcomes.csv — 16 AI-смежных строк;
metrics.csv — оба критерия исхода и три способа прогнозирования по всем поднаборам.
Поисковые ссылки в CSV — сегодняшние запросы по названию, не идентичный архивный
запрос и не доказательство происхождения конкретной статьи.

## 7. Что это даёт для разработки SAIA

Уже сделано: изолированный модуль расчёта признаков, исходов и метрик; отдельные
положительные/отрицательные списки; сохранение оригиналов и контрольных сумм;
тест, меняющий будущие значения и проверяющий неизменность признаков.
Проверки проекта: 83 passed, 1 skipped, 2 предупреждения зависимостей.
База PostgreSQL, интерфейс, модели и пороги действующей SAIA не изменялись.

Можно перенести: нормирование объёмом направления, несколько временных окон,
раздельные признаки и будущие исходы, явные FN/FP, сравнение с простыми правилами.

Нельзя проверить на этом наборе: извлечение новых тем из текста, семантическую
кластеризацию SPECTER2/BERTopic, независимые исследовательские группы, цитатную
связность, дедупликацию OpenAlex/arXiv и влияние этих признаков на качество.
Поэтому мы не подмешивали эти ряды в рабочую базу как научные публикации.

Следующий полноценный тест:

1. Получить AI Phase Transitions при восстановлении доступа к Zenodo или загрузить
   его файлы вручную. Нужны backtest_per_topic.csv, topic_year_venue_counts.csv,
   merged_papers_keywords_keybert.xlsx. Сверить контрольные суммы архива.
2. Проверить авторские исходы и отдельно их предиктор. В коде есть несогласованность:
   текстовые комментарии говорят о 2021/2022–2025, а исполняемые константы —
   2022/2023–2025. Для воспроизведения руководствоваться проверенным протоколом,
   не автоматически доверять надписи «без утечки».
3. Связать статьи с DOI, arXiv ID и OpenAlex ID, посчитать долю успешного связывания.
   Проверить тематическую принадлежность выборки по текстам.
4. До 2022 года запускать обычный поисковый и кластерный конвейер SAIA без подсказки
   будущих названий. Связать найденные кластеры с эталоном и измерить как качество
   обнаружения, так и последующий рост. Все исключения показывать отдельно.
5. Добавить экспертно проверенные контрпримеры: неоднозначные названия,
   разовый всплеск, переименование темы, одна лаборатория, дубликаты,
   тема без достижения заранее заданного будущего порога. «Шум» и «не выросло»
   хранить разными полями.

Ключевой вывод: рабочую обвязку для воспроизводимого тестирования уже можно
использовать. Этот публичный набор не даёт достаточной разметки, чтобы признать
решённой задачу «слабый сигнал или шум» на OpenAlex и arXiv.

## 8. Воспроизведение

Из корня saia_codex_v0_3:

```sh
.venv/bin/python -m tools.run_public_series_pilot
.venv/bin/python -m pytest tests/test_public_series_benchmark.py -q
```

Сначала нужны оригиналы в outputs/external_benchmark_pilot/raw/. Подробные
пороговые допущения сохранены в docs/public-series-pilot-protocol.md.
Файл отчёта генерируется вместе с таблицами. Исходные данные не перезаписываются.
"""
    (OUT/"SAIA_пилот_открытого_датасета.md").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    run()
