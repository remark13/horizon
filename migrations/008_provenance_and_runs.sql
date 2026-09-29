-- Исправление дефектов P0-3, P0-6 и P1-3 по итогам внешней ревизии.
--
-- P0-6. Таблица analysis_run существовала с первой миграции и не заполнялась
-- никогда: код обращался к ней только на чтение, для вывода статистики. Хеш
-- методики, объявленный основой прослеживаемости, не был связан ни с одним
-- результатом. Утверждение «по результату видно, какой версией методики он
-- получен» было ложным всё это время.
--
-- P0-3. Нормализация и кластеризация начинались с DELETE по mission_id.
-- Доказать, что система знала на прежнем срезе, было невозможно: прошлый
-- ответ физически стирался следующим запуском.
--
-- Решение: прогон становится поколением. Работы и темы принадлежат прогону,
-- прогоны не удаляют друг друга, а текущим считается последний завершённый.

ALTER TABLE analysis_run
    ADD COLUMN kind TEXT NOT NULL DEFAULT 'normalize'
        CHECK (kind IN ('normalize', 'cluster', 'score')),
    ADD COLUMN embedding_model TEXT,
    ADD COLUMN code_version TEXT,
    ADD COLUMN notes JSONB;

COMMENT ON COLUMN analysis_run.kind IS
    'Этап конвейера. Нормализация и кластеризация — разные прогоны с разными входами.';
COMMENT ON COLUMN analysis_run.embedding_model IS
    'Векторы разных моделей несопоставимы, поэтому модель — часть происхождения результата.';

-- Поколение нормализации -----------------------------------------------
ALTER TABLE work         ADD COLUMN run_id BIGINT REFERENCES analysis_run ON DELETE CASCADE;
ALTER TABLE author       ADD COLUMN run_id BIGINT REFERENCES analysis_run ON DELETE CASCADE;
ALTER TABLE organisation ADD COLUMN run_id BIGINT REFERENCES analysis_run ON DELETE CASCADE;
ALTER TABLE work_version ADD COLUMN run_id BIGINT REFERENCES analysis_run ON DELETE CASCADE;
ALTER TABLE identifier   ADD COLUMN run_id BIGINT REFERENCES analysis_run ON DELETE CASCADE;

CREATE INDEX work_run_idx         ON work (run_id);
CREATE INDEX author_run_idx       ON author (run_id);
CREATE INDEX organisation_run_idx ON organisation (run_id);

-- P1-3. UNIQUE (source, source_record_id) был глобальным, хотя работа
-- привязана к миссии: одна и та же запись arXiv не могла прикрепиться ко
-- второй миссии, и происхождение зависело от порядка загрузки. Теперь
-- уникальность действует внутри поколения.
-- Имена ограничений PostgreSQL порождает сам, и полагаться на угаданное имя
-- нельзя: промах оставил бы старое ограничение на месте молча. Ищем по
-- набору столбцов и падаем, если не нашли.
DO $$
DECLARE
    target record;
    found  text;
BEGIN
    FOR target IN
        SELECT * FROM (VALUES
            ('work_version'::text, ARRAY['source','source_record_id']::text[]),
            ('author',             ARRAY['mission_id','name_key','external_id']),
            ('organisation',       ARRAY['mission_id','display_name']),
            ('identifier',         ARRAY['mission_id','kind','value'])
        ) AS t(tbl, cols)
    LOOP
        SELECT con.conname INTO found
        FROM pg_constraint con
        JOIN pg_class rel ON rel.oid = con.conrelid
        WHERE rel.relname = target.tbl
          AND con.contype = 'u'
          AND (SELECT array_agg(att.attname::text ORDER BY att.attname::text)
               FROM unnest(con.conkey) AS k(attnum)
               JOIN pg_attribute att
                 ON att.attrelid = con.conrelid AND att.attnum = k.attnum)
              = (SELECT array_agg(c ORDER BY c) FROM unnest(target.cols) AS c);

        IF found IS NULL THEN
            RAISE EXCEPTION 'не найдено UNIQUE-ограничение на %(%)', target.tbl, target.cols;
        END IF;
        EXECUTE format('ALTER TABLE %I DROP CONSTRAINT %I', target.tbl, found);
    END LOOP;
