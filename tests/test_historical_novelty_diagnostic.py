import pytest

from saia.historical_novelty_diagnostic import _pack_ids, _stable_hash


def test_stable_hash_ignores_dictionary_key_order():
    assert _stable_hash({"a": 1, "b": 2}) == _stable_hash({"b": 2, "a": 1})


def test_pack_ids_refuses_duplicates(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    path = tmp_path / "pack.parquet"
    pq.write_table(pa.table({"id": ["one", "one"]}), path)
    with pytest.raises(ValueError, match="duplicated"):
        _pack_ids(path)
