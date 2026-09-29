-- SAIA 001 — миссия, версия запроса, снимки источников, сырые записи.
--
-- Границы этой миграции: всё, что нужно, чтобы честно загрузить данные и
-- доказать, откуда они взялись. Нормализованные работы, темы и признаки —
-- следующая миграция, после того как загрузка проверена на живых данных.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Миссия: постановка задачи пользователем
-- ---------------------------------------------------------------------------
CREATE TABLE mission (
    mission_id  TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    question    TEXT,
    -- Дата среза принадлежит миссии, а не запуску: один и тот же корпус
    -- анализируется на разные даты, и каждая такая дата — отдельный прогон.
    as_of_date  DATE NOT NULL,
    period_from DATE,
    period_to   DATE,
    languages   TEXT[] NOT NULL DEFAULT '{}',
    regions     TEXT[] NOT NULL DEFAULT '{}',
    sources     TEXT[] NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by  TEXT
);

COMMENT ON COLUMN mission.as_of_date IS
    'Дата среза. Публикации, версии, цитирования и отзывы позже этой даты в расчёт не попадают.';

-- ---------------------------------------------------------------------------
-- Версия запроса: словарь синонимов — версионируемый объект, а не строка в коде
-- ---------------------------------------------------------------------------
CREATE TABLE query_version (
    query_version_id TEXT PRIMARY KEY,
    mission_id       TEXT NOT NULL REFERENCES mission ON DELETE CASCADE,
    version          INT  NOT NULL,
    terms            TEXT[] NOT NULL,
    exclusions       TEXT[] NOT NULL DEFAULT '{}',
    parent_field     JSONB,
    -- Чем порождён словарь: вручную, из справочника или предложен LLM.
    -- LLM допустим на расширении запроса, но факт этого должен быть виден.
    expansion_source TEXT NOT NULL DEFAULT 'manual'
        CHECK (expansion_source IN ('manual', 'dictionary', 'llm', 'mixed')),
    payload          JSONB NOT NULL,
    content_sha256   TEXT NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (mission_id, version)
);

-- ---------------------------------------------------------------------------
-- Снимок источника: одна страница одной загрузки одного источника
-- ---------------------------------------------------------------------------
CREATE TABLE source_snapshot (
    snapshot_id       BIGSERIAL PRIMARY KEY,
    mission_id        TEXT NOT NULL REFERENCES mission ON DELETE CASCADE,
    query_version_id  TEXT NOT NULL REFERENCES query_version ON DELETE CASCADE,
    source            TEXT NOT NULL CHECK (source IN ('openalex', 'arxiv', 'crossref')),
    connector_version TEXT NOT NULL,
    request_url       TEXT,
    http_status       INT,
    file_name         TEXT,
    file_sha256       TEXT,
    record_count      INT NOT NULL DEFAULT 0,
    -- Время ЗАГРУЗКИ из источника, а не время вставки в базу: при повторном
    -- разборе тех же файлов первое не меняется, второе меняется.
    fetched_at        TIMESTAMPTZ NOT NULL,
    ingested_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (mission_id, source, file_name, file_sha256)
);

CREATE INDEX source_snapshot_mission_idx ON source_snapshot (mission_id, source);

-- ---------------------------------------------------------------------------
-- Сырая запись: неизменна по построению
-- ---------------------------------------------------------------------------
CREATE TABLE raw_record (
    raw_record_id    BIGSERIAL PRIMARY KEY,
    snapshot_id      BIGINT NOT NULL REFERENCES source_snapshot ON DELETE CASCADE,
    source           TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    payload          JSONB NOT NULL,
    content_sha256   TEXT NOT NULL,
    ingested_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (snapshot_id, source_record_id)
);

CREATE INDEX raw_record_source_id_idx ON raw_record (source, source_record_id);

-- Требование «сырые ответы не изменяются» — не соглашение между разработчиками,
-- а свойство базы. Исправление сырых данных возможно только новой загрузкой.
CREATE OR REPLACE FUNCTION raw_record_is_immutable() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION
        'raw_record неизменна: % запрещён. Чтобы исправить данные, сделайте новую загрузку.',
        TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER raw_record_no_update
    BEFORE UPDATE OR DELETE ON raw_record
    FOR EACH ROW EXECUTE FUNCTION raw_record_is_immutable();

-- ---------------------------------------------------------------------------
-- Прогон анализа
-- ---------------------------------------------------------------------------
-- Здесь разведены два разных конечных автомата, которые в документах пакета
-- смешивались: status — техническое состояние ПРОГОНА. Продуктовый статус
-- КАНДИДАТА (noise/candidate/watch/forming/...) появится в следующей миграции
-- и живёт на другой таблице.
CREATE TABLE analysis_run (
    run_id                BIGSERIAL PRIMARY KEY,
    mission_id            TEXT NOT NULL REFERENCES mission ON DELETE CASCADE,
    query_version_id      TEXT NOT NULL REFERENCES query_version ON DELETE CASCADE,
    as_of_date            DATE NOT NULL,
    methodology_version   TEXT NOT NULL,
    methodology_hash      TEXT NOT NULL,
    gates_configuration   TEXT NOT NULL,
    scoring_configuration TEXT NOT NULL,
    window_step           TEXT NOT NULL,
    status                TEXT NOT NULL DEFAULT 'created'
        CHECK (status IN ('created', 'collecting', 'normalizing', 'scoring', 'done', 'failed')),
    started_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at           TIMESTAMPTZ,
    error                 TEXT
);

COMMENT ON COLUMN analysis_run.methodology_hash IS
    'Хеш паспорта методики. Два прогона с одинаковым as_of_date, но разными хешами несопоставимы.';

CREATE INDEX analysis_run_mission_idx ON analysis_run (mission_id, as_of_date DESC);

INSERT INTO schema_migrations (version) VALUES ('001_initial');
