from saia.embed import reuse_exact_text_embeddings


class Cursor:
    def __init__(self, prior):
        self.prior = prior
        self.inserted = []
        self.rowcount = 0

    def execute(self, sql, params):
        if sql.startswith("SELECT"):
            self.rowcount = len(self.prior)
            self.lookup_params = params
        else:
            self.inserted.append(params)
            self.rowcount = 1

    def fetchall(self):
        return self.prior


def test_reuse_requires_exact_abstract_even_when_title_key_matches():
    cur = Cursor([("Same title", "Old abstract", 2, "[1,0]",
                   "title_abstract", len("Same title\n\nOld abstract"))])
    reused, dim = reuse_exact_text_embeddings(
        cur, [(91, "same title", "Same title", "Different abstract")],
        "test-model", None,
    )
    assert reused == set() and dim is None
    assert cur.inserted == []


def test_reuse_requires_consistent_text_source_and_length():
    cur = Cursor([("Same title", "Old abstract", 2, "[1,0]",
                   "title_only", len("Same title"))])
    reused, _ = reuse_exact_text_embeddings(
        cur, [(91, "same title", "Same title", "Old abstract")],
        "test-model", None,
    )
    assert reused == set() and cur.inserted == []


def test_reuse_accepts_identical_text_with_valid_vector():
    cur = Cursor([("Same title", "Old abstract", 2, "[1,0]",
                   "title_abstract", len("Same title\n\nOld abstract"))])
    reused, dim = reuse_exact_text_embeddings(
        cur, [(91, "same title", "Same title", "Old abstract")],
        "test-model", None,
    )
    assert reused == {91} and dim == 2
    assert cur.inserted[0][0:3] == (91, "test-model", 2)
