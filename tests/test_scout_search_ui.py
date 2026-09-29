from saia.scout_web import SCOUT_WEB_HTML


def test_scout_results_are_optional_expert_candidates_and_fit_narrow_desktop():
    assert 'id="results-title">Кандидаты в слабые сигналы' in SCOUT_WEB_HTML
    assert "$('results-title').textContent='Кандидаты в слабые сигналы'" in SCOUT_WEB_HTML
    assert 'id="status-summary"' in SCOUT_WEB_HTML
    assert 'counts.growth_not_confirmed' in SCOUT_WEB_HTML
    assert '@media(max-width:1280px)' in SCOUT_WEB_HTML
    assert '.table{display:block;min-width:0;width:100%}' in SCOUT_WEB_HTML
    assert 'data-label="Динамика публикаций"' in SCOUT_WEB_HTML
    assert 'Тем в результатах:' not in SCOUT_WEB_HTML
    assert 'id="results-caption"' not in SCOUT_WEB_HTML


def test_publication_block_does_not_label_all_openalex_material_as_primary_science():
    assert "Публикации и препринты" in SCOUT_WEB_HTML
    assert "Тип источника и первичность результата ещё не подтверждены." in SCOUT_WEB_HTML
    assert "e.venue_context" in SCOUT_WEB_HTML
    assert "d.context_hint==='acceptance_or_market_context'" in SCOUT_WEB_HTML
    assert "не технический метод" in SCOUT_WEB_HTML
    assert "Тип записи OpenAlex" in SCOUT_WEB_HTML
    assert "review:'Обзор'" in SCOUT_WEB_HTML
    assert "Тезис конференции" in SCOUT_WEB_HTML


def test_full_publications_are_lazy_scoped_and_composition_checked():
    assert 'id="all-publications"' in SCOUT_WEB_HTML
    assert 'Все публикации темы' in SCOUT_WEB_HTML
    assert '/publications?score_run_id=${encodeURIComponent(score)}&limit=20&offset=${offset}' in SCOUT_WEB_HTML
    assert 'packet.composition_sha256!==c.composition_sha256' in SCOUT_WEB_HTML
    assert "$('full-publication-list')!==block" in SCOUT_WEB_HTML
    assert 'packet.next_offset!==null' in SCOUT_WEB_HTML
    assert 'Ещё 20 публикаций' in SCOUT_WEB_HTML


def test_free_russian_query_is_not_blocked_when_suggestions_are_empty():
    assert "Для поиска по русскому запросу оставьте хотя бы одну тему" not in SCOUT_WEB_HTML
    assert "Поиск начнётся по вашему запросу." in SCOUT_WEB_HTML
    assert 'id="english-phrase"' in SCOUT_WEB_HTML
    assert "const extraEnglish=$('english-phrase')?.value.trim()||''" in SCOUT_WEB_HTML
    assert "[b.query,...(extraEnglish?[extraEnglish]:[])]" in SCOUT_WEB_HTML
    assert "selected_branch_ids:ids" in SCOUT_WEB_HTML


def test_selected_theme_uses_server_supplied_exact_phrases():
    assert "s.phrases_en||phraseHints" in SCOUT_WEB_HTML
    assert "const offered=new Map((p.suggestions||[])" in SCOUT_WEB_HTML
    assert "Для анализа используются выбранные темы; исходный запрос сохраняется." in SCOUT_WEB_HTML


def test_scout_search_prefers_live_openalex_to_incomplete_local_cache():
    assert "openalex_collection_mode:'live_with_cache_fallback'" in SCOUT_WEB_HTML
    assert "openalex_collection_mode:'cache_year_spread'" not in SCOUT_WEB_HTML


def test_too_broad_analysis_has_actionable_message():
    assert "Запрос охватывает слишком много публикаций" in SCOUT_WEB_HTML


def test_growth_caveat_stays_in_card_instead_of_global_technical_caption():
    assert "рост доли публикаций пока не измерен" not in SCOUT_WEB_HTML
    assert "coverage_comparable===true" in SCOUT_WEB_HTML
    assert "Рост их числа сам по себе не подтверждает тренд" in SCOUT_WEB_HTML


def test_scout_distinguishes_observed_sample_count_from_verified_share_growth():
    assert "Динамика публикаций" in SCOUT_WEB_HTML
    assert "s.coverage_comparable===true&&validNumber(s.share_slope_per_window)" in SCOUT_WEB_HTML
    assert "s.observed_sample?.count" in SCOUT_WEB_HTML
    assert "в выборке:" in SCOUT_WEB_HTML
    assert "Рост их числа сам по себе не подтверждает тренд" in SCOUT_WEB_HTML


def test_openalex_only_discovery_is_not_rejected_when_arxiv_has_no_matches():
    assert "const foundWorks=Array.isArray(done.result?.works)?done.result.works:[]" in SCOUT_WEB_HTML
    assert "if(!foundWorks.length)" in SCOUT_WEB_HTML
    assert "localMatches===0" not in SCOUT_WEB_HTML


def test_no_candidate_topics_have_honest_empty_state():
    assert "Отдельные темы по этому запросу не выделены. Попробуйте более широкую формулировку." in SCOUT_WEB_HTML


def test_sparse_result_preserves_found_publications_without_claiming_signals():
    assert 'id="retrieval-fallback"' in SCOUT_WEB_HTML
    assert "Публикации найдены, но отдельная тема пока не выделена" in SCOUT_WEB_HTML
    assert "Их релевантность и возможные дубли ещё не проверены." in SCOUT_WEB_HTML
    assert "Это исходные материалы, а не готовые слабые сигналы" in SCOUT_WEB_HTML
    assert "Карточки пока не сформированы. Найденные публикации — ниже." in SCOUT_WEB_HTML
    assert "state.retrievedWorks=Array.isArray(sourceJob.result?.works)?sourceJob.result.works:[]" in SCOUT_WEB_HTML
    assert "state.discoveryJob=discoveryJob" in SCOUT_WEB_HTML
    assert "&discovery=${encodeURIComponent(discoveryJob)}" in SCOUT_WEB_HTML
