import subprocess
import sys

from saia import methodology


def test_cli_default_is_exactly_the_same_passport_as_api_and_core():
    result = subprocess.run([sys.executable, '-m', 'saia.methodology'],
                            check=True, capture_output=True, text=True)
    assert result.stdout.strip() == methodology.describe(methodology.load_default()).strip()


def test_historical_passport_remains_explicitly_selectable():
    path = methodology.DEFAULT_CONFIG_PATH.with_name('methodology.v0.1.yaml')
    result = subprocess.run([sys.executable, '-m', 'saia.methodology', '--config', str(path)],
                            check=True, capture_output=True, text=True)
    assert result.stdout.strip() == methodology.describe(methodology.load(path)).strip()
