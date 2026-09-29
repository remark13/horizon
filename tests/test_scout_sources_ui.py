from html.parser import HTMLParser

from saia.scout_web import SCOUT_WEB_HTML


class SourceSectionParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.context_sections = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'details':
            self.stack.append(attrs)
        if (attrs.get('id') or '').startswith('context-') and tag == 'div':
            parent = self.stack[-1] if self.stack else {}
            if 'source-content-group' in parent.get('class', '').split():
                self.context_sections[attrs['id']] = parent

    def handle_endtag(self, tag):
        if tag == 'details' and self.stack:
            self.stack.pop()


def test_all_material_sections_are_separate_collapsed_groups():
    parser = SourceSectionParser()
    template = SCOUT_WEB_HTML.split("openDrawer('Карточка темы',`", 1)[1].split('`);', 1)[0]
    parser.feed(template)
    expected = {'context-commercial', 'context-patents', 'context-funding',
                'context-science', 'context-software', 'context-clinical', 'context-investment'}
    assert set(parser.context_sections) == expected
    assert all('open' not in attrs for attrs in parser.context_sections.values())
    assert 'class="block source-content-group" id="card-science"><summary>' in SCOUT_WEB_HTML
    assert 'id="card-sources-collapse"' in SCOUT_WEB_HTML


def test_source_registry_starts_compact_and_can_collapse_without_changing_selection():
    html = SCOUT_WEB_HTML
    assert "sourceGroupOpen(key,false)" in html
    assert "sourceGroupOpen(key,group!=='commercial')" not in html
    assert 'id="sources-collapse"' in html
    assert "saia-source-groups-v2" in html
    handler = html.split('function collapseSourceGroups(){', 1)[1].split('\n', 1)[0]
    assert 'group.open=false' in handler and 'saveSourceGroupState()' in handler
    assert 'selectedContextSources=' not in handler and 'saveContextSources()' not in handler


def test_publications_navigation_opens_collapsed_section_and_keeps_lazy_loading():
    html = SCOUT_WEB_HTML
    assert 'if(section instanceof HTMLDetailsElement)section.open=true' in html
    assert "anchor.id==='open-card-analysis'" in html
    assert "$('all-publications').ontoggle=" in html
    assert "loadCandidatePublications(c)" in html


def test_export_preserves_original_server_bytes_not_browser_float_reencoding():
    handler = SCOUT_WEB_HTML.split("$('export-results').onclick=", 1)[1].split("\n", 1)[0]
    assert "response.blob()" in handler and "response.ok" in handler
    assert "JSON.stringify" not in handler and "contextCache.values()" not in handler
    assert "document.body.appendChild(a)" in handler and "a.remove()" in handler


def test_new_source_views_and_context_are_on_working_scout_without_core_change():
    html = SCOUT_WEB_HTML
    for item in ('id="sources-view"', 'id="source-catalog"', 'id="radar-view"',
                 'id="context-commercial"', 'id="context-patents"', 'id="context-funding"',
                 'id="context-software"', 'id="context-clinical"'):
        assert item in html
    assert '/source-catalog' in html
    assert '/source-context?' in html
    assert 'packet.binding?.composition_sha256!==c.composition_sha256' in html
    assert 'В полученных ответах пока нет подходящих материалов.' in html
    assert 'Доступна небольшая публичная выборка Dealroom.' in html
    assert 'id="context-investment"' in html
    assert 'не является экспертной валидацией' in html


def test_context_does_not_replace_analyst_link_or_date_meaning():
    html = SCOUT_WEB_HTML
    assert 'Обнаружено в СМИ' in html
    assert 'Начало проекта' in html
    assert 'Привязать к сигналу' in html
    assert 'Найдено автоматически · связь не проверена' in html
    assert "$('source-context-status')===status" in html
    assert 'MAX_RESPONSE_BYTES' not in html
    assert "r.status==='not_collected'||r.cache_fresh===false" in html


def test_grouped_materials_keep_all_record_actions_and_do_not_silently_drop_urls():
    html = SCOUT_WEB_HTML
    assert 'packet.material_grouping' in html
    assert 'Другие записи и основание объединения' in html
    assert 'Повторные записи объединены:' in html
    assert 'materialHtml(m,nextIndex())' in html
    assert 'Идентификаторы или типы в источниках противоречат друг другу' in html
    assert 'не независимые подтверждения сигнала' in html
    assert 'Даты и суммы каждой записи сохранены отдельно' in html
    assert 'if(seen.has(m.url))return false' not in html
    assert 'data-observation=' in html and 'data-record-url=' in html


