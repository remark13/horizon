from saia.candidates import choose_topic_label


def test_broad_query_seed_cannot_replace_discovered_cluster_label():
    assert choose_topic_label(
        [("machine learning", 20), ("gradient descent", 5)],
        ["machine learning", "gradient descent"],
        "private / privacy / differentially / differentially private",
    ) == (
        "Differentially private — privacy",
        "readable_cluster_terms_seed_context_only",
    )


def test_specific_non_seed_term_can_label_topic():
    assert choose_topic_label(
        [("machine learning", 20), ("secure aggregation", 8)],
        ["machine learning"], "privacy / private",
    ) == ("Secure aggregation", "automatic_non_seed_term_from_latest_evidence")


def test_seed_context_is_kept_only_with_specific_cluster_details():
    assert choose_topic_label(
        [("tissue engineering", 10)], ["tissue engineering"],
        "tissue / scaffolds / engineering / tissue engineering / surface / cell / adhesion",
    ) == (
        "Tissue engineering — scaffolds, surface and adhesion",
        "readable_cluster_terms_seed_context_only",
    )


def test_repeated_plural_terms_are_deduplicated_in_readable_label():
    assert choose_topic_label(
        [], ["tissue engineering"],
        "properties / network / networks / hydrogel / flow / viscoelastic",
    ) == (
        "Network, hydrogel, flow and viscoelastic",
        "readable_cluster_terms_seed_context_only",
    )


def test_statistical_confidence_interval_is_not_a_topic_name():
    assert choose_topic_label(
        [], ["multimodal artificial intelligence"], "95 ci / multimodal / clinical",
    ) == ("Multimodal and clinical", "readable_cluster_terms_seed_context_only")


def test_confidence_interval_drops_duplicate_ci_token():
    assert choose_topic_label(
        [], ["multimodal artificial intelligence"],
        "95 ci / multimodal / clinical / ci",
    ) == ("Multimodal and clinical", "readable_cluster_terms_seed_context_only")


def test_language_model_label_keeps_its_noun():
    assert choose_topic_label(
        [], ["foundation model"], "large language / models / language",
    ) == ("Large language models", "readable_cluster_terms_seed_context_only")


def test_russian_query_inflections_do_not_become_fake_subtopic():
    assert choose_topic_label(
        [], ["Квантовые коммуникации"], "на / квантовые / данных / квантовых",
    ) == (
        "Тема по запросу «Квантовые коммуникации»",
        "generic_query_context_no_specific_cluster_terms",
    )


def test_russian_terms_use_russian_conjunction_when_specific():
    assert choose_topic_label([], [], "лазер / очистка / спектроскопия") == (
        "Лазер, очистка и спектроскопия",
        "readable_cluster_terms_seed_context_only",
    )
