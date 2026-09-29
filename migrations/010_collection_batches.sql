-- SAIA 010 — воспроизводимый пакет сбора данных.
--
-- Снимок файла сам по себе не говорит, входит ли он в АКТУАЛЬНУЮ
-- выгрузку. После замены ошибочного ответа источника нормализация брала и
-- старый, и новый файл одной миссии. Старое сырьё удалять нельзя, поэтому
-- вводится пакет: неизменяемые снимки могут переиспользоваться в нескольких
-- пакетах, а анализ выбирает последний завершённый пакет целиком.

CREATE TABLE collection_batch (
    batch_id          BIGSERIAL PRIMARY KEY,
    mission_id        TEXT NOT NULL REFERENCES mission ON DELETE CASCADE,
    query_version_id  TEXT NOT NULL REFERENCES query_version ON DELETE CASCADE,
    content_sha256    TEXT NOT NULL,
    connector_version TEXT NOT NULL,
    fetched_at        TIMESTAMPTZ NOT NULL,
    status            TEXT NOT NULL CHECK (status IN ('complete', 'partial')),
    coverage          JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (mission_id, query_version_id, content_sha256)
);

CREATE TABLE collection_batch_snapshot (
    batch_id    BIGINT NOT NULL REFERENCES collection_batch ON DELETE CASCADE,
    snapshot_id BIGINT NOT NULL REFERENCES source_snapshot ON DELETE RESTRICT,
    PRIMARY KEY (batch_id, snapshot_id)
);

CREATE INDEX collection_batch_latest_idx
    ON collection_batch (mission_id, status, created_at DESC, batch_id DESC);

INSERT INTO schema_migrations (version) VALUES ('010_collection_batches');
