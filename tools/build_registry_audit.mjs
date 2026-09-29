import fs from "node:fs/promises";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const projectRoot = "/Users/a/Documents/Codex/2026-09-09/ruen-15-ml/saia_codex_v0_3";
const inputPath = `${projectRoot}/data/processed/signal_seeds.jsonl`;
const outputDir = `${projectRoot}/outputs/01a08680-0825-74d2-8b5f-65c057bbdceb`;
const outputPath = `${outputDir}/Аудит_100_слабых_сигналов_SAIA.xlsx`;
const sourceHash = "1349a208671cf9db9b036d9d137036fc8471297b6ec04fce0fc864e75e02ed03";
const asOf = "15.09.2026";
const fontFamily = "Arial";
const dark = "#17365D";
const blue = "#DCE6F1";
const pale = "#F4F7FB";
const amber = "#FFF2CC";
const red = "#FCE4D6";
const green = "#E2F0D9";
const line = "#B4C6E7";

const raw = await fs.readFile(inputPath, "utf8");
const records = raw.trim().split(/\r?\n/).filter(Boolean).map(line => JSON.parse(line));
const publicationTypes = new Set(["preprint", "scholarly_publication", "bibliographic_index"]);

const urlToRecords = new Map();
for (const record of records) {
  for (const evidence of record.evidence) {
    const rows = urlToRecords.get(evidence.url) || [];
    rows.push(record.source_number);
    urlToRecords.set(evidence.url, rows);
  }
}
const repeatedUrls = [...urlToRecords.entries()].filter(([, rows]) => new Set(rows).size > 1);
const duplicatePairs = repeatedUrls
  .map(([, rows]) => [...new Set(rows)].sort((a, b) => a - b))
  .filter(rows => rows.length > 1);
const familyByNumber = new Map();
duplicatePairs.forEach((pair, index) => {
  const family = `F${String(index + 1).padStart(2, "0")}: №${pair.join("/№")}`;
  pair.forEach(number => familyByNumber.set(number, family));
});

const futurePattern = /(?:октябр[а-яё]*|ноя(?:бр[а-яё]*)?\.?|декабр[а-яё]*)\s+2026|Q4\s*2026|4\s*кв\.?\s*2026/iu;
const auditRows = records.map(record => {
  const counts = { publication: 0, official: 0, industry: 0, web: 0 };
  for (const evidence of record.evidence) {
    if (publicationTypes.has(evidence.source_type)) counts.publication += 1;
    else if (evidence.source_type === "official_or_standard") counts.official += 1;
    else if (evidence.source_type === "industry_media") counts.industry += 1;
    else counts.web += 1;
  }
  const checkText = [
    record.title,
    record.expert_rationale,
    record.stage_original,
    record.mention_trend_original,
    ...record.evidence.map(item => item.title),
  ].join(" ");
  const futureReference = futurePattern.test(checkText);
  const family = familyByNumber.get(record.source_number) || "";
  const issues = [];
  if (!counts.publication) issues.push("Нет публикационного первоисточника");
  if (futureReference) issues.push(`Есть ссылка на событие после ${asOf}`);
  if (family) issues.push("Вероятное пересечение с другим сигналом");
  if (record.stage_code === "unknown") issues.push("Стадия не нормализована");
  return {
    ...record,
    counts,
    futureReference,
    family,
    mvpStatus: counts.publication ? "Наблюдаем в публикациях" : "Не подтверждён публикационными ссылками",
    retest: counts.publication >= 2 ? "Приоритет" : counts.publication === 1 ? "После проверки" : "Не использовать как false negative",
    issues: issues.join("; "),
  };
});

const strongCandidates = auditRows.filter(row => row.counts.publication >= 2);
const areas = [...new Set(auditRows.map(row => row.area))].sort((a, b) => a.localeCompare(b, "ru"));
const sourceRows = auditRows.flatMap(row => row.evidence.map(evidence => [
  row.source_number,
  row.title,
  evidence.title,
  evidence.url,
  evidence.domain,
  evidence.source_type,
  publicationTypes.has(evidence.source_type)
    ? "Публикационный контур MVP"
    : evidence.source_type === "official_or_standard"
      ? "Внешний официальный слой"
      : "Внешний рыночный/медийный слой",
]));

