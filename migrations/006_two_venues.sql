-- SAIA 006 — различение «одна работа в двух местах» от «разные объекты».
--
-- Правило blocked_different_doi разводило по разным работам всё, у чего
-- разные издательские DOI. На двух случаях это верно, на третьем нет:
--
--   тома ECML PKDD 2017   10.1007/978-3-319-71246-8 и ...71249-9
--   депозиты Zenodo       10.5281/zenodo.17665835 и ...17667932
--   одна статья в IEEE и ACM  10.1109/cnsm.2016.7818394 и 10.5555/3375069.3375070
--
-- Состав авторов не различает: у двух томов ECML редакторы совпадают
-- полностью, у четырёх депозитов Zenodo в поле автора стоит одна и та же
-- строка. Различает ПРЕФИКС DOI — регистрант. Один издатель не публикует
-- одну работу дважды под своими разными DOI, а два разных издателя на одну
-- конференционную статью — обычное дело.
--
-- Отсюда правило: полное совпадение заголовка и состава авторов ПЛЮС
-- непересекающиеся префиксы DOI означают одну работу, изданную дважды.

ALTER TABLE dedup_decision DROP CONSTRAINT dedup_decision_rule_check;

ALTER TABLE dedup_decision ADD CONSTRAINT dedup_decision_rule_check
    CHECK (rule IN (
        'doi_match',
        'arxiv_id_match',
        'title_author_match',
        'same_work_two_venues',
        'blocked_different_doi',
        'new_work'
    ));

INSERT INTO schema_migrations (version) VALUES ('006_two_venues');
