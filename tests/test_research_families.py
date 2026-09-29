from saia.research_families import audit, version_pair


ABSTRACT = "Detailed evidence for edge computing and accelerators. " * 6


def work(*, doi: str, title: str = "Survey of Deep Learning Accelerators for Edge and Emerging Computing",
         authors: list[str] | None = None, abstract: str = ABSTRACT,
         published: str = "2024-07-01") -> dict:
    return {"title": title, "abstract": abstract, "published_at": published,
            "authors": authors or ["Chris Yakopcic", "Qing Wu", "Tarek M. Taha"],
            "doi": doi, "source_ids": ["https://openalex.org/" + doi]}


def test_potential_version_pair_is_read_only_and_preserves_both_dois():
    left = work(doi="10.20944/preprint.v1")
    right = work(doi="10.3390/journal", published="2024-07-29")
    result = audit([left, right])
    assert result["candidate_pair_count"] == 1
    assert result["same_title_group_count"] == 1
    assert result["records_in_same_title_groups"] == 2
    assert result["candidate_pairs"][0]["decision"] == "possible_version_family_needs_review"
    assert result["policy"]["no_records_merged"] is True
    assert [left["doi"], right["doi"]] == ["10.20944/preprint.v1", "10.3390/journal"]


def test_title_or_author_or_abstract_disagreement_blocks_version_suggestion():
    reference = work(doi="one")
    assert version_pair(reference, work(doi="two", title="A distinct article title")) is None
    assert version_pair(reference, work(doi="two", authors=["Alice Smith", "Bob Jones"])) is None
    assert version_pair(reference, work(doi="two", abstract="Different results. " * 30)) is None
    assert version_pair(reference, work(doi="two", published="2022-01-01")) is None
