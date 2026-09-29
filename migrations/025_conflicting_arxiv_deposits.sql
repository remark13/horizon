-- Preserve unproven identity between different arXiv deposits.
-- Existing decisions/results remain untouched.
ALTER TABLE dedup_decision DROP CONSTRAINT dedup_decision_rule_check;
ALTER TABLE dedup_decision ADD CONSTRAINT dedup_decision_rule_check
    CHECK (rule IN ('doi_match', 'arxiv_id_match', 'title_author_match',
                   'same_work_two_venues', 'blocked_different_doi',
                   'blocked_conflicting_arxiv_ids', 'new_work'));
INSERT INTO schema_migrations (version) VALUES ('025_conflicting_arxiv_deposits');
