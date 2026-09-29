-- SAIA 013 — история решений фильтра качества.
-- Новая версия политики не должна стирать решение, на котором был построен
-- прошлый прогон. Одинаковая версия идемпотентно пересчитывается, разные
-- версии остаются рядом.

ALTER TABLE work_quality DROP CONSTRAINT work_quality_pkey;
ALTER TABLE work_quality ADD PRIMARY KEY (work_id, policy_version);
CREATE INDEX work_quality_latest_idx ON work_quality (work_id, evaluated_at DESC);

INSERT INTO schema_migrations (version) VALUES ('013_quality_history');