const workbook = Workbook.create();
const summary = workbook.worksheets.add("Итог");
const signals = workbook.worksheets.add("Проверка сигналов");
const candidates = workbook.worksheets.add("Кандидаты ретротеста");
const sources = workbook.worksheets.add("Источники");

for (const sheet of [summary, signals, candidates, sources]) {
  sheet.showGridLines = false;
}

// Итог
summary.getRange("A2:H2").merge();
summary.getRange("A2").values = [["Аудит реестра 100 слабых технологических сигналов"]];
summary.getRange("A2:H2").format = {
  font: { name: fontFamily, size: 15, bold: true, color: dark },
  rowHeight: 24,
};
summary.getRange("A3:H3").format.borders = { bottom: { style: "thin", color: dark } };
summary.getRange("A5:H5").merge();
summary.getRange("A5").values = [[
  "Вывод: реестр пригоден как источник гипотез и контрольных кейсов, но не как обучающий датасет. Для первого слепого ретротеста публикационного MVP подходят 8 сигналов с двумя или более научными источниками.",
]];
summary.getRange("A5:H5").format = {
  fill: amber, font: { name: fontFamily, size: 11, bold: true, color: "#7F6000" },
  wrapText: true, rowHeight: 54, verticalAlignment: "center",
};

summary.getRange("A7:B7").values = [["Показатель", "Значение"]];
summary.getRange("A8:A15").values = [
  ["Записей в реестре"],
  ["Сигналов хотя бы с одной научной публикацией"],
  ["Приоритетных кейсов: две и более научные публикации"],
  ["Сигналов без публикационного подтверждения в файле"],
  ["Ссылок всего"],
  ["Уникальных ссылок"],
  [`Сигналов со ссылкой на событие после ${asOf}`],
  ["Независимых отрицательных примеров"],
];
summary.getRange("B8:B11").formulas = [
  ["=COUNTA('Проверка сигналов'!$A$2:$A$101)"],
  ["=COUNTIF('Проверка сигналов'!$J$2:$J$101,\"Наблюдаем в публикациях\")"],
  ["=COUNTIF('Проверка сигналов'!$K$2:$K$101,\"Приоритет\")"],
  ["=COUNTIF('Проверка сигналов'!$J$2:$J$101,\"Не подтверждён публикационными ссылками\")"],
];
summary.getRange("B12:B13").values = [[sourceRows.length], [urlToRecords.size]];
summary.getRange("B14").values = [[auditRows.filter(row => row.futureReference).length]];
summary.getRange("B15").values = [[0]];
summary.getRange("D7:H7").merge();
summary.getRange("D7").values = [["Как использовать реестр"]];
summary.getRange("D8:H15").merge();
summary.getRange("D8").values = [[
  "1. Восемь приоритетных кейсов использовать как предварительные положительные примеры после ручной проверки первоисточников.\n\n" +
  "2. Для каждого кейса установить дату среза до ускорения темы и сформировать широкий корпус OpenAlex/arXiv без названия проверяемого тренда.\n\n" +
  "3. Остальные 84 записи не считать false negative: приложенные доказательства преимущественно находятся вне публикационного контура.\n\n" +
  "4. Добавить отрицательные и пограничные классы, label_as_of, даты источников и признак observed/planned.\n\n" +
  "5. Две пары пересекающихся сигналов объединить через signal_family_id до расчёта метрик.",
]];
summary.getRange("D8:H15").format = { fill: pale, wrapText: true, verticalAlignment: "top" };
summary.getRange("D8:H15").format.rowHeight = 25;

summary.getRange("A18:C18").values = [["Область", "Всего", "С научными источниками"]];
summary.getRange(`A19:A${18 + areas.length}`).values = areas.map(area => [area]);
for (let i = 0; i < areas.length; i += 1) {
  const row = 19 + i;
  summary.getRange(`B${row}`).formulas = [[`=COUNTIF('Проверка сигналов'!$C$2:$C$101,A${row})`]];
  summary.getRange(`C${row}`).formulas = [[`=COUNTIFS('Проверка сигналов'!$C$2:$C$101,A${row},'Проверка сигналов'!$J$2:$J$101,\"Наблюдаем в публикациях\")`]];
}
summary.getRange("E18:F18").values = [["Экспертный балл", "Число сигналов"]];
const scoreValues = [7, 6, 5, 4, 3];
summary.getRange("E19:E23").values = scoreValues.map(score => [score]);
for (let i = 0; i < scoreValues.length; i += 1) {
  const row = 19 + i;
  summary.getRange(`F${row}`).formulas = [[`=COUNTIF('Проверка сигналов'!$E$2:$E$101,E${row})`]];
}