def test_source_settings_and_radar_are_not_fake_forecasts():
    assert 'selectedContextSources.length>=8' in SCOUT_WEB_HTML
    assert 'selectedContextSources.length===1' in SCOUT_WEB_HTML
    assert 'Расположение не является прогнозом срока внедрения или размера рынка.' in SCOUT_WEB_HTML
    assert 'const colors={growth_observed' in SCOUT_WEB_HTML
    assert 'forecast_probability' not in SCOUT_WEB_HTML


def test_date_formatter_accepts_timestamp_and_year_without_crashing():
    assert 'const day=raw.slice(0,10)' in SCOUT_WEB_HTML
    assert "return raw+' г.'" in SCOUT_WEB_HTML
    assert 'Number.isFinite(parsed.getTime())' in SCOUT_WEB_HTML


def test_unavailable_sources_are_not_reported_as_empty_or_successful_collection():
    assert 'Материалы не получены: источники пока недоступны или требуют доступа.' in SCOUT_WEB_HTML
    assert 'Отсутствие ответа не означает отсутствия материалов.' in SCOUT_WEB_HTML
    assert 'Ответы ещё не получены' in SCOUT_WEB_HTML
    assert 'Сбор завершён' not in SCOUT_WEB_HTML
    assert 'Это текущий контекст, не доказательство' in SCOUT_WEB_HTML


def test_source_feedback_stays_visible_on_sources_view_and_preserves_authorship():
    assert "sourceSelectionMessage('Можно выбрать не более 8 источников" in SCOUT_WEB_HTML
    assert "line=$('sources-selection')" in SCOUT_WEB_HTML
    assert 'material.author' in SCOUT_WEB_HTML
    assert 's.regional_coverage_ru' in SCOUT_WEB_HTML
    assert 'message.textContent=unavailableMessage' in SCOUT_WEB_HTML
    assert 'Машинный черновик: термины и выводы могут быть неточными.' in SCOUT_WEB_HTML


def test_scientific_passport_does_not_invent_language_or_peer_review():
    assert 'Паспорт публикации' in SCOUT_WEB_HTML
    assert 'не указан в сохранённой карточке' in SCOUT_WEB_HTML
    assert 'Первичность исследования и рецензирование отдельно не подтверждены.' in SCOUT_WEB_HTML
    assert 'openalex_record_types:w.record_type?[w.record_type]:[]' in SCOUT_WEB_HTML


def test_search_wait_deadline_is_shared_between_collection_and_analysis():
    assert 'const searchWaitLimitMs=20*60*1000' in SCOUT_WEB_HTML
    assert "pollJob(pending.job_id,'collect',pending.deadline_at)" in SCOUT_WEB_HTML
    assert "pollJob(pending.job_id,'analyze',pending.deadline_at)" in SCOUT_WEB_HTML
    assert SCOUT_WEB_HTML.count('deadline_at:pending.deadline_at') == 2
    assert 'Результат ещё не готов за 20 минут. Задача сохранена' in SCOUT_WEB_HTML
    assert 'signal:controller.signal' in SCOUT_WEB_HTML
    assert 'Задача сохранена' in SCOUT_WEB_HTML


def test_narrow_viewport_keeps_source_and_expert_navigation_accessible():
    assert '@media(max-width:900px){.side{display:flex;flex-direction:row' in SCOUT_WEB_HTML
    assert "$('drawer').scrollTop=0" in SCOUT_WEB_HTML
    assert 'closeDrawer();window.scrollTo(0,0)' in SCOUT_WEB_HTML


def test_additional_funding_and_artifact_dates_do_not_imply_private_investment_or_adoption():
    assert "software:'Данные, модели или ПО'" in SCOUT_WEB_HTML
    assert "grant_award:'Грант выдан'" in SCOUT_WEB_HTML
    assert "resource_publication:'Опубликован набор данных или ПО'" in SCOUT_WEB_HTML
    assert 'Источник указал только год. Точная дата неизвестна.' in SCOUT_WEB_HTML
    assert 'Начало работ запланировано' in SCOUT_WEB_HTML
    assert 'Это не выплаченные расходы и не частные инвестиции.' in SCOUT_WEB_HTML
    assert 'Это не показатель внедрения.' in SCOUT_WEB_HTML
