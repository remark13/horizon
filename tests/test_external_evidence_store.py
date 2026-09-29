import pytest

from saia.external_evidence_store import verify
from saia.hybrid import digest


@pytest.mark.parametrize("source,role", [
    ("nih_reporter", "research_funding_only"),
    ("deps_dev", "software_diffusion_only"),
    ("semantic_scholar", "bibliographic_enrichment_only"),
    ("ukri_gtr", "research_funding_only"),
    ("eu_funding_tenders", "research_programme_only"),
    ("huggingface_hub", "ai_artifact_diffusion_only"),
    ("clinicaltrials_gov", "clinical_translation_only"),
    ("europe_pmc", "bibliographic_enrichment_only"),
    ("nasa_ntrs", "bibliographic_enrichment_only"),
    ("nsf_awards", "research_funding_only"),
    ("openaire_projects", "research_funding_only"),
    ("datacite", "research_artifact_only"),
])
def test_new_external_sources_have_explicit_non_score_roles(source, role):
    payload = {
        "source": source, "role": role, "topic_id": "agents",
        "status": "complete", "scientific_score_modified": False,
        "missing_is_zero": False,
    }
    payload["report_payload_sha256"] = digest(payload)
    assert verify(payload) is payload


def test_external_source_role_cannot_be_swapped():
    payload = {
        "source": "nih_reporter", "role": "software_diffusion_only",
        "topic_id": "agents", "status": "complete",
        "scientific_score_modified": False, "missing_is_zero": False,
    }
    payload["report_payload_sha256"] = digest(payload)
    with pytest.raises(ValueError, match="source/role"):
        verify(payload)