summary.getRange("A27:H27").merge();
summary.getRange("A27").values = [["Критерий следующего слепого теста"]];
summary.getRange("A28:H30").merge();
summary.getRange("A28").values = [[
  "Тема считается обнаруженной, только если до даты среза система без целевого поискового термина формирует отдельную тематическую линию, объединяет не менее двух контрольных публикаций, проходит временные и качественные фильтры и не повышает долю ложных сигналов на сопоставимом наборе отрицательных кейсов.",
]];
summary.getRange("A28:H30").format = { fill: blue, wrapText: true, verticalAlignment: "center" };
summary.getRange("A33:H34").merge();
summary.getRange("A33").values = [[
  `Источник: «100 слабых технологических сигналов (сентябрь 2026)». SHA-256: ${sourceHash}. Дата проверки: ${asOf}. URL не проверялись на доступность в сети; аудит оценивает структуру и заявленные типы доказательств.`,
]];
summary.getRange("A33:H34").format = { font: { name: fontFamily, size: 9, italic: true, color: "#666666" }, wrapText: true };

// Построчная проверка
const signalHeaders = ["№", "Сигнал", "Область", "Стадия", "Балл", "Ссылок", "Научных", "Официальных", "Медиа", "Статус для MVP", "Кандидат ретротеста", "Проблемы", "Семейство"];
signals.getRange("A1:M1").values = [signalHeaders];
signals.getRange("A2").write(auditRows.map(row => [
  row.source_number, row.title, row.area, row.stage_original, row.expert_score,
  row.evidence.length, row.counts.publication, row.counts.official, row.counts.industry,
  row.mvpStatus, row.retest, row.issues, row.family,
]));
signals.tables.add("A1:M101", true, "SignalAuditTable");
signals.freezePanes.freezeRows(1);
signals.getRange("A2:A101").format.numberFormat = "0";
signals.getRange("E2:I101").format.numberFormat = "0";
signals.getRange("B2:D101").format.wrapText = true;
signals.getRange("J2:M101").format.wrapText = true;
signals.getRange("K2:K101").conditionalFormats.add("containsText", { text: "Приоритет", format: { fill: green, font: { bold: true, color: "#375623" } } });
signals.getRange("L2:L101").conditionalFormats.add("notContainsBlanks", { format: { fill: red, font: { color: "#9C0006" } } });

// Кандидаты ретротеста
candidates.getRange("A2:I2").merge();
candidates.getRange("A2").values = [["Приоритетные кейсы для слепого ретротеста OpenAlex/arXiv"]];
candidates.getRange("A4:I6").merge();
candidates.getRange("A4").values = [[
  "Это не готовая gold-разметка. Перед тестом нужно подтвердить даты первых версий, выбрать as-of до ускорения темы, добавить сопоставимые отрицательные кейсы и исключить название целевого тренда из запроса и словаря расширения.",
]];
candidates.getRange("A8:I8").values = [["№", "Сигнал", "Область", "Научных ссылок", "arXiv", "Стадия", "Балл", "Рекомендуемое действие", "Первоисточники"]];
candidates.getRange("A9").write(strongCandidates.map(row => {
  const publications = row.evidence.filter(item => publicationTypes.has(item.source_type));
  return [
    row.source_number, row.title, row.area, publications.length,
    publications.filter(item => item.source_type === "preprint").length,
    row.stage_original, row.expert_score,
    "Проверить v1 и назначить историческую дату среза",
    publications.map(item => item.url).join("\n"),
  ];
}));
candidates.tables.add(`A8:I${8 + strongCandidates.length}`, true, "RetestCandidateTable");
candidates.freezePanes.freezeRows(8);
candidates.getRange(`B9:I${8 + strongCandidates.length}`).format.wrapText = true;

// Реестр источников
sources.getRange("A1:G1").values = [["№ сигнала", "Сигнал", "Название источника", "URL", "Домен", "Тип", "Слой SAIA"]];
sources.getRange("A2").write(sourceRows);
sources.tables.add(`A1:G${1 + sourceRows.length}`, true, "EvidenceSourceTable");
sources.freezePanes.freezeRows(1);
sources.getRange(`B2:D${1 + sourceRows.length}`).format.wrapText = true;

