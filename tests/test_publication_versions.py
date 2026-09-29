from saia.publication_versions import find_version_families


ABSTRACT = ("This review studies edge processors, compares neuromorphic and "
            "processing in memory designs, chip area, energy efficiency, "
            "applications, programming frameworks, model compression, and "
            "fabrication technology. " * 3)


def record(identifier, doi, authors, abstract=ABSTRACT, title="Survey of edge processors",
           date="2024-07-01"):
    return {"work_id": identifier, "doi": doi, "authors": authors,
            "abstract": abstract, "title": title, "published_at": date}


def test_versioned_preprints_and_journal_are_related_but_not_deleted():
    authors = ["Shahanur Alam", "Chris Yakopcic", "Qing Wu"]
    values = [
        record(1, "10.20944/preprints202407.0025.v1", authors),
        record(2, "10.20944/preprints202407.0025.v2", authors,
               date="2024-07-10"),
        record(3, "10.3390/electronics13152988", authors,
               date="2024-07-29"),
    ]
    result = find_version_families(values)
    assert result["families"] == [{"work_ids": [1, 2, 3]}]
    assert result["records_removed"] == 0
    assert len(result["links"]) == 3


def test_same_title_different_journal_dois_remain_distinct():
    authors = ["Alice Smith", "Bob Jones", "Carol Brown"]
    values = [record(1, "10.1234/first", authors),
              record(2, "10.5678/second", authors)]
    assert find_version_families(values)["families"] == []


def test_preprint_journal_needs_strong_authors_and_abstract():
    values = [record(1, "10.20944/preprints202407.0025.v1",
                     ["Alice Smith", "Bob Jones", "Carol Brown"]),
              record(2, "10.3390/electronics13152988",
                     ["Alice Smith", "Bob Jones", "David Young"]),
              record(3, "10.3390/other", [], date="2024-07-25")]
    assert find_version_families(values)["families"] == []


def test_same_preprint_stem_different_title_does_not_link():
    authors = ["Alice Smith", "Bob Jones"]
    values = [record(1, "10.20944/preprints202407.0025.v1", authors),
              record(2, "10.20944/preprints202407.0025.v2", authors,
                     title="A different study")]
    assert find_version_families(values)["families"] == []


def test_figshare_versions_and_frontiers_abstract_alias_are_candidate_families():
    authors = ["Alice Smith", "Bob Jones", "Carol Brown"]
    rows = [
        record(1, "10.6084/m9.figshare.c.8497344.v1", authors),
        record(2, "10.6084/m9.figshare.c.8497344", authors),
        record(3, "10.3389/fsufs.2026.1692819/abstract", authors,
               title="Different paper"),
        record(4, "10.3389/fsufs.2026.1692819", authors,
               title="Different paper"),
    ]
    result = find_version_families(rows)
    assert result["policy_version"] == "publication-version-family-candidates-v2"
    assert result["families"] == [{"work_ids": [1, 2]}, {"work_ids": [3, 4]}]
    assert result["records_removed"] == 0
    assert {link["reason"] for link in result["links"]} == {
        "figshare_version_and_concept_doi_same_title_date_and_authors",
        "frontiers_abstract_page_doi_alias_same_title_date_and_authors",
    }


def test_explicit_doi_alias_does_not_override_missing_or_conflicting_authors():
    a = record(1, "10.6084/m9.figshare.30715610.v1",
               ["Alice Smith", "Bob Jones"])
    b = record(2, "10.6084/m9.figshare.30715610",
               ["Alice Smith", "Carol Brown"])
    assert find_version_families([a, b])["families"] == []
    b["authors"] = []
    assert find_version_families([a, b])["families"] == []
    b["authors"] = a["authors"]
    b["title"] = "Different science"
    assert find_version_families([a, b])["families"] == []


def test_consecutive_zenodo_dois_are_not_assumed_identical():
    authors = ["Alice Smith", "Bob Jones"]
    a = record(1, "10.5281/zenodo.20765535", authors)
    b = record(2, "10.5281/zenodo.20765534", authors)
    assert find_version_families([a, b])["families"] == []
