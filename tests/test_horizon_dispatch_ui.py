"""Execute the shipped dispatch JavaScript, not a reimplementation of it."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from saia.scout_web import SCOUT_WEB_HTML


def test_horizon_brand_and_expertise_actions_are_in_the_shipped_page():
    assert '<title>Horizon' in SCOUT_WEB_HTML
    assert 'brand-product">Horizon<' in SCOUT_WEB_HTML
    assert 'id="nav-experts">Экспертиза<' in SCOUT_WEB_HTML
    assert 'Экспертная очередь' not in SCOUT_WEB_HTML
    assert 'Найденные Horizon' in SCOUT_WEB_HTML
    for action in ('data-dispatch-candidate', 'data-dispatch-public',
                   'id="card-dispatch"', 'id="public-dispatch"', 'id="dispatch-open"'):
        assert action in SCOUT_WEB_HTML
    assert "$('dispatch-open').onclick=()=>dispatchForm()" in SCOUT_WEB_HTML
    assert "async function expertView(){\n  activateScoutView('experts');" in SCOUT_WEB_HTML


def test_dispatch_javascript_behaviour():
    node = os.environ.get('HORIZON_NODE_BINARY') or shutil.which('node')
    if not node:
        pytest.skip('Node.js is needed for executable UI regression tests')
    script = SCOUT_WEB_HTML.split('function dispatchCandidate(', 1)[1].split('async function expertView()', 1)[0]
    script = 'function dispatchCandidate(' + script
    result = subprocess.run(
        [node, str(Path(__file__).with_name('horizon_dispatch_ui.cjs'))],
        input=json.dumps({'script': script}), text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
