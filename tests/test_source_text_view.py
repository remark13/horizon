import pytest

from saia.source_text_view import VERSION, source_text_view
from saia.cross_cluster_diffusion import DiffusionPolicy, text_phrases
from saia.scientific_aliases import document_occurrences


def test_opt_in_escaped_linebreak_preserves_offsets_unicode_and_raw_source():
    text = "Граф: generative\\n adversarial networks"
    view = source_text_view(text, VERSION)
    assert len(view) == len(text)
    assert "generative   adversarial" in view
    assert "\\n" in text
    assert "generative adversarial networks" in text_phrases(text, DiffusionPolicy(), text_mode=VERSION)
    assert "generative adversarial networks" not in text_phrases(text, DiffusionPolicy())


def test_latex_attached_escapes_and_double_escaping_are_not_guessed():
    text = r"\nabla \rho \text{x} alpha\nbeta alpha\\n beta"
    assert source_text_view(text, VERSION) == text
    native = "русский текст\nwith native whitespace"
    assert source_text_view(native, VERSION) == native


def test_alias_quotes_reference_original_escaped_source_not_the_clean_view():
    title = r"generative\n adversarial networks (GAN)"
    records = document_occurrences(title, "", text_mode=VERSION)["generative adversarial network"]
    assert any(record["basis"] == "source_defined_acronym" for record in records)
    for record in records:
        assert title[record["start"]:record["end"]] == record["quote"]
        if "definition" in record:
            definition = record["definition"]
            assert title[definition["start"]:definition["end"]] == definition["quote"]


def test_unsupported_or_nonstrings_fail():
    with pytest.raises(ValueError, match="Unsupported"):
        source_text_view("text", "decode-everything")
    with pytest.raises(ValueError, match="string"):
        source_text_view(None)
