"""Keep the result list compact without hiding failures or fabricating metrics."""

from saia.scout_assessment_web import ASSESSMENT_SCRIPT
from saia.scout_public_signals_web import PUBLIC_SCRIPT
from saia.scout_web import SCOUT_WEB_HTML


def test_result_heading_has_no_technical_caption():
    assert 'Тем в результатах:' not in SCOUT_WEB_HTML
    assert 'рост доли публикаций пока не измерен' not in SCOUT_WEB_HTML
    assert 'id="results-caption"' not in SCOUT_WEB_HTML
    assert "$('page-label').textContent=items.length?" in SCOUT_WEB_HTML
    assert 'id="results-title">Кандидаты в слабые сигналы' in SCOUT_WEB_HTML


def test_successful_enrichment_removes_the_progress_block():
    assert 'Оценки обновлены.' not in ASSESSMENT_SCRIPT
    assert 'Не полученные данные не уменьшают научный балл.' not in ASSESSMENT_SCRIPT
    assert "setEvidenceProgress('Обновляю данные…')" in ASSESSMENT_SCRIPT
    assert "render();setEvidenceProgress();" in ASSESSMENT_SCRIPT
    assert "status.classList.toggle('hidden',!message)" in ASSESSMENT_SCRIPT
    assert 'id="evidence-progress" class="evidence-progress hidden"' in SCOUT_WEB_HTML


def test_enrichment_failure_remains_actionable():
    assert "setEvidenceProgress('Не удалось обновить дополнительные данные. Попробуйте ещё раз.')" in ASSESSMENT_SCRIPT
    assert "Не удалось загрузить результаты. Обновите страницу или попробуйте позже." in SCOUT_WEB_HTML
    assert "Результат ещё не готов за 20 минут." in SCOUT_WEB_HTML
    assert "Продолжить ожидание" in SCOUT_WEB_HTML


def test_scientific_rows_omit_details_but_card_and_metrics_keep_them():
    row_code = SCOUT_WEB_HTML.split("$('rows').innerHTML=visible.length?", 1)[1].split('function renderRetrievalFallback', 1)[0]
    assert 'screening(q).explanation' not in row_code
    assert 'scoreDetail(' not in row_code
    assert '${scoreText(q.assessment)}' in row_code
    assert '${esc(screening(q).label)}' in row_code
    assert 'growth(c)' in row_code
    assert 'Почему тема показана' in SCOUT_WEB_HTML
    assert 'esc(readableSourceDescription(s.explanation))' in SCOUT_WEB_HTML
    assert 'Показатели и формулы расчёта' not in row_code
    assert 'Рост их числа сам по себе не подтверждает тренд' in SCOUT_WEB_HTML


def test_public_rows_keep_identity_and_unknown_values_without_technical_reasons():
    row_code = PUBLIC_SCRIPT.split('function publicRow(q)', 1)[1].split('function publicCitations', 1)[0]
    assert 'matching_explanation' not in row_code
    assert 'Опубликованная подборка, не расчёт Horizon' not in row_code
    assert 'Дата источника, не появления технологии' not in row_code
    assert 'Из публичного источника' in row_code
    assert 'data-label="Выпуск источника"' in row_code
    assert 'не рассчитана' in row_code
    assert 'Почему запись показана' in PUBLIC_SCRIPT
    assert 'esc(r.matching_explanation)' in PUBLIC_SCRIPT
    assert 'Дата выпуска источника не является датой появления технологии.' not in PUBLIC_SCRIPT
    assert 'Расчёт по источникам пока не выполнен.' in PUBLIC_SCRIPT
