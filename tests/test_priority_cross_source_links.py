from saia.priority_cross_source_links import candidates, title_key


def _arxiv(**overrides):
    return {"arxiv_id": "2501.00001", "title_current": "A matched research title",
            "doi_current": "10.1000/match", "authors_current": "Ada Scientist",
            "first_submission_date": "2025-01-01"} | overrides


def _openalex(**overrides):
    return {"openalex_id": "W123", "openalex_url": "https://openalex.org/W123",
            "title": "A matched research title", "doi": "10.1000/match",
            "authors": ["Ada Scientist"], "publication_date": "2025-01-02",
            "source_mission_id": "pilot"} | overrides


def test_doi_and_title_are_a_strong_candidate_but_not_an_automatic_merge():
    result = candidates([_arxiv()], [_openalex()])
    assert len(result) == 1
    assert result[0]["status"] == "strong_doi_candidate"
    assert result[0]["automatic_merge"] is False


def test_title_alone_cannot_merge_and_conflicting_dois_are_flagged():
    no_author = candidates([_arxiv(authors_current="")],
                           [_openalex(doi=None, authors=[])])
    assert no_author[0]["status"] == "title_only_review"
    conflict = candidates([_arxiv()], [_openalex(doi="10.1000/different")])
    assert conflict[0]["status"] == "doi_conflict_review"
    assert title_key("Vision-Language-Action") == title_key("Vision Language Action")
