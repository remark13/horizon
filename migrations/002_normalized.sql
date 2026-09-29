-- SAIA 002 — нормализованный корпус: работы, версии, авторы, организации,
-- идентификаторы и журнал решений о дедупликации.
--
-- Ключевое требование пакета: «удалить дубли, не удаляя происхождение».
-- Поэтому объединение не стирает записи, а создаёт каноническую работу, к
-- которой подшиты все версии со ссылками на исходные raw_record, плюс
-- запись о том, каким правилом произошло объединение.

-- ---------------------------------------------------------------------------
-- Каноническая работа
-- ---------------------------------------------------------------------------
CREATE TABLE work (
    work_id           BIGSERIAL PRIMARY KEY,
    mission_id        TEXT NOT NULL REFERENCES mission ON DELETE CASCADE,
    canonical_title   TEXT NOT NULL,
    title_key         TEXT NOT NULL,          -- заголовок, приведённый для сравнения
    abstract          TEXT,
    type              TEXT,
    language          TEXT,

    publication_date  DATE,
    publication_year  INT,
    -- Найдено на данных: OpenAlex ставит 1 января, когда известен только год.
    -- Флаг и консервативная дата вынесены в схему, чтобы ни один расчёт не мог
    -- случайно принять заглушку за настоящий день публикации.
    date_is_imprecise BOOLEAN NOT NULL DEFAULT false,
    effective_date    DATE NOT NULL,

    cited_by_count    INT,
    counts_by_year    JSONB,                  -- цитирования по годам: единственный честный источник на срез
    is_retracted      BOOLEAN NOT NULL DEFAULT false,

    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX work_mission_idx ON work (mission_id, effective_date);
CREATE INDEX work_title_key_idx ON work (mission_id, title_key);

COMMENT ON COLUMN work.effective_date IS
    'Дата для отсечения по срезу. При неточной дате — конец года: ошибка в эту сторону скрывает часть корпуса, ошибка в обратную подсовывает знание будущего.';

-- ---------------------------------------------------------------------------
-- Версия работы: одна строка на каждую исходную запись источника
-- ---------------------------------------------------------------------------
CREATE TABLE work_version (
    work_version_id  BIGSERIAL PRIMARY KEY,
    work_id          BIGINT NOT NULL REFERENCES work ON DELETE CASCADE,
    raw_record_id    BIGINT NOT NULL REFERENCES raw_record ON DELETE CASCADE,
    source           TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    version_kind     TEXT NOT NULL DEFAULT 'unknown'
        CHECK (version_kind IN ('preprint', 'published', 'unknown')),
    version_date     DATE,
    UNIQUE (source, source_record_id)
);

CREATE INDEX work_version_work_idx ON work_version (work_id);

COMMENT ON TABLE work_version IS
    'Препринт и журнальная версия — разные строки одной работы, а не два доказательства. Счётчики независимости считаются по work, не по work_version.';

-- ---------------------------------------------------------------------------
-- Идентификаторы
-- ---------------------------------------------------------------------------
CREATE TABLE identifier (
    identifier_id BIGSERIAL PRIMARY KEY,
    mission_id    TEXT NOT NULL REFERENCES mission ON DELETE CASCADE,
    work_id       BIGINT NOT NULL REFERENCES work ON DELETE CASCADE,
    kind          TEXT NOT NULL CHECK (kind IN ('doi', 'arxiv', 'openalex', 'pmid', 'mag')),
    value         TEXT NOT NULL,
    -- Уникальность внутри миссии, а не глобально: один и тот же DOI законно
    -- встречается в корпусах разных миссий и не должен их связывать.
    UNIQUE (mission_id, kind, value)
);

CREATE INDEX identifier_work_idx ON identifier (work_id);

-- ---------------------------------------------------------------------------
-- Авторы и организации
-- ---------------------------------------------------------------------------
CREATE TABLE author (
    author_id    BIGSERIAL PRIMARY KEY,
    mission_id   TEXT NOT NULL REFERENCES mission ON DELETE CASCADE,
    external_id  TEXT,                        -- OpenAlex author id, если есть
    display_name TEXT NOT NULL,
    name_key     TEXT NOT NULL,
    UNIQUE (mission_id, name_key, external_id)
);

CREATE TABLE organisation (
    organisation_id BIGSERIAL PRIMARY KEY,
    mission_id      TEXT NOT NULL REFERENCES mission ON DELETE CASCADE,
    ror             TEXT,
    display_name    TEXT NOT NULL,
    country_code    TEXT,
    UNIQUE (mission_id, display_name)
);

CREATE TABLE work_author (
    work_author_id  BIGSERIAL PRIMARY KEY,
    work_id         BIGINT NOT NULL REFERENCES work ON DELETE CASCADE,
    author_id       BIGINT NOT NULL REFERENCES author ON DELETE CASCADE,
    organisation_id BIGINT REFERENCES organisation ON DELETE SET NULL,
    author_position INT
);

-- Выражение допустимо в уникальном ИНДЕКСЕ, но не в PRIMARY KEY.
-- COALESCE нужен, потому что у автора без аффилиации organisation_id = NULL,
-- а NULL в обычном UNIQUE не считается совпадением и пропускал бы дубли.
CREATE UNIQUE INDEX work_author_unique_idx
    ON work_author (work_id, author_id, COALESCE(organisation_id, 0));

CREATE INDEX work_author_author_idx ON work_author (author_id);
CREATE INDEX work_author_org_idx ON work_author (organisation_id);

-- ---------------------------------------------------------------------------
-- Журнал дедупликации
-- ---------------------------------------------------------------------------
-- Без этой таблицы объединение выглядит как потеря записи. С ней видно, что
-- именно и по какому правилу было признано одной работой — а значит, решение
-- можно оспорить, не перезапуская загрузку.
CREATE TABLE dedup_decision (
    dedup_id      BIGSERIAL PRIMARY KEY,
    work_id       BIGINT NOT NULL REFERENCES work ON DELETE CASCADE,
    source        TEXT NOT NULL,
    record_id     TEXT NOT NULL,
    rule          TEXT NOT NULL CHECK (rule IN (
        'doi_match', 'arxiv_id_in_openalex', 'title_author_match', 'new_work'
    )),
    matched_value TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX dedup_decision_work_idx ON dedup_decision (work_id);

INSERT INTO schema_migrations (version) VALUES ('002_normalized');