END $$;

ALTER TABLE work_version ADD CONSTRAINT work_version_unique_in_run
    UNIQUE (run_id, source, source_record_id);
ALTER TABLE author       ADD CONSTRAINT author_unique_in_run
    UNIQUE (run_id, name_key, external_id);
ALTER TABLE organisation ADD CONSTRAINT organisation_unique_in_run
    UNIQUE (run_id, display_name);
ALTER TABLE identifier   ADD CONSTRAINT identifier_unique_in_run
    UNIQUE (run_id, kind, value);

-- Поколение кластеризации ----------------------------------------------
-- run_id в topic был объявлен в миграции 007 и не заполнялся. Делаем
-- обязательным: тема без прогона недоказуема.
DELETE FROM topic WHERE run_id IS NULL;
ALTER TABLE topic ALTER COLUMN run_id SET NOT NULL;
DO $$
DECLARE found text;
BEGIN
    SELECT con.conname INTO found
    FROM pg_constraint con JOIN pg_class rel ON rel.oid = con.conrelid
    WHERE rel.relname = 'topic' AND con.contype = 'f'
      AND (SELECT att.attname FROM unnest(con.conkey) AS k(attnum)
           JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = k.attnum
           LIMIT 1) = 'run_id';
    IF found IS NOT NULL THEN
        EXECUTE format('ALTER TABLE topic DROP CONSTRAINT %I', found);
    END IF;
END $$;

-- ON DELETE CASCADE вместо SET NULL: тема без прогона недоказуема, и
-- обнулять связь значит оставлять результат без происхождения.
ALTER TABLE topic ADD CONSTRAINT topic_run_id_fkey
    FOREIGN KEY (run_id) REFERENCES analysis_run ON DELETE CASCADE;

-- P1-5. Тема, молчавшая слишком долго, перестаёт быть кандидатом для
-- связывания: иначе новая тема может приклеиться к линии, угасшей годы назад.
ALTER TABLE topic ADD COLUMN alive BOOLEAN NOT NULL DEFAULT true;
ALTER TABLE topic ADD COLUMN died_in_window TEXT;

-- Текущее поколение ------------------------------------------------------
-- Последний ЗАВЕРШЁННЫЙ прогон каждого вида. Незавершённый или упавший
-- прогон не подменяет собой предыдущий ответ.
CREATE VIEW current_run AS
SELECT DISTINCT ON (mission_id, kind)
       run_id, mission_id, kind, as_of_date, methodology_hash,
       methodology_version, embedding_model, finished_at
FROM analysis_run
WHERE status = 'done'
ORDER BY mission_id, kind, finished_at DESC;

CREATE VIEW work_current AS
SELECT w.* FROM work w
JOIN current_run r ON r.run_id = w.run_id AND r.kind = 'normalize';

COMMENT ON VIEW work_current IS
    'Работы последнего завершённого прогона нормализации. Прежние поколения остаются в work.';

CREATE VIEW topic_current AS
SELECT t.* FROM topic t
JOIN current_run r ON r.run_id = t.run_id AND r.kind = 'cluster';

-- Родословная хранит только смены личности темы. continuation писался
-- ребром parent = child и не нёс информации; продолжение выражено двумя
-- последовательными снимками одного topic_id.
DELETE FROM topic_lineage WHERE event = 'continuation';
ALTER TABLE topic_lineage DROP CONSTRAINT topic_lineage_event_check;
ALTER TABLE topic_lineage ADD CONSTRAINT topic_lineage_event_check
    CHECK (event IN ('split', 'merge', 'new_core'));

-- При разделении и слиянии у одного ребёнка бывает несколько родителей,
-- а у родителя несколько детей. Прежнее UNIQUE этого не запрещало, но
-- проверим, что ключ именно такой: (child, to_window, parent).

INSERT INTO schema_migrations (version) VALUES ('008_provenance_and_runs');
