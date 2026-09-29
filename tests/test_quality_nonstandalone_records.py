import pytest

from saia import methodology
from saia.quality import POLICY_VERSION, decide


def decision(types, *, arxiv=False, mode="annotate"):
    policy = dict(methodology.load_default().raw["publication_quality"])
    policy["non_standalone_mode"] = mode
    return decide(
        title="Precision fermentation measurement", abstract="Precision fermentation measurement",
        publication_year=2026, date_is_imprecise=False,
        terms=["precision fermentation"], sources={"openalex", "arxiv"} if arxiv else {"openalex"},
        openalex_payloads=[{"type": kind} for kind in types],
        arxiv_payloads=[{"id": "2601.00001", "created": "2026-01-01"}] if arxiv else [],
        as_of_date="2026-09-01", effective_date="2026-01-01",
        policy=policy,
    )


def test_supporting_objects_do_not_inflate_publication_count():
    assert POLICY_VERSION == "publication-quality-v10-supporting-type-diagnostic"
    for kind in ("peer-review", "erratum", "supplementary-materials"):
        result = decision([kind])
        assert result.decision == "include"
        assert result.flags["non_standalone_publication_object"] is True
        assert result.flags["openalex_record_types"] == [kind]
        assert decision([kind], mode="exclude").decision == "exclude"


def test_article_or_arxiv_text_prevents_whole_canonical_work_exclusion():
    assert decision(["supplementary-materials", "article"]).decision == "include"
    assert decision(["supplementary-materials"], arxiv=True).decision == "include"
    assert decision(["article"]).decision == "include"
    assert decision(["other"]).decision == "include"
    assert decision([None]).decision == "include"
    assert decision(["article"], mode="exclude").decision == "include"


def test_unknown_supporting_mode_is_rejected():
    with pytest.raises(ValueError, match="non_standalone_mode"):
        decision(["supplementary-materials"], mode="silently_merge")