// Общая типографика и заголовки
for (const sheet of [summary, signals, candidates, sources]) {
  const used = sheet.getUsedRange();
  used.format.font = { name: fontFamily, size: 10, color: "#1F1F1F" };
  used.format.verticalAlignment = "center";
}
for (const range of [summary.getRange("A7:B7"), summary.getRange("D7:H7"), summary.getRange("A18:C18"), summary.getRange("E18:F18"), signals.getRange("A1:M1"), candidates.getRange("A8:I8"), sources.getRange("A1:G1")]) {
  range.format = { fill: dark, font: { name: fontFamily, size: 10, bold: true, color: "#FFFFFF" }, horizontalAlignment: "center", verticalAlignment: "center", wrapText: true };
}
summary.getRange("A2:H2").format.font = { name: fontFamily, size: 15, bold: true, color: dark };
candidates.getRange("A2:I2").format.font = { name: fontFamily, size: 14, bold: true, color: dark };
candidates.getRange("A4:I6").format = { fill: amber, font: { name: fontFamily, size: 10, color: "#7F6000" }, wrapText: true, verticalAlignment: "center" };

summary.getRange("A:A").format.columnWidth = 43;
summary.getRange("B:C").format.columnWidth = 18;
summary.getRange("D:H").format.columnWidth = 17;
signals.getRange("A:A").format.columnWidth = 6;
signals.getRange("B:B").format.columnWidth = 48;
signals.getRange("C:C").format.columnWidth = 20;
signals.getRange("D:D").format.columnWidth = 34;
signals.getRange("E:I").format.columnWidth = 11;
signals.getRange("J:K").format.columnWidth = 27;
signals.getRange("L:L").format.columnWidth = 42;
signals.getRange("M:M").format.columnWidth = 16;
candidates.getRange("A:A").format.columnWidth = 7;
candidates.getRange("B:B").format.columnWidth = 52;
candidates.getRange("C:C").format.columnWidth = 20;
candidates.getRange("D:E").format.columnWidth = 13;
candidates.getRange("F:F").format.columnWidth = 34;
candidates.getRange("G:G").format.columnWidth = 9;
candidates.getRange("H:H").format.columnWidth = 34;
candidates.getRange("I:I").format.columnWidth = 58;
sources.getRange("A:A").format.columnWidth = 10;
sources.getRange("B:B").format.columnWidth = 50;
sources.getRange("C:C").format.columnWidth = 44;
sources.getRange("D:D").format.columnWidth = 70;
sources.getRange("E:E").format.columnWidth = 27;
sources.getRange("F:G").format.columnWidth = 27;

summary.getRange("A7:B15").format.borders = { preset: "outside", style: "thin", color: line };
summary.getRange("A18:C24").format.borders = { preset: "outside", style: "thin", color: line };
summary.getRange("E18:F23").format.borders = { preset: "outside", style: "thin", color: line };

workbook.recalculate();
await fs.mkdir(outputDir, { recursive: true });

const summaryInspect = await workbook.inspect({
  kind: "table", range: "Итог!A7:F24", include: "values,formulas",
  tableMaxRows: 20, tableMaxCols: 8, maxChars: 8000,
});
console.log(summaryInspect.ndjson);
const errorInspect = await workbook.inspect({
  kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: { useRegex: true, maxResults: 100 }, summary: "final formula error scan",
});
console.log(errorInspect.ndjson);

for (const [sheetName, range] of [
  ["Итог", "A1:H35"],
  ["Проверка сигналов", "A1:M22"],
  ["Кандидаты ретротеста", `A1:I${8 + strongCandidates.length}`],
  ["Источники", "A1:G22"],
]) {
  const preview = await workbook.render({ sheetName, range, scale: 1.2, format: "png" });
  const safeName = sheetName.replaceAll(" ", "_");
  await fs.writeFile(`${outputDir}/preview_${safeName}.png`, new Uint8Array(await preview.arrayBuffer()));
}

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
console.log(JSON.stringify({ outputPath, records: records.length, publicationBacked: auditRows.filter(row => row.counts.publication > 0).length, strongCandidates: strongCandidates.length, duplicateFamilies: duplicatePairs.length, postAsOfReferences: auditRows.filter(row => row.futureReference).length }));
