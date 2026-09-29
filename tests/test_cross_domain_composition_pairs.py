"""Frozen transfer-review packet remains label-free and non-duplicative."""

import json

from scripts.build_cross_domain_composition_pairs import ROOT, SOURCES, make_packet


def test_cross_domain_pair_packet_structure() -> None:
    sources = {domain: json.loads((ROOT / relative).read_text(encoding="utf-8"))
               for domain, (relative, _) in SOURCES.items()}
    packet = make_packet(sources)
    assert packet["version"] == "cross-domain-composition-pairs-v2"
    assert len(packet["cases"]) == 44
    assert sum(case["partition"] == "development" for case in packet["cases"]) == 24
    assert sum(case["partition"] == "holdout" for case in packet["cases"]) == 20
    assert len({case["case_id"] for case in packet["cases"]}) == 44
    assert len({(case["domain"], case["card_composition_sha256"],
                 tuple(sorted((case["paper_a"]["work_id"], case["paper_b"]["work_id"]))))
                for case in packet["cases"]}) == 44
    for case in packet["cases"]:
        assert all(value is None for value in case["review"].values())
        assert case["paper_a"]["source_urls"] and case["paper_b"]["source_urls"]
        assert case["paper_a"]["work_id"] != case["paper_b"]["work_id"]
