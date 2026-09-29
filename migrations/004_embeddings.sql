-- SAIA 004 — эмбеддинги работ.
--
-- Вектор хранится вместе с именем модели и размерностью: смена модели
-- меняет смысл всех расстояний, поэтому эмбеддинги, посчитанные разными
-- моделями, не должны молча смешиваться в одном расчёте.

CREATE TABLE work_embedding (
    work_id     BIGINT NOT NULL REFERENCES work ON DELETE CASCADE,
    model       TEXT   NOT NULL,
    dim         INT    NOT NULL,
    -- Размерность намеренно не зафиксирована в типе: SPECTER даёт 768,
    -- BGE-M3 — 1024, и выбор модели ещё не закрыт. Фиксация размерности
    -- здесь означала бы миграцию при каждой смене модели, а сравнивать
    -- векторы разных моделей всё равно нельзя — от этого защищает model
    -- в первичном ключе, а не тип колонки.
    embedding   vector NOT NULL,

    -- Из чего построен вектор. У четверти корпуса нет аннотации, и такой
    -- эмбеддинг заведомо беднее: он опирается на один заголовок. Признак
    -- новизны, посчитанный по нему, обязан идти со скидкой в coverage
    -- confidence, а не притворяться равноценным.
    text_source TEXT   NOT NULL CHECK (text_source IN ('title_abstract', 'title_only')),
    char_count  INT    NOT NULL,

    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (work_id, model)
);

CREATE INDEX work_embedding_model_idx ON work_embedding (model);

-- Векторный индекс намеренно не создаётся: на сотнях строк последовательный
-- перебор быстрее ivfflat, а сам индекс на маленькой таблице ухудшает
-- точность поиска. Добавим вместе с широким корпусом.

COMMENT ON TABLE work_embedding IS
    'Эмбеддинги работ. Модель входит в первичный ключ: векторы разных моделей живут рядом, но никогда не сравниваются между собой.';

INSERT INTO schema_migrations (version) VALUES ('004_embeddings');
