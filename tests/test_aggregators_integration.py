"""Real PostgreSQL source guards and export retrieval; isolated test DB only."""
import os
import uuid
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from psycopg.types.json import Jsonb

from saia import db, external_evidence_store as store, source_context
from saia.aggregator_evidence import AggregatorQuery, unavailable
from saia.hybrid import digest

pytestmark = pytest.mark.skipif("test" not in os.environ.get("SAIA_DATABASE_URL", "").rsplit("/", 1)[-1], reason="isolated test database required")


@pytest.mark.parametrize("source", ["dealroom_marketmaps", "dealroom_public_rounds", "event_registry", "mediacloud_news", "lens_patents", "google_news_rss"])
def test_real_database_accepts_each_typed_aggregator_but_rejects_role_substitution(source):
    today = datetime.now(timezone.utc).date()
    report = unavailable(AggregatorQuery("integration:" + str(uuid.uuid4()), source, "robotics", today - timedelta(days=29), today, today), "credentials_required", datetime.now(timezone.utc).isoformat())
    first = store.record(report)
    second = store.record(report)
    assert second["observation_id"] == first["observation_id"]
    assert store.read(first["observation_id"])["payload"]["observations"] is None
    bad = {**report, "role": "research_funding_only"}
    bad.pop("report_payload_sha256")
    bad["report_payload_sha256"] = digest(bad)
    with pytest.raises(psycopg.errors.RaiseException):
        with db.connect() as conn:
            conn.execute("INSERT INTO external_evidence_observation(observation_id,source,role,topic_id,status,report_payload_sha256,payload) VALUES(%s,%s,%s,%s,%s,%s,%s)",
                         (str(uuid.uuid4()), source, bad["role"], bad["topic_id"], bad["status"], bad["report_payload_sha256"], Jsonb(bad)))


def test_real_saved_export_finds_prior_query_only_for_exact_composition():
    today = datetime.now(timezone.utc).date()
    mission = "export-integration:" + str(uuid.uuid4())
    card = {"label": "Robot technology", "composition_sha256": "a" * 64}
    scope = source_context.binding(mission, 12, 7, card, "old robotics query", today - timedelta(days=1))
    report = unavailable(AggregatorQuery(source_context.topic_key(scope), "event_registry", "old robotics query", today - timedelta(days=29), today, today), "credentials_required", datetime.now(timezone.utc).isoformat())
    report.pop("report_payload_sha256")
    report["candidate_context_binding"] = scope
    report["report_payload_sha256"] = digest(report)
    store.record(report)
    result = source_context.read_saved_all(mission, 12, 7, card)
    assert result["saved_query_bindings"] == [scope]
    assert result["reports"][0]["observed_count"] is None
    assert source_context.read_saved_all(mission, 12, 7, {**card, "composition_sha256": "b" * 64})["reports"] == []
