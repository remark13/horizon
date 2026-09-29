"""Append-only publisher observations, validated only in an isolated test DB."""
import os
import uuid
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from psycopg.types.json import Jsonb

from saia import db, external_evidence_store as store
from saia.aggregator_evidence import AggregatorQuery, unavailable
from saia.hybrid import digest
from saia.news_publishers import publishers

pytestmark = pytest.mark.skipif("test" not in os.environ.get("SAIA_DATABASE_URL", "").rsplit("/", 1)[-1],
                               reason="isolated test database required")


def report(source):
    today = datetime.now(timezone.utc).date()
    return unavailable(AggregatorQuery("publisher-integration:" + str(uuid.uuid4()), source,
                      "robotics", today - timedelta(days=29), today, today),
                      "source_unavailable", datetime.now(timezone.utc).isoformat())


def insert(value):
    with db.connect() as conn:
        conn.execute("INSERT INTO external_evidence_observation(observation_id,source,role,topic_id,status,report_payload_sha256,payload) "
                     "VALUES(%s,%s,%s,%s,%s,%s,%s)",
                     (str(uuid.uuid4()), value["source"], value["role"], value["topic_id"], value["status"],
                      value["report_payload_sha256"], Jsonb(value)))


def test_database_allowlist_accepts_all_directory_identities_without_network():
    for source in publishers():
        value = report(source)
        saved = store.record(value)
        assert store.read(saved["observation_id"])["payload"]["observed_count"] is None
        assert store.record(value)["observation_id"] == saved["observation_id"]


@pytest.mark.parametrize("field,value", [("publisher_filter_domain", "phys-org"), ("publisher_backend", "unverified"),
     ("role", "patent_landscape_only"), ("publisher_directory_version", "wrong"),
     ("usage_scope", "commercial"), ("model_input_allowed", True), ("model_training_allowed", True),
     ("stores_full_text", True), ("stores_images", True), ("public_republication_allowed", True)])
def test_database_guards_cannot_be_bypassed_with_direct_sql(field, value):
    payload = report("publisher_news_phys_org")
    payload[field] = value
    payload.pop("report_payload_sha256")
    payload["report_payload_sha256"] = digest(payload)
    with pytest.raises(psycopg.errors.RaiseException):
        insert(payload)


def test_unregistered_publisher_and_mutating_old_observation_are_rejected():
    payload = report("publisher_news_phys_org")
    saved = store.record(payload)
    with pytest.raises(psycopg.errors.RaiseException):
        with db.connect() as conn:
            conn.execute("UPDATE external_evidence_observation SET status='complete' WHERE observation_id=%s", (saved["observation_id"],))
    payload["source"] = "publisher_news_unregistered_example"
    payload.pop("report_payload_sha256")
    payload["report_payload_sha256"] = digest(payload)
    with pytest.raises((psycopg.errors.RaiseException, psycopg.errors.CheckViolation)):
        insert(payload)
