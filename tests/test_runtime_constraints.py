from importlib import metadata

import pytest

from saia.runtime_constraints import check, parse


def test_normalized_pins_and_cpu_local_version():
    assert parse('# provenance\nPyYAML==6.0.3\ntorch==2.14.0+cpu\n') == {'pyyaml': '6.0.3', 'torch': '2.14.0+cpu'}


@pytest.mark.parametrize('text', ['', 'torch>=2', 'Torch==2\ntorch==2', 'pkg @ https://example.org', 'pkg==1; python_version>"3"'])
def test_ambiguous_or_unpinned_inputs_rejected(text):
    with pytest.raises(ValueError):
        parse(text)


def test_installed_mismatch_and_missing_are_not_silently_skipped():
    def lookup(name):
        if name == 'missing':
            raise metadata.PackageNotFoundError(name)
        return '2'
    assert check({'good': '2', 'changed': '1', 'missing': '1'}, lookup) == [
        {'package': 'changed', 'expected': '1', 'installed': '2'},
        {'package': 'missing', 'expected': '1', 'installed': None}]
