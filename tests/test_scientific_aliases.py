import pytest

from saia.scientific_aliases import document_occurrences, regular_plural_key
from saia.cross_cluster_diffusion import GENERIC, STOP


def extract(title, abstract=""):
    return document_occurrences(title, abstract, stopwords=STOP, generic=GENERIC)


@pytest.mark.parametrize("surface,canonical", [
    ("neural networks", "neural network"), ("sequential policies", "sequential policy"),
    ("prediction classes", "prediction class"), ("time series", "time series"),
    ("statistical analysis", "statistical analysis"), ("quantum physics", "quantum physics"),
    ("measurement bias", "measurement bias"), ("surface gas", "surface gas")])
def test_regular_final_plural_is_limited(surface, canonical):
    assert regular_plural_key(surface) == canonical


def test_exact_source_spans_survive_plural_and_hyphen_normalization():
    title = "Graph-convolutional networks for molecules"
    spans = extract(title)["graph convolutional network"]
    assert spans[0]["basis"] == "regular_plural"
    assert spans[0]["quote"] == "Graph-convolutional networks"
    assert title[spans[0]["start"]:spans[0]["end"]] == spans[0]["quote"]


def test_title_acronym_uses_its_own_abstract_definition_with_two_source_spans():
    title = "GCNs for proteins"
    abstract = "We use graph convolutional networks (GCN) and test GCNs."
    spans = extract(title, abstract)["graph convolutional network"]
    expanded = [span for span in spans if span["basis"] == "source_defined_acronym"]
    assert any(span["field"] == "title" and span["quote"] == "GCNs" for span in expanded)
    for span in expanded:
        source = title if span["field"] == "title" else abstract
        definition = span["definition"]
        assert source[span["start"]:span["end"]] == span["quote"]
        assert abstract[definition["start"]:definition["end"]] == definition["quote"]


def test_definition_matching_handles_hyphens_not_sentence_crossings():
    spans = extract("LSTM", "long short-term memory (LSTM)")["long short term memory"]
    assert any(span["basis"] == "source_defined_acronym" for span in spans)
    invalid = extract("AB", "alpha. beta (AB)")
    assert not any(span["basis"] == "source_defined_acronym"
                   for records in invalid.values() for span in records)


def test_ambiguous_acronym_is_not_expanded_and_literal_expansions_stay_separate():
    found = extract("GCN applications", "graph convolutional network (GCN); genetic cell network (GCN)")
    assert "graph convolutional network" in found and "genetic cell network" in found
    assert not any(span["basis"] == "source_defined_acronym"
                   for records in found.values() for span in records)


def test_acronym_resolution_never_leaks_between_documents_or_from_later_definition():
    assert "graph convolutional network" not in extract("GCN models")
    extract("graph convolutional network (GCN)")
    assert "graph convolutional network" not in extract("GCN models")


def test_no_semantic_or_derivational_aliases_and_no_bridge_over_grammatical_words():
    assert regular_plural_key("generative adversarial nets") != regular_plural_key("generative adversarial networks")
    assert regular_plural_key("graph convolutions") != regular_plural_key("graph convolutional")
    assert "protein convolution" not in extract("protein and convolution")
    assert "protein convolution" not in extract("protein. convolution")


def test_lowercase_undefined_or_mismatched_acronyms_not_inferred():
    for title, abstract in [("gcn models", ""), ("GCN models", "graph neural network (GCN)")]:
        assert not any(span["basis"] == "source_defined_acronym"
                       for records in extract(title, abstract).values() for span in records)


def test_invalid_phrase_bounds_fail():
    with pytest.raises(ValueError, match="Phrase sizes"):
        document_occurrences("text", "", min_tokens=True)

