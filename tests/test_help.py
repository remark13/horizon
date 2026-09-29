"""Help is local, grounded, linked and usable without a database or model."""
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

from fastapi.testclient import TestClient
import pytest

from saia.api import app
from saia.help_content import HELP_ARTICLES, HELP_VERSION
from saia.help_web import HELP_SCRIPT, HELP_WIDGET_SCRIPT, attach_help_widget, render_help
from saia.public_signals_web import PUBLIC_SIGNALS_HTML
from saia.scout_web import SCOUT_WEB_HTML


class Markup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids, self.topics, self.frames = [], [], []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if 'id' in values:
            self.ids.append(values['id'])
        for key in ('data-help-topic', 'data-horizon-help'):
            if key in values:
                self.topics.append(values[key])
        if tag == 'iframe':
            self.frames.append(values)


def test_help_catalog_covers_actual_product_and_all_links_resolve():
    required = {'quick-start', 'search', 'results', 'signal-card', 'scores', 'charts', 'dynamics',
                'sources', 'public-signals', 'expertise', 'radar', 'pestle', 'models',
                'exports', 'troubleshooting', 'glossary'}
    ids = [a['id'] for a in HELP_ARTICLES]
    assert required <= set(ids)
    assert len(ids) == len(set(ids))
    assert HELP_VERSION == app.version
    for article in HELP_ARTICLES:
        assert re.fullmatch('[a-z][a-z-]*', article['id'])
        assert len(article['body']) > 200
        assert article['keywords'] and article['summary']
        assert '<script' not in article['body'].lower()
    for html in (render_help(), SCOUT_WEB_HTML, PUBLIC_SIGNALS_HTML):
        markup = Markup()
        markup.feed(html)
        assert len(markup.ids) == len(set(markup.ids))
        assert set(markup.topics) <= set(ids)


def test_help_endpoint_is_available_without_calling_database(monkeypatch):
    from saia import db
    def forbidden():
        raise AssertionError('Help must not depend on database availability')
    monkeypatch.setattr(db, 'connect', forbidden)
    for path in ('/help', '/help?embed=1'):
        response = TestClient(app).get(path)
        assert response.status_code == 200
        assert 'Справка Horizon' in response.text
        assert 'id="help-search"' in response.text
        assert 'id="help-article-quick-start"' in response.text
        assert 'frame-ancestors \'self\'' in response.headers['content-security-policy']
        assert response.headers['cache-control'] == 'no-store, max-age=0'
        assert '<script src="https://' not in response.text


def test_help_widget_does_not_replace_page_or_duplicate_itself():
    raw = '<html><head></head><body><input value="preserve my query"></body></html>'
    result = attach_help_widget(raw)
    assert attach_help_widget(result) == result
    assert '<input value="preserve my query">' in result
    markup = Markup()
    markup.feed(result)
    assert markup.ids.count('horizon-help-dialog') == 1
    assert markup.frames[0]['title'] == 'Справка по работе с Horizon'
    assert 'src' not in markup.frames[0]  # no request until the user asks for help
    assert 'dialog.showModal()' in result
    assert 'event.stopImmediatePropagation()' in result
    assert 'event.source!==frame.contentWindow' in result
    assert 'event.origin!==location.origin' in result
    assert 'ids.has(event.data.topic)' in result


def test_skip_link_does_not_reset_selected_article_or_search():
    html = render_help()
    assert 'id="help-main" tabindex="-1"' in html
    assert "if(event.target.closest('.help-skip')){event.preventDefault();$('help-main').focus();return}" in html


def test_help_search_and_widget_javascript():
    node = os.environ.get('HORIZON_NODE_BINARY') or shutil.which('node')
    if not node:
        pytest.skip('Node.js is needed for executable help UI checks')
    search = 'function normalizeHelpText' + HELP_SCRIPT.split('function normalizeHelpText', 1)[1].split('const embedded=', 1)[0]
    result = subprocess.run([node, str(Path(__file__).with_name('help_ui.cjs'))],
                            input=json.dumps({'search': search, 'widget': HELP_WIDGET_SCRIPT.replace('__HELP_IDS__', json.dumps([a['id'] for a in HELP_ARTICLES]))}),
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
