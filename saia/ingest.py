"""Загрузка сырых выгрузок в базу.

    python -m saia.ingest data/raw/gnn-fraud
    python -m saia.ingest --stats

Читает manifest.json и файлы рядом с ним, создаёт миссию, версию запроса,
снимки источников и сырые записи. Ничего не нормализует: нормализация —
следующий шаг, и она работает уже из базы.

Идемпотентность обеспечена уникальными ключами в схеме, а не проверками в
коде: повторный запуск на тех же файлах не создаёт дублей, потому что снимок
опознаётся по (миссия, источник, файл, sha256).
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterator

import psycopg
from psycopg.types.json import Jsonb

from saia import db

ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"
SUBMITTED_DATE = re.compile(r"(?<!\d)(\d{1,2} [A-Z][a-z]{2} \d{4})(?!\d)")
SUBJECT_CODE = re.compile(r"\(([a-z-]+(?:\.[A-Za-z-]+)?)\)")

# Загрузка намеренно НЕ создаёт analysis_run и не знает про паспорт методики.
# Веса и пороги не влияют на то, какие байты пришли из источника: прогон
# анализа появится, когда будут считаться признаки. Иначе правка порога в
# YAML и повторный ingest плодили бы «прогоны» на тех же самых данных.


def sha256_of_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def collection_content_hash(manifest: dict) -> str:
    """Хеш состава пакета без нестабильных времён и HTTP URL."""
    sources = {}
    for source, block in sorted(manifest.get("sources", {}).items()):
        sources[source] = [
            {"file": entry.get("file"), "sha256": entry.get("sha256")}
            for entry in block.get("files", [])
            if entry.get("file")
        ]
    payload = {
        'identity_version': 'collection-identity-0.4.2',
        "mission_id": manifest["mission_id"],
        "query_version": manifest.get("query_version"),
        'connector_version': manifest.get('connector_version'),
        'coverage': collection_coverage(manifest),
        "sources": sources,
    }
    return sha256_of_text(json.dumps(payload, sort_keys=True, ensure_ascii=False))


def collection_coverage(manifest: dict) -> dict:
    incomplete = dict(manifest.get("incomplete") or {})
    for source, block in manifest.get('sources', {}).items():
        markers = [f.get('reason', 'incomplete source page') for f in block.get('files', []) if f.get('incomplete')]
        if markers and source not in incomplete:
            incomplete[source] = markers
    source_errors = manifest.get("source_errors") or {}
    return {
        'identity_version': 'collection-identity-0.4.2',
        'mission_file_sha256': manifest.get('mission_file_sha256'),
        'expected_corpus_files': sorted([
            {'source': source, 'file': f['file'], 'sha256': f['sha256'], 'records': f['records']}
            for source, block in manifest.get('sources', {}).items() for f in block.get('files', [])
            if f.get('file') and not f['file'].startswith('field_baseline')
        ], key=lambda f: (f['source'], f['file'])),
        "incomplete": incomplete,
        "source_errors": source_errors,
        "source_modes": {
            source: {
                "access_mode": block.get("access_mode"),
                "retrieval_origin": block.get("retrieval_origin"),
                "independent_discovery": block.get("independent_discovery"),
                "total_records": block.get("total_records", 0),
                "category_scope": block.get("category_scope", "unknown"),
                "crosslist_completeness": block.get("crosslist_completeness", "unknown"),
                "field_coverage": block.get("field_coverage", "unknown"),
                "historical_text_scope": block.get("historical_text_scope", "unknown"),
                "dataset_revision": block.get("dataset_revision"),
                **({"upstream_inventory_sha256": block.get("upstream_inventory_sha256"),
                    "adapter_schema": block.get("adapter_schema"),
                    **({"record_quarantine": block["record_quarantine"]}
                       if "record_quarantine" in block else {}),
                    **({"text_scope": block["selection"]["text_scope"]}
                       if block.get("selection", {}).get("text_scope") is not None else {}),
                    **({"controlled_search_plan_sha256":
                        block["selection"]["controlled_search_plan_sha256"]}
                       if block.get("selection", {}).get("controlled_search_plan_sha256")
                       is not None else {}),
                    **({"selection_engine": block["selection"]["selection_engine"]}
                       if block.get("selection", {}).get("selection_engine")
                       is not None else {}),
                    **({"thematic_cache_not_used_reason":
                        block["selection"]["thematic_cache_not_used_reason"]}
                       if block.get("selection", {}).get(
                           "thematic_cache_not_used_reason") is not None else {}),
                    **({"cache_manifest_sha256":
                        block["selection"]["cache_manifest_sha256"]}
                       if block.get("selection", {}).get("cache_manifest_sha256")
                       is not None else {}),
                    **({"target_pack_manifest_sha256":
                        block["selection"]["target_pack_manifest_sha256"]}
                       if block.get("selection", {}).get(
                           "target_pack_manifest_sha256") is not None else {}),
                    **({"selected_target_packs":
                        block["selection"]["selected_target_packs"]}
                       if block.get("selection", {}).get("selected_target_packs")
                       else {}),
                    **({"selection_predicate_reapplied":
                        block["selection"]["selection_predicate_reapplied"]}
                       if block.get("selection", {}).get(
                           "selection_predicate_reapplied") is not None else {}),
                    **({"records_are_selected_cohort": block["records_are_selected_cohort"]}
                       if "records_are_selected_cohort" in block else {})}
                   if block.get("access_mode") == "local-arxiv-metadata-parquet" else {}),
            }
            for source, block in manifest.get("sources", {}).items()
        },
    }


def upsert_collection_batch(cur: psycopg.Cursor, manifest: dict,
                            query_version_id: str) -> int:
    coverage = collection_coverage(manifest)
    status = 'partial' if coverage['incomplete'] or coverage['source_errors'] else 'complete'
    content_hash = collection_content_hash(manifest)
    cur.execute(
        """
        INSERT INTO collection_batch
            (mission_id, query_version_id, content_sha256, connector_version,
             fetched_at, status, coverage)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (mission_id, query_version_id, content_sha256)
            DO NOTHING
        RETURNING batch_id
        """,
        (
            manifest["mission_id"], query_version_id,
            content_hash, manifest["connector_version"],
            manifest["fetch_started_utc"], status, Jsonb(coverage),
        ),
    )
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute('SELECT batch_id, status, coverage, connector_version FROM collection_batch '
                'WHERE mission_id = %s AND query_version_id = %s AND content_sha256 = %s',
                (manifest['mission_id'], query_version_id, content_hash))
    row = cur.fetchone()
    if not row or row[1:] != (status, coverage, manifest['connector_version']):
        raise ValueError('Existing collection identity has inconsistent metadata')
    return row[0]


def openalex_records(path: Path) -> Iterator[tuple[str, dict]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    for work in payload.get("results", []):
        identifier = work.get("id")
        if identifier:
            yield identifier, work


def arxiv_records(path: Path) -> Iterator[tuple[str, dict]]:
    """arXiv отдаёт Atom XML. В базу кладём разобранную запись целиком.

    Это не нормализация: поля не переименовываются и не приводятся к общей
    модели, XML лишь переводится в JSON, потому что JSONB нельзя запросить
    по XML. Исходный .xml остаётся на диске нетронутым.
    """
    root = ET.parse(path).getroot()
    for entry in root.findall(f"{ATOM}entry"):
        record: dict[str, Any] = {}
        for child in entry:
            tag = child.tag.replace(ATOM, "").replace(ARXIV_NS, "arxiv_")
            value: Any
            if tag == "author":
                name = child.find(f"{ATOM}name")
                value = {"name": name.text if name is not None else None}
            elif tag in ("category", "link"):
                value = dict(child.attrib)
            else:
                value = (child.text or "").strip()
            record.setdefault(tag, [])
            record[tag].append(value)

        # Одиночные поля разворачиваем из списков, множественные оставляем.
        for key in ("id", "title", "summary", "published", "updated", "arxiv_doi",
                    "arxiv_journal_ref", "arxiv_comment", "arxiv_primary_category"):
            if key in record and len(record[key]) == 1:
                record[key] = record[key][0]

        identifier = record.get("id")
        if isinstance(identifier, str) and identifier:
            yield identifier.rsplit("/", 1)[-1], record


def _iso_date(value: str, *, last: bool = False) -> str:
    matches = SUBMITTED_DATE.findall(value or "")
    if not matches:
        return ""
    selected = matches[-1] if last else matches[0]
    return datetime.strptime(selected, "%d %b %Y").date().isoformat()


def arxiv_snapshot_records(path: Path, mission: dict | None = None) -> Iterator[tuple[str, dict]]:
    """Перевести строку Parquet-зеркала в форму официальных arXiv-метаданных.

    Сам Parquet остаётся неизменным в raw-каталоге. В JSONB сохраняется также
    исходная строка дат и ревизия набора, чтобы преобразование можно было
    проверить. Первая дата и дата последней ревизии не смешиваются.
    """
    try:
        import pyarrow.parquet as pq
    except ImportError as error:
        raise RuntimeError(
            "для Parquet-среза установите optional dependency: pip install -e '.[bulk]'"
        ) from error
    parquet = pq.ParquetFile(path)
    if "arxiv_id" not in parquet.schema_arrow.names:
        from saia.arxiv_metadata import records
        yield from records(path, mission)
        return
    required = {"arxiv_id", "submission_date", "subjects", "title", "abstract"}
    if not required.issubset(parquet.schema_arrow.names):
        raise ValueError("Unsupported legacy arXiv snapshot schema")
    table = pq.read_table(path)
    for row in table.to_pylist():
        identifier = str(row.get("arxiv_id") or "").strip()
        if not identifier:
            continue
        submitted = str(row.get("submission_date") or "")
        created = _iso_date(submitted)
        revised = _iso_date(submitted, last=True) or created
        period = (mission or {}).get("period") or {}
        if period.get("from") and (not created or created < period["from"]):
            continue
        if period.get("to") and (not created or created > period["to"]):
            continue
        subject_text = str(row.get("subjects") or "")
        categories = SUBJECT_CODE.findall(subject_text)
        primary = SUBJECT_CODE.findall(str(row.get("primary_subject") or ""))
        if primary and primary[0] not in categories:
            categories.insert(0, primary[0])
        doi = str(row.get("doi") or "").removeprefix("https://doi.org/")
        yield identifier, {
            "id": identifier,
            "created": created,
            "updated": revised,
            "title": row.get("title") or "",
            "summary": row.get("abstract") or "",
            "categories": categories,
            "author": [{"name": name} for name in (row.get("authors") or [])],
            "arxiv_doi": doi,
            "arxiv_journal_ref": "",
            "arxiv_comment": row.get("comments") or "",
            "arxiv_primary_category": primary[0] if primary else None,
            "_source_format": "hf-arxiv-parquet-snapshot",
            "_snapshot_file": path.name,
            "_submission_date_raw": submitted,
        }


class _ArxivMetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.values: dict[str, list[str]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "meta":
            return
        values = dict(attrs)
        name, content = values.get("name"), values.get("content")
        if name and name.startswith("citation_") and content:
            self.values.setdefault(name, []).append(content)


def arxiv_html_records(path: Path) -> Iterator[tuple[str, dict]]:
    """Разобрать meta-теги сохранённой официальной страницы arXiv.

    HTML остаётся нетронутым на диске. ``citation_date`` — первая подача,
    тогда как ``citation_online_date`` может обозначать позднюю замену;
    смешение этих дат сдвигает ретроспективный сигнал в будущее.
    """
    parser = _ArxivMetaParser()
    parser.feed(path.read_text(encoding="utf-8", errors="replace"))
    meta = parser.values
    identifiers = meta.get("citation_arxiv_id") or []
    if not identifiers:
        return
    arxiv_id = identifiers[0]
    submitted = (meta.get("citation_date") or [""])[0].replace("/", "-")
    yield arxiv_id, {
        "id": arxiv_id,
        "created": submitted,
        "updated": (meta.get("citation_online_date") or [submitted])[0].replace("/", "-"),
        "title": (meta.get("citation_title") or [""])[0],
        "summary": (meta.get("citation_abstract") or [""])[0],
        "author": [{"name": name} for name in meta.get("citation_author", [])],
        "arxiv_doi": (meta.get("citation_doi") or [""])[0],
        "_source_format": "arxiv-abs-html",
    }


OAI_NS = "{http://www.openarchives.org/OAI/2.0/}"
ARXIV_OAI_NS = "{http://arxiv.org/OAI/arXiv/}"


def arxiv_oai_records(path: Path, mission: dict) -> Iterator[tuple[str, dict]]:
    """Разбор OAI-PMH выдачи arXiv с отбором по категориям и дате подачи.

    Отбор делается здесь, а не в запросе, по двум причинам, обе внешние.
    Наборы OAI грубее категорий: доступен cs целиком, cs.LG отдельно — нет.
    И параметры from/until фильтруют по дате ИЗМЕНЕНИЯ записи, а не подачи,
    поэтому окно запроса взято с запасом, а настоящий период применяется к
    полю created.

    Это отбор подмножества, а не правка записей: сырые XML на диске
    остаются нетронутыми, и любой отбор воспроизводится из них заново.
    """
    wanted = set(mission["query"].get("arxiv_categories") or [])
    period = mission.get("period", {})
    date_from, date_to = period.get("from"), period.get("to")

    root = ET.parse(path).getroot()
    for record in root.iter(f"{OAI_NS}record"):
        meta = record.find(f"{OAI_NS}metadata/{ARXIV_OAI_NS}arXiv")
        if meta is None:
            continue

        def text(tag: str) -> str:
            node = meta.find(f"{ARXIV_OAI_NS}{tag}")
            return " ".join((node.text or "").split()) if node is not None else ""

        created = text("created")
        if date_from and created and created < date_from:
            continue
        if date_to and created and created > date_to:
            continue

        categories = text("categories").split()
        if wanted and not (wanted & set(categories)):
            continue

        authors = []
        for author in meta.iter(f"{ARXIV_OAI_NS}author"):
            keyname = author.find(f"{ARXIV_OAI_NS}keyname")
            forenames = author.find(f"{ARXIV_OAI_NS}forenames")
            name = " ".join(part.text.strip() for part in (forenames, keyname)
                            if part is not None and part.text)
            if name:
                authors.append({"name": name})

        identifier = text("id")
        if not identifier:
            continue

        yield identifier, {
            "id": identifier,
            "created": created,
            "updated": text("updated"),
            "title": text("title"),
            "summary": text("abstract"),
            "categories": categories,
            "author": authors,
            "arxiv_doi": text("doi"),
            "arxiv_journal_ref": text("journal-ref"),
            "arxiv_comment": text("comments"),
            "_record_parser_version": "arxiv-oai-reader-0.4.5",
            "_source_format": "oai-pmh",
        }


READERS = {
    "openalex": lambda path, mission: openalex_records(path),
    "arxiv": lambda path, mission: (
        arxiv_snapshot_records(path, mission)
        if path.suffix.lower() == ".parquet" else
        arxiv_html_records(path)
        if path.suffix.lower() == ".html" else
        arxiv_oai_records(path, mission)
        if (
            mission["query"].get("arxiv_oai")
            or mission["query"].get("arxiv_linked_from_openalex")
        ) else arxiv_records(path)
    ),
}


def upsert_mission(cur: psycopg.Cursor, mission: dict) -> None:
    cur.execute(
        """
        INSERT INTO mission (mission_id, title, question, as_of_date,
                             period_from, period_to, sources)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (mission_id) DO UPDATE
            SET title = EXCLUDED.title,
                question = EXCLUDED.question,
                as_of_date = EXCLUDED.as_of_date
        """,
        (
            mission["mission_id"],
            mission["title"],
            mission.get("question"),
            mission["as_of_date"],
            mission.get("period", {}).get("from"),
            mission.get("period", {}).get("to"),
            mission["sources"],
        ),
    )


def upsert_query_version(cur: psycopg.Cursor, mission: dict, raw_text: str) -> str:
    query_version_id = mission["query_version"]
    expansion_source = mission.get("expansion_source", "manual")
    # The database column has a deliberately small provenance vocabulary.
    # These two frozen pilot descriptors are more specific *manual* choices;
    # retain their exact wording in the immutable payload, but use the schema
    # category for indexing. Do not silently coerce arbitrary unknown values.
    if expansion_source in {
        "manual_pilot_from_focus_profile",
        "manual_pilot_from_full_audit_2026-09-22",
    }:
        expansion_source = "manual"
    cur.execute(
        """
        INSERT INTO query_version (query_version_id, mission_id, version, terms,
                                   exclusions, parent_field, expansion_source,
                                   payload, content_sha256)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (query_version_id) DO NOTHING
        """,
        (
            query_version_id,
            mission["mission_id"],
            int(query_version_id.rsplit("/v", 1)[-1]) if "/v" in query_version_id else 1,
            mission["query"]["terms"],
            mission["query"].get("exclusions", []),
            Jsonb(mission.get("parent_field")),
            expansion_source,
            Jsonb(mission),
            sha256_of_text(raw_text),
        ),
    )
    cur.execute('SELECT mission_id, content_sha256, payload FROM query_version '
                'WHERE query_version_id = %s', (query_version_id,))
    saved = cur.fetchone()
    if not saved or saved[0] != mission['mission_id'] or saved[1] != sha256_of_text(raw_text) or saved[2] != mission:
        raise ValueError('Версия запроса уже связана с другими настройками; создайте новую версию и пакет.')
    return query_version_id


def validate_collection_input(root: Path, manifest: dict, mission: dict, raw_text: str) -> None:
    """Fail before DB mutation; technical completion cannot hide absent or changed files."""
    if manifest['mission_id'] != mission['mission_id'] or manifest.get('query_version') != mission['query_version']:
        raise ValueError('Миссия или версия запроса не совпадает с пакетом сбора.')
    if manifest.get('mission_file_sha256') != sha256_of_text(raw_text):
        raise ValueError('Контрольная сумма настроек не совпадает с использованной при сборе.')
    declared_sources = set(mission.get('sources') or [])
    package_sources = set(manifest.get('sources') or {})
    # Legacy fixture/packages may predate an explicit ``sources`` field.
    # New source profiles always declare it and then require exact equality.
    if declared_sources and package_sources != declared_sources:
        raise ValueError('Источники пакета должны точно совпадать с закреплённым профилем миссии.')
    controlled = mission.get('controlled_search_plan')
    local_arxiv = manifest.get('sources', {}).get('arxiv', {})
    if controlled and local_arxiv.get('access_mode') == 'local-arxiv-metadata-parquet':
        from saia.query_expansion import digest
        recorded = local_arxiv.get('selection', {}).get('controlled_search_plan_sha256')
        if recorded != digest(controlled):
            raise ValueError('План отбора local arXiv не совпадает с закреплённым запросом.')
    local_policy = mission.get('query', {}).get('arxiv_local_snapshot', {})
    if local_policy:
        from saia.arxiv_metadata import text_scope
        scope = text_scope(mission)
        recorded_scope = manifest.get('sources', {}).get('arxiv', {}).get('selection', {}).get('text_scope')
        if recorded_scope != scope:
            raise ValueError('Text scope differs from the frozen mission')
    quarantine = manifest.get('sources', {}).get('arxiv', {}).get('record_quarantine')
    if quarantine is not None or local_policy.get('record_error_policy') == 'quarantine':
        from saia.arxiv_local import validate_quarantine
        validate_quarantine(root, manifest, mission)
    for source, block in manifest['sources'].items():
        if source not in ('openalex', 'arxiv', 'crossref'):
            raise ValueError('Неизвестный источник в пакете.')
        seen_names = set()
        for entry in block['files']:
            name = entry.get('file')
            if not name:
                if not entry.get('incomplete'):
                    raise ValueError('Пустое имя файла без признака незавершённой загрузки.')
                continue
            if Path(name).name != name:
                raise ValueError('Файл должен находиться в каталоге своего источника.')
            if name in seen_names:
                raise ValueError('Повторён файл в манифесте: ' + name)
            seen_names.add(name)
            if not isinstance(entry.get('records'), int) or isinstance(entry.get('records'), bool) or entry['records'] < 0:
                raise ValueError('Нужно неотрицательное целое число строк файла: ' + name)
            path = root / source / name
            source_dir = (root / source).resolve()
            if (not source_dir.is_relative_to(root.resolve()) or not path.is_file()
                    or not path.resolve().is_relative_to(source_dir)):
                raise ValueError('Заявленный файл отсутствует или выходит за каталог источника: ' + name)
            h = hashlib.sha256()
            with path.open('rb') as handle:
                for data in iter(lambda: handle.read(65536), b''):
                    h.update(data)
            if h.hexdigest() != entry.get('sha256'):
                raise ValueError('Контрольная сумма файла не совпадает: ' + name)


def ingest(root: Path, verbose: bool = True) -> dict:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    project_root = Path(__file__).resolve().parents[1]
    mission_id = manifest['mission_id']
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', mission_id):
        raise ValueError('Некорректный идентификатор миссии.')
    frozen_name = manifest.get('mission_snapshot_file')
    if frozen_name and Path(frozen_name).name != frozen_name:
        raise ValueError('Снимок настроек должен находиться внутри пакета.')
    mission_path = root / frozen_name if frozen_name else project_root / "missions" / f"{mission_id}.json"
    if frozen_name and not mission_path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Снимок настроек выходит за каталог пакета.')
    mission_text = mission_path.read_bytes().decode('utf-8')
    mission = json.loads(mission_text)
    validate_collection_input(root, manifest, mission, mission_text)

    summary = {"snapshots": 0, "records": 0, "skipped_snapshots": 0}

    with db.connect() as conn:
        with conn.cursor() as cur:
            upsert_mission(cur, mission)
            query_version_id = upsert_query_version(cur, mission, mission_text)
            batch_id = upsert_collection_batch(cur, manifest, query_version_id)
            summary["batch_id"] = batch_id
            cur.execute('SELECT seal_status FROM collection_batch WHERE batch_id = %s FOR UPDATE', (batch_id,))
            if cur.fetchone()[0] != 'open':
                summary['already_sealed'] = True
                return summary

            for source, block in manifest["sources"].items():
                reader = READERS.get(source)
                if reader is None:
                    if verbose:
                        print(f"  {source}: читателя нет, пропускаю")
                    continue

                for entry in block["files"]:
                    file_name = entry.get("file")
                    # Незавершённый источник сохраняет в manifest причину
                    # разрыва покрытия отдельной записью без файла. Это не
                    # снимок и не должно ломать импорт успешно полученных
                    # страниц того же источника.
                    if not file_name:
                        continue
                    # Агрегат по родительской области — не корпус работ,
                    # его место в признаках, а не в сырых записях.
                    if file_name.startswith("field_baseline"):
                        continue

                    path = root / source / file_name
                    if not path.exists():
                        raise ValueError('Файл исчез после проверки: ' + file_name)

                    cur.execute(
                        """
                        INSERT INTO source_snapshot
                            (mission_id, query_version_id, source, connector_version,
                             request_url, http_status, file_name, file_sha256,
                             record_count, fetched_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (mission_id, query_version_id, source, file_name, file_sha256)
                            DO NOTHING
                        RETURNING snapshot_id
                        """,
                        (
                            mission["mission_id"], query_version_id, source,
                            manifest["connector_version"], entry.get("url"),
                            entry.get("http_status"), file_name, entry["sha256"],
                            entry["records"], manifest["fetch_started_utc"],
                        ),
                    )
                    row = cur.fetchone()
                    if row is None:
                        cur.execute('SELECT snapshot_id FROM source_snapshot WHERE mission_id = %s '
                                    'AND query_version_id = %s AND source = %s AND file_name = %s AND file_sha256 = %s',
                                    (mission['mission_id'], query_version_id, source, file_name, entry['sha256']))
                        row = cur.fetchone()
                    snapshot_id = row[0]
                    cur.execute(
                        "INSERT INTO collection_batch_snapshot (batch_id, snapshot_id) "
                        "VALUES (%s, %s) ON CONFLICT DO NOTHING",
                        (batch_id, snapshot_id),
                    )
                    cur.execute(
                        "SELECT EXISTS (SELECT 1 FROM raw_record WHERE snapshot_id = %s)",
                        (snapshot_id,),
                    )
                    if cur.fetchone()[0]:
                        summary["skipped_snapshots"] += 1
                        if verbose:
                            print(f"  {source}/{file_name}: уже загружен")
                        continue
                    count = 0
                    for record_id, payload in reader(path, mission):
                        cur.execute(
                            """
                            INSERT INTO raw_record
                                (snapshot_id, source, source_record_id, payload, content_sha256)
                            VALUES (%s, %s, %s, %s, %s)
                            ON CONFLICT (snapshot_id, source_record_id) DO NOTHING
                            """,
                            (
                                snapshot_id, source, record_id, Jsonb(payload),
                                sha256_of_text(json.dumps(payload, sort_keys=True, ensure_ascii=False)),
                            ),
                        )
                        count += 1
                    if block.get('records_are_selected_cohort') and count != entry['records']:
                        raise ValueError(
                            f"Число проверенных записей {source}/{file_name} не совпало "
                            f"с манифестом: {count} != {entry['records']}"
                        )
                    summary["snapshots"] += 1
                    summary["records"] += count
                    summary["offered"] = summary.get("offered", 0) + entry["records"]
                    if verbose and (count or entry["records"]):
                        # Показываем и сколько было в файле, и сколько взято:
                        # при отборе по категориям разрыв огромный, и молчать
                        # о нём значит скрывать, какая часть выгрузки не нужна.
                        print(f"  {source}/{file_name}: взято {count} из {entry['records']}")

            validate_collection_input(root, manifest, mission, mission_text)
            cur.execute("UPDATE collection_batch SET seal_status = 'sealed', sealed_at = now() WHERE batch_id = %s", (batch_id,))
        conn.commit()

    return summary


def stats() -> str:
    queries = [
        ("миссии", "SELECT count(*) FROM mission"),
        ("версии запроса", "SELECT count(*) FROM query_version"),
        ("снимки источников", "SELECT count(*) FROM source_snapshot"),
        ("сырые записи", "SELECT count(*) FROM raw_record"),
        ("прогоны", "SELECT count(*) FROM analysis_run"),
    ]
    lines = []
    with db.connect() as conn, conn.cursor() as cur:
        for label, sql in queries:
            cur.execute(sql)
            lines.append(f"  {label:<22} {cur.fetchone()[0]}")

        cur.execute(
            """
            SELECT source, count(*), min(ingested_at)::date
            FROM raw_record GROUP BY source ORDER BY source
            """
        )
        rows = cur.fetchall()
        if rows:
            lines.append("\n  по источникам:")
            for source, count, day in rows:
                lines.append(f"    {source:<20} {count:>6} записей, загружено {day}")

        cur.execute(
            """
            SELECT run_id, as_of_date, methodology_hash, gates_configuration, status
            FROM analysis_run ORDER BY run_id DESC LIMIT 5
            """
        )
        runs = cur.fetchall()
        if runs:
            lines.append("\n  последние прогоны:")
            for run_id, as_of, hash_, gates, status in runs:
                lines.append(f"    #{run_id}  срез {as_of}  методика {hash_}  шлюзы {gates}  {status}")

    return "\n".join(lines)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Загрузка сырых выгрузок Horizon в базу")
    parser.add_argument("path", nargs="?", help="каталог выгрузки, например data/raw/gnn-fraud")
    parser.add_argument("--stats", action="store_true", help="показать содержимое базы")
    args = parser.parse_args()

    if args.stats:
        print(stats())
        return 0

    if not args.path:
        parser.error("укажите каталог выгрузки или --stats")

    root = Path(args.path)
    if not (root / "manifest.json").exists():
        print(f"в {root} нет manifest.json")
        return 1

    print(f"Загружаю {root} ...")
    summary = ingest(root)
    offered = summary.get("offered", 0)
    print(
        f"\nГотово. Снимков: {summary['snapshots']}"
        f" (пропущено как уже загруженные: {summary['skipped_snapshots']}),"
        f" записей загружено: {summary['records']}"
        + (f" из {offered} в выгрузке"
           f" ({100 * summary['records'] / offered:.1f}%)" if offered else "")
        + "."
    )
    print("\nСостояние базы:")
    print(stats())
    return 0


if __name__ == "__main__":
    sys.exit(main())
