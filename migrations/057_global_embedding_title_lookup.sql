-- Locate prior vectors by a short indexed title hash, then verify the complete
-- title and abstract before reuse. The hash is a lookup accelerator, never an
-- identity or deduplication rule.
CREATE INDEX work_embedding_reuse_title_hash_idx ON work (md5(title_key));

INSERT INTO schema_migrations (version) VALUES ('057_global_embedding_title_lookup');
