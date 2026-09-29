-- SAIA v0.3: точная связь каждого производного прогона с его входом.

ALTER TABLE analysis_run
    ADD COLUMN upstream_run_id BIGINT REFERENCES analysis_run ON DELETE RESTRICT,
    ADD COLUMN clustering_scale TEXT;

COMMENT ON COLUMN analysis_run.upstream_run_id IS
    'Точный завершённый прогон, данные которого использованы как вход. Для cluster это normalize run.';
COMMENT ON COLUMN analysis_run.clustering_scale IS
    'Фактически использованный масштаб micro/established, включая CLI override.';

CREATE INDEX analysis_run_upstream_idx ON analysis_run (upstream_run_id);

INSERT INTO schema_migrations (version) VALUES ('009_reproducible_runs');

