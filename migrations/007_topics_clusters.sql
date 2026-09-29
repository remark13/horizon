-- SAIA 007 — тематические кластеры, их история по окнам и связь между окнами.
--
-- Это то, ради чего собирался широкий корпус. На корпусе из одной темы
-- половина воронки была невычислима: новизна определена как расстояние до
-- кластеров прошлого, а рост — как перцентиль среди соседних тем. Ни
-- кластеров, ни соседей там не существовало.

-- ---------------------------------------------------------------------------
-- Тема: устойчивая сущность, живущая через окна
-- ---------------------------------------------------------------------------
CREATE TABLE topic (
    topic_id        BIGSERIAL PRIMARY KEY,
    mission_id      TEXT NOT NULL REFERENCES mission ON DELETE CASCADE,
    run_id          BIGINT REFERENCES analysis_run ON DELETE SET NULL,

    label           TEXT,                    -- название по верхним терминам
    top_terms       TEXT[] NOT NULL DEFAULT '{}',

    -- Якорь: эмбеддинг ПЕРВОГО появления темы. Не пересчитывается по
    -- текущему составу — иначе тема уплывает, и через десяток окон под тем
    -- же идентификатором оказывается другой смысл.
    anchor_embedding vector NOT NULL,
    anchor_window    TEXT NOT NULL,

    first_window    TEXT NOT NULL,
    last_window     TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX topic_mission_idx ON topic (mission_id, first_window);

COMMENT ON COLUMN topic.anchor_embedding IS
    'Эмбеддинг первого появления. Смысл темы фиксируется при рождении; сопоставление окон идёт с ним, а не со скользящим центроидом.';

-- ---------------------------------------------------------------------------
-- Состояние темы в одном окне
-- ---------------------------------------------------------------------------
CREATE TABLE topic_snapshot (
    snapshot_id     BIGSERIAL PRIMARY KEY,
    topic_id        BIGINT NOT NULL REFERENCES topic ON DELETE CASCADE,
    window_key      TEXT NOT NULL,
    window_start    DATE NOT NULL,
    window_end      DATE NOT NULL,

    doc_count       INT NOT NULL DEFAULT 0,
    -- Накопленная популярность с затуханием при молчании темы:
    -- p(t) = p(t-1) + |D(t)|, а в окне без пополнения
    -- p(t) = p(t-1) * exp(-lambda * dt^2).
    popularity      DOUBLE PRECISION NOT NULL DEFAULT 0,
    slope           DOUBLE PRECISION,        -- наклон регрессии популярности в скользящем окне

    centroid        vector,                  -- центроид ТЕКУЩЕГО состава, для диагностики дрейфа
    drift_from_anchor DOUBLE PRECISION,      -- 1 - cos(centroid, anchor): насколько тема ушла от себя

    -- Перцентили считаются по распределению всех тем этого окна.
    -- Абсолютное значение популярности несопоставимо между окнами,
    -- перцентиль — сопоставим.
    popularity_percentile DOUBLE PRECISION,
    lifecycle_class TEXT CHECK (lifecycle_class IN ('noise', 'weak', 'strong')),

    UNIQUE (topic_id, window_key)
);

CREATE INDEX topic_snapshot_window_idx ON topic_snapshot (window_key);

-- ---------------------------------------------------------------------------
-- Принадлежность работы теме в окне
-- ---------------------------------------------------------------------------
CREATE TABLE topic_membership (
    topic_id    BIGINT NOT NULL REFERENCES topic ON DELETE CASCADE,
    work_id     BIGINT NOT NULL REFERENCES work ON DELETE CASCADE,
    window_key  TEXT NOT NULL,
    probability DOUBLE PRECISION,
    PRIMARY KEY (topic_id, work_id, window_key)
);

CREATE INDEX topic_membership_work_idx ON topic_membership (work_id);

-- ---------------------------------------------------------------------------
-- Связь тем между окнами
-- ---------------------------------------------------------------------------
-- Методика требует различать четыре события, а не только «та же тема»:
-- продолжение, разделение, слияние и появление нового ядра. Новое ядро —
-- главный источник кандидатов; разделение зрелой темы — частая причина
-- ложной новизны.
CREATE TABLE topic_lineage (
    lineage_id    BIGSERIAL PRIMARY KEY,
    mission_id    TEXT NOT NULL REFERENCES mission ON DELETE CASCADE,
    parent_topic  BIGINT REFERENCES topic ON DELETE CASCADE,
    child_topic   BIGINT NOT NULL REFERENCES topic ON DELETE CASCADE,
    from_window   TEXT,
    to_window     TEXT NOT NULL,
    event         TEXT NOT NULL CHECK (event IN ('continuation', 'split', 'merge', 'new_core')),
    similarity    DOUBLE PRECISION,
    UNIQUE (child_topic, to_window, parent_topic)
);

CREATE INDEX topic_lineage_child_idx ON topic_lineage (child_topic);

-- ---------------------------------------------------------------------------
-- Значения признаков
-- ---------------------------------------------------------------------------
-- Признак хранится вместе со способом нормализации и числом объектов, по
-- которым посчитан: на ранних кластерах доверительный интервал шире
-- самого значения, и показывать балл без этого числа нельзя.
CREATE TABLE feature_value (
    feature_id   BIGSERIAL PRIMARY KEY,
    topic_id     BIGINT NOT NULL REFERENCES topic ON DELETE CASCADE,
    window_key   TEXT NOT NULL,
    feature      TEXT NOT NULL,
    raw_value    DOUBLE PRECISION,
    percentile   DOUBLE PRECISION,
    n_objects    INT,
    peer_group   TEXT,
    UNIQUE (topic_id, window_key, feature)
);

CREATE INDEX feature_value_lookup_idx ON feature_value (window_key, feature);

INSERT INTO schema_migrations (version) VALUES ('007_topics_clusters');
