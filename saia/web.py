"""Самодостаточный интерфейс первого вертикального сценария."""

WEB_APP_HTML = r'''<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Horizon — разведка слабых сигналов</title>
  <style>
    :root { --ink:#12233f; --blue:#215fb5; --pale:#eef4fb; --line:#d8e1ee; --warn:#8a5a00; --good:#19734a; --bad:#a12828; }
    * { box-sizing:border-box; }
    body { margin:0; background:#f5f7fa; color:var(--ink); font:15px/1.45 Arial,sans-serif; }
    header { background:linear-gradient(120deg,#10294c,#2468b7); color:white; padding:30px max(24px,calc((100% - 1120px)/2)); }
    header h1 { margin:0 0 6px; font-size:28px; }
    header p { margin:0; opacity:.86; }
    main { max-width:1120px; margin:24px auto; padding:0 20px 48px; }
    section { background:white; border:1px solid var(--line); border-radius:14px; padding:20px; box-shadow:0 5px 20px #19365c10; }
    form { display:grid; grid-template-columns:2fr 1fr 1fr .65fr auto; gap:12px; align-items:end; }
    #search { grid-template-columns:minmax(0,3fr) auto auto; }
    #search .search-options { grid-column:1/-1; border-top:1px solid var(--line); padding-top:10px; }
    #search .search-options summary, .lab-tools>summary { cursor:pointer; color:var(--blue); font-weight:bold; }
    .advanced-grid { display:grid; grid-template-columns:1fr 1fr .6fr; gap:12px; margin-top:10px; }
    .lab-tools { margin-top:18px; padding:14px; border:1px solid var(--line); border-radius:10px; background:#f8fafc; }
    .lab-tools>.note { margin-bottom:12px; }
    label { display:block; font-size:12px; font-weight:bold; color:#4c6079; }
    input, select, textarea { width:100%; margin-top:5px; padding:11px 12px; border:1px solid #b9c8db; border-radius:8px; font:inherit; }
    textarea { min-height:86px; resize:vertical; }
    button { border:0; border-radius:8px; background:var(--blue); color:white; font-weight:bold; padding:12px 18px; cursor:pointer; }
    button.secondary, a.secondary { background:#e8f0fb; color:var(--blue); margin-top:12px; text-decoration:none; border-radius:8px; font-weight:bold; padding:12px 18px; display:inline-block; }
    .actions { display:flex; gap:10px; flex-wrap:wrap; }
    button:disabled { opacity:.55; cursor:wait; }
    .note { margin:14px 0 0; padding:10px 12px; background:#fff8e6; color:var(--warn); border-radius:8px; }
    #status { margin:18px 0 10px; color:#53677f; }
    .summary { display:flex; gap:10px; flex-wrap:wrap; margin:12px 0; }
    .pill { background:var(--pale); padding:7px 10px; border-radius:999px; font-size:13px; }
    .error { background:#fff0f0; color:#8e2020; padding:10px 12px; border-radius:8px; margin:8px 0; }
    .benchmark { border-left:5px solid var(--bad); margin-bottom:14px; }
    .benchmark.pass { border-left-color:var(--good); }
    .check { color:var(--good); } .fail { color:var(--bad); }
    .query-form { display:block; }
    .query-choice { display:flex; align-items:center; gap:10px; font-size:16px; }
    .query-choice input { width:auto; margin:0; }
    .suggestion { display:flex; gap:10px; align-items:flex-start; padding:10px 0; border-bottom:1px solid var(--line); }
    .suggestion input { width:auto; margin:4px 0 0; }
    .query-form textarea { display:block; width:100%; min-height:80px; margin:8px 0 14px; font:inherit; }
    #query-panel { margin-top:16px; }
    .cards { display:grid; gap:12px; }
    article { background:white; border:1px solid var(--line); border-radius:12px; padding:16px 18px; }
    article h3 { margin:0 0 7px; font-size:17px; }
    article p { margin:7px 0; color:#40546d; }
    article .meta { font-size:12px; color:#687b91; }
    article a { color:var(--blue); }
    @media (max-width:850px) { form, #search, .advanced-grid { grid-template-columns:1fr; } form>label:first-child, #search .search-options { grid-column:1; } }
  </style>
</head>
<body>
  <header><h1>Horizon</h1><p>Научно-техническая разведка · независимая ветка Codex · v0.4.42</p></header>
  <main>
    <section style="margin-bottom:18px;border-color:#bccdf0;background:#f8fbff">
      <h2>Новое рабочее место технологического скаута</h2>
      <p>Реальные кандидаты слабых сигналов, карточка справа и передача выбранных тем экспертам.</p>
      <a class="secondary" href="/scout">Открыть интерфейс скаута →</a>
      <a class="secondary" href="/help" data-horizon-help="quick-start">Справка Horizon</a>
    </section>
    <section>
      <h2>Поиск научно-технологических изменений</h2>
      <form id="search">
        <label>Технология, научная тема или проблема<input id="query" placeholder="Например: новые катализаторы, ИИ-агенты, спутниковая связь" required></label>
        <button id="submit">Проверить публикации</button>
        <button class="secondary" type="button" id="plan-query">Настроить ветви</button>
        <details class="search-options">
          <summary>Период и объём предпросмотра</summary>
          <div class="advanced-grid">
            <label>Начало периода<input id="from" type="date" required></label>
            <label>Публикации строго до даты<input id="asof" type="date" required></label>
            <label>На источник<input id="limit" type="number" min="1" max="100" value="10"></label>
          </div>
        </details>
      </form>
      <div class="note">Введите любую технологию или научно-техническую тему. Можно сразу проверить охват либо сначала посмотреть необязательные поисковые ветви. Ни одна предложенная ветвь не выбирается и не запускается автоматически. Поисковая выдача ещё не является слабыми сигналами.</div>
      <details class="lab-tools">
        <summary>Режим аналитика: ретротесты, сохранённые эксперименты и служебные инструменты</summary>
        <div class="note">Эти инструменты предназначены для проверки методики. Их результаты нельзя автоматически считать текущими сигналами.</div>
        <div class="actions">
          <a class="secondary" href="/annotation-review">Независимая разметка 24 кандидатов</a>
          <a class="secondary" href="/retrieval-review">Проверка 99 расширенных совпадений</a>
          <a class="secondary" href="/external-evidence">Внешние источники</a>
          <button class="secondary" type="button" id="blind">Рабочий ретротест: полный локальный arXiv</button>
          <button class="secondary" type="button" id="verified">Показать направленный ретротест GNN</button>
          <button class="secondary" type="button" id="terminology">Термины и фрагменты широкого ИИ-корпуса</button>
          <button class="secondary" type="button" id="history">История результатов ИИ-корпуса</button>
          <button class="secondary" type="button" id="expansion">Предложить связанные термины из ИИ-корпуса</button>
          <button class="secondary" type="button" id="hybrid">Сохранённый гибридный эксперимент ИИ</button>
          <button class="secondary" type="button" id="embedding-progress">Готовность нового корпуса OpenAlex + arXiv</button>
          <button class="secondary" type="button" id="enriched-retro">Ретротест OpenAlex + arXiv: новый сохранённый результат</button>
          <button class="secondary" type="button" id="current-signals">Текущий пилот: выбор данных для ИИ</button>
        </div>
        <form id="query-reopen" class="query-form">
          <label>Открыть ранее сохранённую управляемую версию (например, ml-area-2017/v3)<input id="saved-query-id" required maxlength="160" placeholder="Номер версии"></label>
          <button class="secondary" type="submit">Открыть версию без нового поиска</button>
        </form>
        <form id="portfolio-open" class="query-form">
          <p id="embedding-progress-state" aria-live="polite"></p>
          <label>Карта и радар выбранного сохранённого снимка<input id="portfolio-id" required value="acfc51c7-a220-466b-b442-a2cca82a235a" maxlength="36"></label>
          <label for="portfolio-assessment">Версия оценки<select id="portfolio-assessment"><option value="">Без оценки: исходный состав</option></select></label>
          <button class="secondary" type="button" id="assessment-history-load">Загрузить историю оценок этого снимка</button>
          <button class="secondary" type="submit" id="portfolio-submit">Открыть карту и радар без пересчёта</button>
        </form>
        <p id="assessment-history-state" aria-live="polite"></p>
      </details>
    </section>
    <div id="query-panel"></div><div id="status"></div><div id="output"></div>
  </main>
  <script>
    const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const ruStatus=s=>({forming:'Формируется по автоматическим признакам',watch:'Наблюдение',candidate:'Кандидат слабого сигнала',mature:'Зрелая тема (старые правила)',widespread:'Распространённая публикационная тема'}[s]||s);
    const ruLabel=s=>s.toLowerCase()==='graph convolutional network'?'Графовые свёрточные нейронные сети (GCN)':s;
    const metricText=(m,kind)=>{const value=m[`${kind}_percentile`];if(value!==null&&value!==undefined)return String(value);const reason=m[`${kind}_availability`];if(reason==='insufficient_peer_topics'||reason==='insufficient_current_peer_topics')return `недостаточно тем для сравнения (${m.peer_topics}/${m.min_percentile_peers})`;if(reason==='historical_background_parameter_sensitivity_unresolved')return `неустойчива при проверке (${m.novelty_sensitivity_percentile_min}–${m.novelty_sensitivity_percentile_max})`;if(reason==='insufficient_full_windows')return `недостаточно полных периодов (${m.momentum_used_full_windows}/${m.momentum_required_full_windows})`;if(reason==='coverage_unknown')return 'покрытие периодов не проверено';if(reason==='coverage_not_comparable')return 'покрытие периодов несопоставимо';if(reason==='no_prior_topic_anchor')return 'нет более ранней темы для сравнения';return 'не рассчитана';};
    const noveltyContext=m=>{if(m.novelty_basis!=='nearest_pre_period_semantic_topic_anchor')return '';const terms=(m.nearest_background_terms||[]).slice(0,5).join(', ');const range=`${m.novelty_sensitivity_percentile_min}–${m.novelty_sensitivity_percentile_max}`;if(m.novelty_availability==='available_historical_background_sensitivity_stable')return `<div class="ok"><b>Исторический фон:</b> ближайшая тема до начала периода — ${esc(terms||'название не определено')}; сходство ${esc(m.nearest_background_similarity)}. Перцентиль ${esc(m.novelty_percentile)} устойчив во всех ${esc(m.novelty_sensitivity_variants)} вариантах проверки.</div>`;return `<div class="note"><b>Новизна пока не включена в оценку:</b> raw distance ${esc(m.novelty_raw)}, диапазон перцентиля ${esc(range)} в ${esc(m.novelty_sensitivity_variants)} вариантах фоновой кластеризации.</div>`;};
    const gateName=s=>({G1_maturity:'зрелость по старым правилам',G1_publication_prevalence:'распространённость среди активных тем',G2_novelty:'новизна относительно соседних тем',G4_momentum:'темп роста относительно соседних тем',G4_positive_share_slope:'доля публикаций растёт',G4_positive_share_change:'доля выросла за период',G3_consecutive:'последовательные активные окна',G_coherence:'смысловая связность',G_coherence_calibration:'порог связности проверен',G_coverage:'сопоставимое покрытие источников',G_embedding_coverage:'полнота векторизации',G0_volume:'достаточно публикаций',G0_concentration:'нет доминирования одной организации',G0_single_org_share:'нет доминирования одной организации',G0_independent_orgs:'несколько организаций',G3_independent_teams:'несколько исследовательских групп',G3_persistence:'устойчивость наблюдения',G5_age:'возраст линии',G6_primary_sources:'не только обзорные публикации'}[s]||s);
    const checkName=s=>({quality_generation_pinned:'поколение качества закреплено',peer_topic_coverage:'достаточно соседних тем',temporal_leakage:'нет данных из будущего',quality_filter_enforced:'карантин исключён',no_one_window_forming:'один всплеск не проходит',maturity_gate_enforced:'зрелые темы отсечены',repeatable_partition:'результат повторяется'}[s]||s);
    const renderGate=g=>`<p>${g.passed===true?'✓':g.passed===false?'✕':'?'} ${esc(gateName(g.gate==='G0_concentration'?'G0_single_org_share':g.gate))} · ${esc(g.passed===true?'Правило выполнено.':g.passed===false?g.reason:'Недостаточно данных для проверки.')} Измерение: ${esc(g.observed??'неизвестно')}; порог: ${esc(g.threshold??'не указан')}.</p>`;
    const renderEvidence=e=>`<article><h3>${esc(e.title)}</h3><div class="meta">${esc(e.published_at)} · ${esc(e.role==='раннее основание'?'самая ранняя работа в выборке':e.role==='рост последнего окна'?'пример из последнего окна (не доказательство роста)':e.role)}</div><div>${e.sources.map(s=>`<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.type)}</a>`).join(' · ')}</div></article>`;
    const queryPanel=document.getElementById('query-panel');
    const isoDate=value=>value.toISOString().slice(0,10);
    const currentDate=new Date(), periodStart=new Date(currentDate);
    periodStart.setUTCFullYear(periodStart.getUTCFullYear()-2);
    document.getElementById('from').value=isoDate(periodStart);
    document.getElementById('asof').value=isoDate(currentDate);
    let transparentPlan=null, approvedTransparentPlan=null;
    const renderTransparentPlan=plan=>{
      const suggestions=(plan.suggestions||[]).map(item=>`<label class="suggestion"><input type="checkbox" name="branch" value="${esc(item.suggestion_id)}"><span><b>${esc(item.label_ru)}</b><br><span class="meta">${esc(item.query_en)}</span></span></label>`).join('');
      queryPanel.innerHTML=`<article><h3>План поиска</h3><p><b>Исходный запрос сохранён:</b> ${esc(plan.original_query)}</p><div class="note">Предложения взяты из внутреннего словаря и могут быть неточными. Выберите только понятные ветви. Исходный запрос будет выполнен в любом случае.</div>${suggestions||'<p>Подходящих предложений во внутреннем словаре нет. Можно явно подтвердить запуск только исходного запроса.</p>'}<label>Кто подтверждает план<input id="plan-approved-by" maxlength="120" placeholder="Имя или служебный идентификатор" required></label><div class="actions"><button type="button" id="approve-plan">Подтвердить выбранные ветви</button></div><p class="meta">Контрольная сумма предпросмотра: ${esc(plan.plan_payload_sha256)}</p></article>`;
      document.getElementById('approve-plan').onclick=approveTransparentPlan;
    };
    const approveTransparentPlan=async()=>{
      const approvedBy=document.getElementById('plan-approved-by').value.trim();
      if(!approvedBy){document.getElementById('status').textContent='Укажите, кто подтверждает план.';return;}
      const selected=[...queryPanel.querySelectorAll('input[name="branch"]:checked')].map(item=>item.value);
      document.getElementById('status').textContent='Сохраняю подтверждённый план без запуска поиска…';
      try{
        const response=await fetch('/query-plan/approve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query:transparentPlan.original_query,max_suggestions:12,preview_payload_sha256:transparentPlan.plan_payload_sha256,selected_branch_ids:selected,approved_by:approvedBy,operation_id:crypto.randomUUID()})});
        const saved=await response.json();if(!response.ok)throw new Error(saved.detail||JSON.stringify(saved));
        approvedTransparentPlan=saved;
        queryPanel.innerHTML=`<article><h3>План подтверждён</h3><p>Исходный запрос и ${selected.length} дополнительных ветвей сохранены в неизменяемой истории. Сбор данных ещё не запускался.</p><div class="note">Быстрый сбор использует публичные API. Для полного локального arXiv сначала нужно явно задать точные фразы каждой ветви. Ни один вариант не запускается автоматически.</div><div class="actions"><button type="button" id="start-balanced-job">Быстрый ограниченный сбор</button><button class="secondary" type="button" id="prepare-local-plan">Подготовить точные фразы для local arXiv</button></div><p class="meta">План ${esc(saved.plan_id)}</p></article>`;
        document.getElementById('start-balanced-job').onclick=()=>startBalancedJob(null);
        document.getElementById('prepare-local-plan').onclick=renderCompilationForm;
        document.getElementById('status').textContent='План сохранён; выполнение ожидает отдельного запуска.';
      }catch(error){document.getElementById('status').textContent='План не сохранён';document.getElementById('output').innerHTML=`<div class="error">${esc(error.message)}</div>`;}
    };
    const renderCompilationForm=()=>{
      const plan=approvedTransparentPlan?.approved_plan;if(!plan)return;
      const rows=plan.branches.map(branch=>`<div class="lab-tools" data-compile-branch="${esc(branch.branch_id)}"><p><b>${esc(branch.label_ru||'Исходный запрос')}</b></p><p class="meta">Строка ветви: ${esc(branch.query)}</p><label>Точные включаемые фразы — по одной на строку<textarea name="included" placeholder="Например: tissue engineering">${branch.branch_id==='original-query'?esc(branch.query):''}</textarea></label><label>Исключаемые фразы — по одной на строку<textarea name="excluded" placeholder="Можно оставить пустым"></textarea></label></div>`).join('');
      queryPanel.innerHTML=`<article><h3>Точные фразы для локального arXiv</h3><div class="note">Локальный поиск требует буквальных фраз в названии или аннотации. Система не переводит и не дробит строки сама. Заполните каждую ветвь; этот шаг только сохранит правила и ещё не запустит сканирование.</div>${rows}<button type="button" id="compile-local-plan">Подтвердить точные фразы</button></article>`;
      document.getElementById('compile-local-plan').onclick=compileLocalPlan;
    };
    const lines=value=>value.split('\n').map(item=>item.trim()).filter(Boolean);
    const compileLocalPlan=async()=>{
      const rows=[...queryPanel.querySelectorAll('[data-compile-branch]')];
      const branch_specs=rows.map(row=>({branch_id:row.dataset.compileBranch,included_phrases:lines(row.querySelector('[name="included"]').value),excluded_phrases:lines(row.querySelector('[name="excluded"]').value)}));
      if(branch_specs.some(item=>!item.included_phrases.length)){document.getElementById('status').textContent='Для каждой ветви нужна хотя бы одна точная фраза.';return;}
      document.getElementById('status').textContent='Сохраняю точные фразы без запуска сканирования…';
      try{
        const plan=approvedTransparentPlan;
        const response=await fetch(`/query-plans/${encodeURIComponent(plan.plan_id)}/compile`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({branch_specs,compiled_by:plan.approved_plan.approved_by,operation_id:crypto.randomUUID()})});
        const compiled=await response.json();if(!response.ok)throw new Error(compiled.detail||JSON.stringify(compiled));
        queryPanel.innerHTML=`<article><h3>Точные фразы подтверждены</h3><p>Правила local arXiv сохранены отдельно от запуска и привязаны к исходному плану.</p><div class="note">Полный проход читает 3,16 млн записей один раз для всех ветвей. Он может занять несколько минут. Результат останется корпусом-кандидатом.</div><button type="button" id="start-local-job">Запустить сбор с полным local arXiv</button><p class="meta">Компиляция ${esc(compiled.compilation_id)}</p></article>`;
        document.getElementById('start-local-job').onclick=()=>startBalancedJob(compiled.compilation_id);
        document.getElementById('status').textContent='Точные фразы сохранены; сканирование ещё не запущено.';
      }catch(error){document.getElementById('status').textContent='Точные фразы не сохранены';document.getElementById('output').innerHTML=`<div class="error">${esc(error.message)}</div>`;}
    };
    const startBalancedJob=async compiledId=>{
      const plan=approvedTransparentPlan;if(!plan)return;
      document.getElementById('status').textContent='Ставлю подтверждённый многоветочный сбор в очередь…';
      try{
        const payload={requested_by:plan.approved_plan.approved_by,date_from:document.getElementById('from').value,as_of_date:document.getElementById('asof').value,limit_per_source:Math.min(25,Number(document.getElementById('limit').value)),max_results:15,max_attempts:3,operation_id:crypto.randomUUID()};
        if(compiledId)payload.compiled_query_plan_id=compiledId;
        const response=await fetch(`/query-plans/${encodeURIComponent(plan.plan_id)}/jobs/discovery`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
        const job=await response.json();if(!response.ok)throw new Error(job.detail||JSON.stringify(job));
        queryPanel.innerHTML='';await watchJob(job.job_id);
      }catch(error){document.getElementById('status').textContent='Многоветочный сбор не запущен';document.getElementById('output').innerHTML=`<div class="error">${esc(error.message)}</div>`;}
    };
    document.getElementById('plan-query').onclick=async()=>{
      const query=document.getElementById('query').value.trim();if(query.length<2){document.getElementById('status').textContent='Введите технологию или научную тему.';return;}
      document.getElementById('status').textContent='Ищу только необязательные варианты ветвей; поиск публикаций не запускается…';
      try{
        const response=await fetch('/query-plan/preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query,max_suggestions:12})});
        transparentPlan=await response.json();if(!response.ok)throw new Error(transparentPlan.detail||JSON.stringify(transparentPlan));
        approvedTransparentPlan=null;renderTransparentPlan(transparentPlan);document.getElementById('status').textContent='План показан. Ничего не выбрано и не запущено автоматически.';
      }catch(error){document.getElementById('status').textContent='План недоступен';queryPanel.innerHTML=`<div class="error">${esc(error.message)}</div>`;}
    };
    let queryProposal=null, approvedQueryId=null, approvedMissionId=null, approvedRequestedBy='analyst';
    const jobButtons=()=>`<div class="actions"><button type="button" id="query-job">Запустить сохраняемый предпросмотр</button><button type="button" id="query-full-job">Запустить полный анализ до карточек</button><button type="button" class="secondary" id="query-jobs">История задач</button></div><label class="query-choice"><input type="checkbox" id="full-scope-ack">Я понимаю: полный корпус этого запуска — локальный arXiv; OpenAlex используется отдельно для предпросмотра и последующего обогащения.</label><p>Полный анализ ограничен 10 000 публикаций. При превышении задача безопасно остановится и предложит сузить запрос. Результат — очередь научной проверки, а не прогноз рынка.</p>`;
    const renderJob=j=>{
      const eventName=e=>({queued:'поставлена в очередь',claimed:'взята воркером',heartbeat:'воркер работает',stage_started:'стадия начата',stage_succeeded:'стадия завершена',stage_reused:'проверенный результат стадии использован повторно',succeeded:'задача завершена',failed:'ошибка',cancel_requested:'запрошена отмена',cancelled:'отменена',lease_expired_requeued:'возвращена в очередь после потери воркера',lease_expired_failed:'попытки исчерпаны',retry_created:'создан повтор'}[e]||e);
      const stageName=e=>({collect:'полный сбор arXiv',ingest:'загрузка сырья',parent_coverage:'проверка знаменателя и покрытия',normalize:'нормализация и дедупликация',quality:'проверка качества',embed:'научные эмбеддинги',cluster:'временные тематические линии',score:'правила слабого сигнала',triage:'очередь из карточек'}[e]||e||'');
      const events=(j.events||[]).map(e=>`<p class="meta">${esc(e.created_at)} · ${esc(eventName(e.event_type))}${e.details?.stage?` · ${esc(stageName(e.details.stage))}`:''}</p>`).join('');
      const sourceErrors=Object.entries(j.result?.errors||{}).map(([source,message])=>`<div class="error">${esc(source)}: ${esc(message)}</div>`).join('');
      const sourceCounts=Object.entries(j.result?.source_counts||{}).map(([source,count])=>`<span class="pill">${esc(source)}: ${esc(count)}</span>`).join('');
      const sourceModes=Object.entries(j.result?.source_modes||{}).map(([source,mode])=>`<p class="meta">${esc(source)}: ${esc(mode)}</p>`).join('');
      const works=(j.result?.works||[]).slice(0,25).map(w=>`<article><h3>${esc(w.title)}</h3><p>${esc(w.published_at)}</p>${(w.urls||[]).map((u,i)=>`<a href="${esc(u)}" target="_blank" rel="noopener">Источник ${i+1}</a>`).join(' · ')}</article>`).join('');
      const full=j.job_kind==='controlled_full_analysis', balanced=j.job_kind==='approved_balanced_discovery', fullResult=j.result||{};
      const fullSummary=full&&j.status==='succeeded'?`<div class="summary"><span class="pill">Публикаций: ${esc(fullResult.collection?.selected_records)}</span><span class="pill">Карточек: ${esc(fullResult.cards?.all)}</span><span class="pill">Показано: ${esc(fullResult.cards?.shown)}</span><span class="pill">score #${esc(fullResult.runs?.score)}</span></div><button type="button" data-full-mission="${esc(j.mission_id)}" data-full-score="${esc(fullResult.runs?.score)}">Открыть итоговую очередь карточек</button>${(fullResult.limitations||[]).map(v=>`<div class="note">${esc(v)}</div>`).join('')}`:'';
      const automaticAnalysis=balanced&&j.status==='succeeded'?`<div class="actions"><button type="button" data-automatic-full="${esc(j.job_id)}">Сформировать до 15 кандидатов слабых сигналов</button></div><div class="note">Кандидаты формируются автоматически. Экспертная валидация для получения результата не требуется. Если доказательных тем меньше 15, система покажет фактическое число и не станет заполнять список шумом.</div>`:'';
      const retrievalReview=balanced&&j.status==='succeeded'?`<details><summary>Необязательная проверка качества поиска</summary><p><a href="/retrieval-review?job_id=${encodeURIComponent(j.job_id)}" target="_blank" rel="noopener">Передать выдачу на независимую проверку релевантности</a> · <a href="/retrieval-adjudication?job_id=${encodeURIComponent(j.job_id)}" target="_blank" rel="noopener">сопоставить две анкеты</a></p><div class="note">Этот дополнительный контур оценивает качество поиска. Он не блокирует автоматические кандидаты и не подтверждает их как слабые сигналы.</div></details>`:'';
      document.getElementById('status').textContent=`Задача ${j.job_id}: ${j.status}. Попыток: ${j.attempt_count}/${j.max_attempts}.`;
      const branchCounts=balanced?Object.entries(j.result?.merge?.branch_contributions||{}).map(([branch,count])=>`<span class="pill">${esc(branch)}: ${esc(count)}</span>`).join(''):'';
      document.getElementById('output').innerHTML=`<article><h3>${full?'Полный анализ до карточек':balanced?'Сбор по подтверждённым ветвям':'Сохраняемый предпросмотр публикаций'}</h3><div class="summary"><span class="pill">${esc(j.status)}</span><span class="pill">полнота: ${esc(j.result_completeness)}</span>${j.query_version_id?`<span class="pill">версия ${esc(j.query_version_id)}</span>`:''}${sourceCounts}${branchCounts}</div>${sourceModes}<div class="note">${esc(j.interpretation)}</div>${j.error?`<div class="error">${esc(j.error)}</div>`:''}${sourceErrors}${fullSummary}${automaticAnalysis}${retrievalReview}<details><summary>Журнал исполнения</summary>${events||'<p>Журнал загружается…</p>'}</details></article>${works?`<h2>Публикации-кандидаты</h2><div class="cards">${works}</div>`:''}`;
    };
    const watchJob=async jobId=>{
      for(let attempt=0;attempt<150;attempt++) {
        const response=await fetch(`/jobs/${encodeURIComponent(jobId)}`); const job=await response.json();
        if(!response.ok) throw new Error(job.detail||JSON.stringify(job)); renderJob(job);
        if(['succeeded','failed','cancelled'].includes(job.status)) return;
        await new Promise(resolve=>setTimeout(resolve,2000));
      }
      throw new Error('Задача продолжает выполняться. Откройте её позже в истории задач.');
    };
    document.getElementById('hybrid').addEventListener('click',async()=>{
      const button=document.getElementById('hybrid'); button.disabled=true;
      document.getElementById('status').textContent='Открываю сохранённый эксперимент, без нового поиска…';
      try {
        const response=await fetch('/corpus/ml-area-2017/hybrid-history');
        const h=await response.json(); if(!response.ok) throw new Error(h.detail||JSON.stringify(h));
        if(!h.snapshots.length) throw new Error('Сохранённых гибридных экспериментов пока нет.');
        const fetched=await fetch(`/hybrid/${encodeURIComponent(h.snapshots[0].snapshot_id)}`);
        const p=await fetched.json(); if(!fetched.ok) throw new Error(p.detail||JSON.stringify(p));
        const counts=p.counts;
        const lexical=p.candidates.filter(c=>c.channels.includes('lexical'))
          .map(c=>({...c,contexts:c.contexts.filter((v,i,all)=>all.findIndex(x=>x.work_id===v.work_id)===i)}))
          .sort((a,b)=>a.label.localeCompare(b.label));
        document.getElementById('status').textContent=`Снимок ${p.snapshot_id}: cluster #${p.provenance.cluster_run_id}, quality #${p.provenance.quality_generation_id}`;
        document.getElementById('output').innerHTML=`<article><h3>Два канала предложений на одном корпусе</h3><div class="summary"><span class="pill">Допущенных работ: ${counts.eligible_corpus}</span><span class="pill">Семантических линий: ${counts.semantic_lines}</span><span class="pill">Редких фраз: ${counts.lexical_phrases_selected} из ${counts.lexical_phrases_before_cap}</span><span class="pill">Разных составов: ${counts.hybrid_candidates}</span><span class="pill">Без вектора: ${p.missing_vector_work_ids.length}</span></div><p>Период: ${esc(p.period_from)} — строго раньше ${esc(p.period_end_exclusive)}. Версия: ${esc(p.version)}.</p><div class="note">Это кандидаты для проверки, не подтверждённые сигналы. Частичное пересечение не сливает темы. Показаны первые 30 буквальных кандидатов по алфавиту — это не рейтинг перспективности.</div>${p.limitations.map(v=>`<p>${esc(v)}</p>`).join('')}</article><div class="cards">${lexical.slice(0,30).map(c=>`<article><h3>${esc(c.label)}</h3><div class="summary"><span class="pill">Работ: ${c.document_support}</span><span class="pill">Каналы: ${c.channels.map(v=>v==='lexical'?'буквальный':'семантический').join(' · ')}</span><span class="pill">Первое наблюдение: ${esc(c.first_observed_in_corpus)}</span></div><p>Варианты с тем же составом работ: ${c.aliases.map(esc).join(' · ')}.</p><p>Рост: ${c.publication_series.direction==='not_established'?'не установлен':esc(c.publication_series.direction)}. Итоговый балл и статус слабого сигнала не рассчитаны.</p><details><summary>Фрагменты, связи и ограничения</summary>${c.contexts.slice(0,3).map(v=>`<p>${esc(v.published_at)} · ${esc(v.context)}</p>${v.sources.map(s=>`<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.type)}</a>`).join(' · ')}`).join('')}${c.overlap_links.map(l=>`<p>Пересечение с ${esc(l.candidate_id)}: ${l.shared_work_ids.length} работ, ${(l.lexical_fraction*100).toFixed(1)}% буквального состава. Это не перенос статуса.</p>`).join('')}${c.unknown_checks.map(v=>`<p>${esc(v)}</p>`).join('')}</details></article>`).join('')}</div>`;
      } catch(error) {document.getElementById('output').innerHTML=`<div class="error">${esc(error.message)}</div>`;}
      finally {button.disabled=false;}
    });
    document.getElementById('query-reopen').addEventListener('submit',async event=>{
      event.preventDefault(); const button=event.target.querySelector('button'); button.disabled=true;
      try {
        const response=await fetch(`/queries/approved?query_version_id=${encodeURIComponent(document.getElementById('saved-query-id').value.trim())}`);
        const p=await response.json(); if(!response.ok) throw new Error(p.detail||JSON.stringify(p));
        queryProposal=null; approvedQueryId=p.decision.query_version_id; approvedMissionId=approvedQueryId.replace(/\/v\d+$/,''); approvedRequestedBy=p.decision.reviewed_by||'analyst';
        const chosen=p.suggestions.filter(t=>p.decision.selected_ids.includes(t.suggestion_id));
        queryPanel.innerHTML=`<section><h2>Сохранённая версия ${esc(approvedQueryId)}</h2><p>Исходная фраза: ${esc(p.original_query)}. Добавлено: ${chosen.map(t=>esc(t.phrase)).join(' · ')}.</p><p>Исключения: ${p.decision.exclusions.map(esc).join(' · ')||'нет'}. Автор решения: ${esc(p.decision.reviewed_by)} (указан пользователем, не проверенная учётная запись).</p><p>Период: ${esc(p.period_from)} — строго раньше ${esc(p.period_end_exclusive)}; поколение качества #${p.provenance.quality_generation_id}. Отпечаток предложения: ${esc(p.proposal_content_sha256)}.</p><div class="note">Выбор открыт без нового поиска и без изменения корпуса. Совместная встречаемость не подтверждает синонимию или слабый сигнал.</div>${chosen.map(t=>`<article><h3>${esc(t.phrase)}</h3><p>Поддержка: ${t.document_support} работ.</p><details><summary>Фрагменты и первоисточники</summary>${t.contexts.slice(0,2).map(c=>`<p>${esc(c.published_at)} · ${esc(c.context)}</p>${c.sources.map(s=>`<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.type)}</a>`).join(' · ')}`).join('')}</details></article>`).join('')}<button type="button" id="query-preview">Выполнить предпросмотр сохранённого запроса</button><p>Предпросмотр — отдельный ограниченный поиск, не аналитический корпус.</p>${jobButtons()}</section>`;
      } catch(error) {queryPanel.innerHTML=`<section class="error">${esc(error.message)}</section>`; approvedQueryId=null; approvedMissionId=null;}
      finally {button.disabled=false;}
    });
    document.getElementById('expansion').addEventListener('click',async()=>{
      const button=document.getElementById('expansion'); button.disabled=true;
      queryPanel.innerHTML='<section>Ищу буквальные сочетания в сохранённом ИИ-корпусе…</section>';
      try {
        const response=await fetch('/corpus/ml-area-2017/query-proposals',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query:document.getElementById('query').value})});
        const p=await response.json(); if(!response.ok) throw new Error(p.detail||JSON.stringify(p));
        queryProposal=p; approvedQueryId=null;
        const terms=p.suggestions.map(t=>`<article><label class="query-choice"><input type="checkbox" name="suggestion" value="${esc(t.suggestion_id)}">${esc(t.phrase)}</label><p>Поддержка: ${t.document_support} разных работ. Это совместная встречаемость, не проверенный синоним.</p><details><summary>Фрагменты и первоисточники</summary>${t.contexts.slice(0,2).map(c=>`<p class="meta">${esc(c.published_at)} · работа #${c.work_id}</p><p>${esc(c.context)}</p><div>${c.sources.map(s=>`<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.type)}</a>`).join(' · ')}</div>`).join('')}</details></article>`).join('');
        queryPanel.innerHTML=`<section><h2>Управляемое расширение запроса</h2><p>Исходная фраза: ${esc(p.original_query)}. Основание: ${p.seed_works} работ из ${p.eligible_corpus_works}; поколение качества #${p.provenance.quality_generation_id}.</p><p>Использован сохранённый корпус: ${esc(p.period_from||'начало неизвестно')} — строго раньше ${esc(p.period_end_exclusive)}. Даты поисковой формы этот корпус не меняют.</p><div class="note">Выберите от одного до четырёх сочетаний. Они добавятся через «ИЛИ» и могут расширить поиск слишком сильно. Ни одна галочка не выбрана автоматически; корпус и карточки не пересчитываются.</div><form id="query-approval" class="query-form"><div class="cards">${terms||'<p>Подходящих сочетаний с поддержкой нескольких работ нет.</p>'}</div>${p.suggestions.length?'<label>Исключения: одна обычная фраза на строку<textarea id="query-exclusions"></textarea></label><label>Автор решения (имя, указанное пользователем; не проверенная учётная запись)<input id="query-reviewer" required maxlength="120"></label><button type="submit">Сохранить выбранный словарь как новую версию</button>':''}</form>${p.warnings.map(w=>`<div class="note">${esc(w)}</div>`).join('')}<div id="query-message"></div><div id="query-approved"></div></section>`;
      } catch(error) {queryProposal=null; queryPanel.innerHTML=`<section class="error">${esc(error.message)}</section>`;}
      finally {button.disabled=false;}
    });
    queryPanel.addEventListener('submit',async event=>{
      if(event.target.id!=='query-approval') return; event.preventDefault();
      const selected=Array.from(queryPanel.querySelectorAll('input[name="suggestion"]:checked')).map(e=>e.value);
      const message=document.getElementById('query-message');
      if(selected.length<1||selected.length>4) {message.textContent='Выберите от одного до четырёх сочетаний.'; return;}
      const button=event.target.querySelector('button[type="submit"]'); button.disabled=true;
      try {
        const payload={selected_ids:selected,exclusions:document.getElementById('query-exclusions').value.split('\n').map(s=>s.trim()).filter(Boolean),reviewed_by:document.getElementById('query-reviewer').value};
        const response=await fetch(`/query-proposals/${encodeURIComponent(queryProposal.proposal_id)}/approve`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
        const saved=await response.json(); if(!response.ok) throw new Error(saved.detail||JSON.stringify(saved));
        approvedQueryId=saved.query_version_id; approvedMissionId=approvedQueryId.replace(/\/v\d+$/,''); approvedRequestedBy=payload.reviewed_by; message.textContent=`Сохранена версия ${saved.query_version_id}. Старые результаты не изменены.`;
        document.getElementById('query-approved').innerHTML=`<button type="button" id="query-preview">Выполнить предпросмотр сохранённого запроса</button><p>Это отдельный ограниченный поиск, не новый аналитический корпус и не список слабых сигналов.</p>${jobButtons()}`;
      } catch(error) {message.textContent=error.message;}
      finally {button.disabled=false;}
    });
    queryPanel.addEventListener('click',async event=>{
      if(event.target.id==='query-job') {
        const button=event.target; button.disabled=true;
        try {
          if(!approvedQueryId||!approvedMissionId) throw new Error('Сначала откройте или сохраните версию запроса.');
          const response=await fetch(`/missions/${encodeURIComponent(approvedMissionId)}/jobs/discovery`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query_version_id:approvedQueryId,requested_by:approvedRequestedBy,limit_per_source:Number(document.getElementById('limit').value),operation_id:crypto.randomUUID()})});
          const job=await response.json(); if(!response.ok) throw new Error(job.detail||JSON.stringify(job)); renderJob(job); await watchJob(job.job_id);
        } catch(error) {document.getElementById('status').textContent=error.message;}
        finally {button.disabled=false;} return;
      }
      if(event.target.id==='query-full-job') {
        const button=event.target; button.disabled=true;
        try {
          if(!approvedQueryId||!approvedMissionId) throw new Error('Сначала откройте или сохраните версию запроса.');
          if(!document.getElementById('full-scope-ack')?.checked) throw new Error('Подтвердите границу полного корпуса: локальный arXiv, OpenAlex — отдельный канал.');
          const response=await fetch(`/missions/${encodeURIComponent(approvedMissionId)}/jobs/full-analysis`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query_version_id:approvedQueryId,requested_by:approvedRequestedBy,max_records:10000,top_n:15,acknowledge_source_scope:true,operation_id:crypto.randomUUID()})});
          const job=await response.json(); if(!response.ok) throw new Error(job.detail||JSON.stringify(job)); renderJob(job); await watchJob(job.job_id);
        } catch(error) {document.getElementById('status').textContent=error.message;}
        finally {button.disabled=false;} return;
      }
      if(event.target.id==='query-jobs') {
        try {
          if(!approvedMissionId) throw new Error('Сначала откройте или сохраните версию запроса.');
          const response=await fetch(`/missions/${encodeURIComponent(approvedMissionId)}/jobs`); const data=await response.json(); if(!response.ok) throw new Error(data.detail||JSON.stringify(data));
          document.getElementById('status').textContent=`Сохранённых задач: ${data.jobs.length}`;
          document.getElementById('output').innerHTML=`<div class="cards">${data.jobs.map(j=>`<article><h3>${esc(j.status)} · ${esc(j.query_version_id)}</h3><p>${esc(j.created_at)} · попыток ${j.attempt_count}/${j.max_attempts}</p><button type="button" data-job-open="${esc(j.job_id)}">Открыть журнал</button></article>`).join('')||'<p>Сохранённых задач пока нет.</p>'}</div>`;
        } catch(error) {document.getElementById('status').textContent=error.message;} return;
      }
      if(event.target.id!=='query-preview') return; const button=event.target; button.disabled=true;
      const previewQueryId=approvedQueryId, previewLimit=Number(document.getElementById('limit').value);
      document.getElementById('status').textContent='Получаю ограниченную выдачу сохранённой версии…';
      try {
        const response=await fetch('/queries/preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query_version_id:previewQueryId,limit_per_source:previewLimit})});
        const data=await response.json(); if(!response.ok) throw new Error(data.detail||JSON.stringify(data));
        document.getElementById('status').textContent=`Версия ${previewQueryId}: ${data.works.length} канонических работ в предпросмотре. Это не знаменатель области.`;
        document.getElementById('output').innerHTML=`<article><h3>Выполненные выражения</h3><p>OpenAlex: ${esc(data.search_plan.logical_expressions.openalex)}</p><p>arXiv: ${esc(data.search_plan.logical_expressions.arxiv)}</p><p>Публикации ${esc(data.date_from)} — строго раньше ${esc(data.as_of_date)}. Хеш: ${esc(data.query_hash)}</p></article>${Object.entries(data.errors).map(([k,v])=>`<div class="error">${esc(k)}: ${esc(v)}</div>`).join('')}${data.limitations.map(v=>`<div class="note">${esc(v)}</div>`).join('')}<div class="cards">${data.works.map(w=>`<article><h3>${esc(w.title)}</h3><p>${esc(w.published_at)}</p><p>${esc((w.abstract||'Аннотация отсутствует').slice(0,420))}</p>${w.urls.map((u,i)=>`<a href="${esc(u)}" target="_blank" rel="noopener">Источник ${i+1}</a>`).join(' · ')}</article>`).join('')}</div>`;
        document.getElementById('output').insertAdjacentHTML('afterbegin',`<div class="summary"><span class="pill">OpenAlex: ${esc(data.source_counts.openalex)}</span><span class="pill">arXiv: ${esc(data.source_counts.arxiv)}</span><span class="pill">Выдача ограничена ${esc(previewLimit)} работами на источник</span></div>`);
      } catch(error) {document.getElementById('status').textContent=error.message;}
      finally {button.disabled=false;}
    });
    document.getElementById('output').addEventListener('click',async event=>{
      const automatic=event.target.closest('button[data-automatic-full]');
      if(automatic){automatic.disabled=true;document.getElementById('status').textContent='Формирую автоматические кандидаты по публикационной динамике…';try{const response=await fetch(`/jobs/${encodeURIComponent(automatic.dataset.automaticFull)}/full-analysis`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({requested_by:'user-interface',max_records:10000,top_n:15,operation_id:crypto.randomUUID()})});const data=await response.json();if(!response.ok)throw new Error(data.detail||JSON.stringify(data));renderJob(data.analysis_job);await watchJob(data.analysis_job.job_id);}catch(error){document.getElementById('status').textContent=error.message;}finally{automatic.disabled=false;}return;}
      const button=event.target.closest('button[data-job-open]'); if(!button) return; button.disabled=true;
      try {await watchJob(button.dataset.jobOpen);} catch(error) {document.getElementById('status').textContent=error.message;} finally {button.disabled=false;}
    });
    document.getElementById('output').addEventListener('click',async event=>{
      const button=event.target.closest('button[data-full-score]'); if(!button) return; button.disabled=true;
      try {
        const response=await fetch(`/triage/${encodeURIComponent(button.dataset.fullMission)}?score_run_id=${encodeURIComponent(button.dataset.fullScore)}&limit=15`);
        const data=await response.json(); if(!response.ok) throw new Error(data.detail||JSON.stringify(data));
        document.getElementById('status').textContent=`Итог полного анализа: score #${data.score_run_id}. Показано ${data.counts.shown} карточек.`;
        document.getElementById('output').innerHTML=`<div class="note">${esc(data.interpretation)}</div><div class="cards">${data.queue.map(item=>{const c=item.card,m=c.metrics.observed;return `<article><h3>${item.rank}. ${esc(c.label)}</h3><div class="summary"><span class="pill">${esc(ruStatus(c.status))}</span><span class="pill">Публикаций: ${esc(m.doc_count)}</span><span class="pill">Новизна: ${esc(metricText(m,'novelty'))}</span><span class="pill">Динамика: ${esc(metricText(m,'momentum'))}</span><span class="pill">Экспертиза: ${c.expert_validation?.status==='not_requested'?'не запрошена':esc(c.expert_validation?.status||'неизвестно')}</span></div><p>${esc(item.why_in_queue)}</p><details><summary>Основания и ограничения</summary>${noveltyContext(m)}<p><b>Сырые показатели:</b> новизна ${esc(m.novelty_raw??'не рассчитана')}; наклон доли публикаций ${esc(m.momentum_raw??'не рассчитан')}; связность ${esc(m.coherence??'не рассчитана')}.</p>${c.gates.map(renderGate).join('')}${c.limitations.map(v=>`<p>${esc(v)}</p>`).join('')}${c.evidence.slice(0,5).map(renderEvidence).join('')}</details></article>`}).join('')}</div>`;
      } catch(error) {document.getElementById('status').textContent=error.message;}
      finally {button.disabled=false;}
    });
    document.getElementById('history').addEventListener('click',async()=>{
      document.getElementById('status').textContent='Открываю историю сохранённых результатов…';
      try {
        const response=await fetch('/history/ml-area-2017'); const data=await response.json();
        if(!response.ok) throw new Error(data.detail||JSON.stringify(data));
        const scores=data.runs.filter(r=>r.kind==='score'&&r.status==='done').slice(0,10);
        document.getElementById('status').textContent=`История: ${data.title}`;
        document.getElementById('output').innerHTML=`<div class="note">Старые результаты сохранены. Прогоны с разными правилами и входным корпусом нельзя сравнивать как точность одного алгоритма. Показано до 10 завершённых результатов.</div><div class="cards">${scores.map(r=>`<article><h3>Результат #${r.run_id} · правила v${esc(r.methodology_version)}</h3><p>Анализ публикаций до ${esc(r.as_of_date)} · сохранён ${esc(r.finished_at)}</p><p class="meta">Запрос: ${esc(r.query_version_id)} · модель: ${esc(r.embedding_model||'не указана')} · поколение качества: ${r.quality_generation_id??'не закреплено (legacy)'}</p><button type="button" data-history-run="${r.run_id}">Открыть карточки</button></article>`).join('')||'<p>Завершённых результатов нет.</p>'}</div>`;
      } catch(error) {document.getElementById('output').innerHTML=`<div class="error">${esc(error.message)}</div>`;}
    });
    document.getElementById('output').addEventListener('click',async event=>{
      const button=event.target.closest('button[data-history-run]'); if(!button) return;
      button.disabled=true;
      try {
        const response=await fetch(`/signals/ml-area-2017?score_run_id=${encodeURIComponent(button.dataset.historyRun)}`);
        const data=await response.json(); if(!response.ok) throw new Error(data.detail||JSON.stringify(data));
        const passport=data.provenance[0];
        document.getElementById('status').textContent=`Сохранённый результат #${data.score_run_id} · правила v${passport.methodology_version} · публикации до ${passport.as_of_date}`;
        document.getElementById('output').innerHTML=`<div class="note">Показано до 15 карточек сохранённого прогона. При отсутствии score порядок по индексу данных не означает перспективность. Legacy-результаты не считаются исправленными v0.4.</div><div class="cards">${data.cards.slice(0,15).map(c=>`<article><h3>${esc(c.label)}</h3><div class="summary"><span class="pill">${esc(ruStatus(c.status))}</span><span class="pill">Score: ${c.emergence_score??'не рассчитан'}</span><span class="pill">Индекс данных: ${c.evidence_confidence}/100</span><span class="pill">Первое наблюдение: ${esc(c.first_found)}</span></div><details><summary>Основания и ограничения</summary>${c.gates.map(renderGate).join('')}${c.limitations.map(l=>`<p>${esc(l)}</p>`).join('')}<div class="cards">${c.evidence.slice(0,3).map(renderEvidence).join('')}</div></details></article>`).join('')}</div>`;
      } catch(error) {document.getElementById('output').innerHTML=`<div class="error">${esc(error.message)}</div>`;}
      finally {button.disabled=false;}
    });
    document.getElementById('terminology').addEventListener('click',async()=>{
      const button=document.getElementById('terminology'); button.disabled=true;
      document.getElementById('status').textContent='Извлекаю буквальные фразы из сохранённого корпуса…';
      document.getElementById('output').innerHTML='';
      try {
        const response=await fetch('/corpus/ml-area-2017/terminology'); const data=await response.json();
        if(!response.ok) throw new Error(data.detail||JSON.stringify(data));
        const renderTerm=t=>`<article><h3>${esc(t.phrase)}</h3><div class="summary"><span class="pill">Работ: ${t.document_support}</span><span class="pill">Впервые в корпусе: ${esc(t.first_observed_in_corpus)}</span></div><details><summary>Точные фрагменты публикаций</summary>${t.contexts.map(c=>{const start=c.start-c.context_start,end=c.end-c.context_start;return `<p class="meta">${esc(c.published_at)} · ${esc(c.field==='title'?'название':'аннотация')} · работа #${c.work_id}</p><p>${esc(c.context.slice(0,start))}<mark>${esc(c.context.slice(start,end))}</mark>${esc(c.context.slice(end))}</p><div>${(c.sources||[]).map(s=>`<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.type)}</a>`).join(' · ')}</div>`}).join('')}</details></article>`;
        document.getElementById('status').textContent=`Корпус: ${data.corpus_works} работ · поколение качества #${data.provenance.quality_generation_id}`;
        document.getElementById('output').innerHTML=`<div class="note">Это инструмент поиска терминов, не список подтверждённых слабых сигналов. Показаны первые 20 из ${data.candidates.length} выгруженных фраз; всего повторяющихся сочетаний: ${data.candidate_phrases_total}. Порядок — число работ, не перспективность. Редкие новые темы такой порядок может пропустить.</div><h2>Повторяющиеся фразы</h2><div class="cards">${data.candidates.slice(0,20).map(renderTerm).join('')}</div><h2>Малая поддержка — очередь проверки</h2><p>До 10 примеров, не доказательство того, что тема является шумом.</p><div class="cards">${data.low_support_review.slice(0,10).map(renderTerm).join('')}</div>${data.limitations.map(s=>`<div class="note">${esc(s)}</div>`).join('')}`;
      } catch(error) { document.getElementById('status').textContent='Терминологический анализ недоступен'; document.getElementById('output').innerHTML=`<div class="error">${esc(error.message)}</div>`; }
      finally {button.disabled=false;}
    });
    document.getElementById('blind').addEventListener('click',async()=>{
      document.getElementById('status').textContent='Открываю полный локальный ретротест утверждённого запроса…';
      document.getElementById('output').innerHTML='';
      try {
        const [triageResponse,benchmarkResponse]=await Promise.all([fetch('/triage/ml-area-2017?limit=15'),fetch('/benchmarks/ml-area-2017')]);
        const data=await triageResponse.json(), benchmark=await benchmarkResponse.json();
        if(!triageResponse.ok) throw new Error(data.detail||JSON.stringify(data));
        if(!benchmarkResponse.ok) throw new Error(benchmark.detail||JSON.stringify(benchmark));
        const checks=Object.entries(benchmark.controls).map(([name,c])=>`<span class="pill ${c.passed===true?'check':c.passed===false?'fail':''}">${c.passed===true?'✓':c.passed===false?'✕':'?'} ${esc(checkName(name))}${c.passed==null?' — не проверено':''}</span>`).join('');
        const statuses=data.counts.statuses, parent=data.parent_corpus_context;
        const banner=`<article class="benchmark ${benchmark.overall_benchmark_passed?'pass':''}"><h3>Проверяемый публикационный ретротест</h3><p>${esc(benchmark.conclusion)}</p><div class="summary"><span class="pill">Работ: ${benchmark.corpus.canonical_works}</span><span class="pill">Тематических линий: ${benchmark.discovery.topic_lines}</span><span class="pill">Наблюдать: ${statuses.watch||0}</span><span class="pill">Требуют проверки: ${statuses.candidate||0}</span><span class="pill">Распространённые: ${statuses.widespread||0}</span></div><div class="summary">${checks}</div><div class="note">Статус «Наблюдение» означает: научная динамика видна, но обязательные основания ещё неизвестны. Это не прогноз рынка и не подтверждённый тренд.</div></article>`;
        const backdrop=parent?`<article><h3>Знаменатель и покрытие</h3><p>${esc(parent.interpretation)}</p><div class="summary"><span class="pill">arXiv ID в периоде: ${parent.counts.parent_native_ids_in_period}</span><span class="pill">Совпало с запросом: ${parent.counts.phrase_scope_native_ids_in_period}</span><span class="pill">Сопоставимость: подтверждена внутри frozen snapshot</span></div></article>`:'';
        const cards=data.queue.map(item=>{const c=item.card,m=c.metrics.observed;const evidence=c.evidence.slice(0,5).map(renderEvidence).join('');const unknown=item.unknown_checks.map(gateName);const failed=item.failed_checks.map(gateName);return `<article><h3>${item.rank}. ${esc(c.label)}</h3><div class="summary"><span class="pill">${esc(ruStatus(c.status))}</span><span class="pill">Публикаций: ${m.doc_count}</span><span class="pill">Новизна: ${esc(metricText(m,'novelty'))}</span><span class="pill">Динамика: ${esc(metricText(m,'momentum'))}</span><span class="pill">Последовательных лет: ${m.windows_present??'неизвестно'}</span></div><p><b>Первое наблюдение:</b> ${esc(c.first_found)}. ${esc(item.why_in_queue)}</p>${failed.length?`<p><b>Не пройдено:</b> ${failed.map(esc).join(', ')}.</p>`:''}${unknown.length?`<p><b>Нужно проверить:</b> ${unknown.map(esc).join(', ')}.</p>`:''}<details><summary>Проверки, ограничения и статьи</summary>${noveltyContext(m)}${c.gates.map(renderGate).join('')}${c.limitations.map(v=>`<p>${esc(v)}</p>`).join('')}<div class="cards">${evidence}</div></details></article>`}).join('');
        document.getElementById('status').textContent=`Прогон #${data.score_run_id} · публикации до ${data.provenance[0].as_of_date} · полный local arXiv profile v4`;
        document.getElementById('output').innerHTML=banner+backdrop+`<h2>Очередь проверки — не рейтинг успеха</h2><div class="cards">${cards}</div>`;
      } catch(error) { document.getElementById('status').textContent='Ретротест недоступен'; document.getElementById('output').innerHTML=`<div class="error">${esc(error.message)}</div>`; }
    });
    document.getElementById('verified').addEventListener('click',async()=>{
      document.getElementById('status').textContent='Открываю доказательную карточку…';
      try {
        const response=await fetch('/signals/gnn-retro-2017'); const data=await response.json();
        if(!response.ok) throw new Error(data.detail||JSON.stringify(data));
        const card=data.cards.find(c=>c.label.toLowerCase().includes('convolutional'))||data.cards[0];
        if(!card) throw new Error('Кандидатов в этом прогоне нет.');
        const m={...card.metrics.observed,windows_present:card.metrics.observed.windows_present??'неизвестно'};
        const unknown=card.gates.filter(g=>g.passed===null).map(g=>esc(gateName(g.gate))).join(', ');
        const checks=card.gates.map(renderGate).join('');
        const evidence=card.evidence.map(renderEvidence).join('');
        document.getElementById('status').textContent=`Карточка прогона #${data.score_run_id} · правила v${data.provenance?.[0]?.methodology_version||'неизвестны'}`;
        document.getElementById('output').innerHTML=`<article><h3>${esc(ruLabel(card.label))}</h3><div class="summary"><span class="pill">Статус: ${esc(ruStatus(card.status))}</span><span class="pill">Публикаций: ${m.doc_count}</span><span class="pill">Периодов: ${m.windows_present}</span><span class="pill">Индекс качества данных: ${card.evidence_confidence}/100</span></div><p><b>Первое наблюдение:</b> ${esc(card.first_found)} · <b>устойчивая исследовательская линия:</b> ${esc(card.research_birth||'ещё не подтверждена')}</p><p>${card.emergence_score===null?'Итоговый балл не рассчитан: отсутствуют обязательные метрики. Причины — в проверках ниже.':''}</p>${unknown?`<div class="note">Пока неизвестно: ${unknown}</div>`:''}<details><summary>Проверки и ограничения</summary>${checks}${card.limitations.map(s=>`<p>${esc(s)}</p>`).join('')}</details></article><h2>Первоисточники</h2><div class="cards">${evidence}</div>`;
      } catch(error) { document.getElementById('status').textContent='Карточка недоступна'; document.getElementById('output').innerHTML=`<div class="error">${esc(error.message)}</div>`; }
    });
    document.getElementById('search').addEventListener('submit',async e=>{
      e.preventDefault(); const button=document.getElementById('submit'); button.disabled=true;
      document.getElementById('status').textContent='Получаю OpenAlex и arXiv…'; document.getElementById('output').innerHTML='';
      try {
        const payload={query:document.getElementById('query').value,date_from:document.getElementById('from').value,as_of_date:document.getElementById('asof').value,limit_per_source:Number(document.getElementById('limit').value)};
        const response=await fetch('/discover/preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
        const data=await response.json(); if(!response.ok) throw new Error(JSON.stringify(data));
        document.getElementById('status').textContent=`Найдено канонических работ: ${data.works.length}. Запрос: ${data.query_hash}`;
        const errors=Object.entries(data.errors).map(([k,v])=>`<div class="error"><b>${esc(k)}:</b> ${esc(v)}</div>`).join('');
        const summary=`<div class="summary"><span class="pill">OpenAlex: ${data.source_counts.openalex}</span><span class="pill">arXiv: ${data.source_counts.arxiv}</span><span class="pill">срез: строго раньше ${esc(data.as_of_date)}</span></div>`;
        const cards=data.works.map(w=>`<article><h3>${esc(w.title)}</h3><div class="meta">${esc(w.published_at)} · ${w.sources.map(esc).join(' + ')}</div><p>${esc((w.abstract||'Аннотация отсутствует').slice(0,420))}</p><div>${w.urls.map((u,i)=>`<a href="${esc(u)}" target="_blank" rel="noopener">Источник ${i+1}</a>`).join(' · ')}</div></article>`).join('');
        const limitations=(data.limitations||[]).map(v=>`<div class="note">${esc(v)}</div>`).join('');
        document.getElementById('output').innerHTML=summary+errors+limitations+`<div class="cards">${cards}</div>`;
      } catch(error) { document.getElementById('status').textContent='Запрос не выполнен'; document.getElementById('output').innerHTML=`<div class="error">${esc(error.message)}</div>`; }
      finally { button.disabled=false; }
    });
    document.getElementById('embedding-progress').onclick=async()=>{
      const button=document.getElementById('embedding-progress'), state=document.getElementById('embedding-progress-state');
      button.disabled=true;state.textContent='Проверяю сохранённые в базе векторы, не журнал процесса';
      try{
        const url='/corpus/ai-area-2017-v041-openalex-enriched/embedding-status?quality_generation_id=761&model='+encodeURIComponent('specter2/proximity@3447645e+20815596');
        const response=await fetch(url), data=await response.json();
        if(!response.ok)throw new Error(data.detail||'Проверка не выполнена');
        const meaning={complete:'Векторы готовы. Выделение и проверка сигналов — следующие отдельные этапы.',partial:'Вычисление не завершено; строгий семантический анализ ещё не допускается.',pending:'Сохранённых векторов пока нет.',empty:'В заданном периоде нет допущенных публикаций.',invalid:'Есть несогласованность размерностей; анализ заблокирован.'};
        state.textContent=`Корпус #${data.normalize_run_id}, качество #${data.quality_generation_id}. Сохранено ${data.committed_vectors} из ${data.eligible_works}, осталось ${data.remaining_works}. ${meaning[data.state]||'Состояние не распознано.'} ${data.limitations}`;
      }catch(error){state.textContent=error.message;}finally{button.disabled=false;}
    };
    document.getElementById('enriched-retro').onclick=()=>{
      document.getElementById('portfolio-id').value='5a801871-07e6-4ec2-8964-a9e08785bca8';
      document.getElementById('portfolio-assessment').innerHTML='<option value="eae3a848-da19-4b9f-a272-a36cdfed498e">composition-assessment-0.4.2-experimental · корпус #2517 / качество #761</option>';
      document.getElementById('assessment-history-state').textContent='Выбран конкретный сохранённый эксперимент 2010–2016, не поиск текущих сигналов. Другие версии можно открыть через историю.';
      document.getElementById('portfolio-open').requestSubmit();
    };
    document.getElementById('current-signals').onclick=async()=>{
      const button=document.getElementById('current-signals'); button.disabled=true;
      document.getElementById('status').textContent='Открываю последний сохранённый анализ текущего корпуса…';
      try {
        const response=await fetch('/triage/ai-data-selection-current-p1-v047-openalex-enriched?limit=15');
        const data=await response.json(); if(!response.ok) throw new Error(data.detail||JSON.stringify(data));
        const queue=data.queue;
        const counts=data.counts.statuses;
        const passport=data.provenance[0];
        const parent=data.parent_corpus_context;
        const backdrop=parent?`<div class="note"><b>Фон родительского arXiv-корпуса:</b> ${esc(parent.interpretation)} За все 24 месяца наклон доли выбранного среза: ${parent.full_period.phrase_share_slope_per_month??'неизвестен'}; в последних окнах направление числа публикаций: ${esc(parent.recent_window.phrase_count_direction??'неизвестно')}, доли: ${esc(parent.recent_window.phrase_share_direction??'неизвестно')}.</div>`:'';
        document.getElementById('status').textContent=`Сохранённый результат #${data.score_run_id} · публикации до ${passport.as_of_date}`;
        document.getElementById('output').innerHTML=`<article><h3>Текущая очередь проверки</h3><div class="summary"><span class="pill">Микротем: ${data.counts.all_cards}</span><span class="pill">Наблюдать: ${counts.watch||0}</span><span class="pill">Требуют проверки: ${counts.candidate||0}</span><span class="pill">Распространённые: ${counts.widespread||0}</span></div><div class="note">${esc(data.interpretation)} Порядок проверяемый: меньше непройденных обязательных правил → больше пройденных научных правил → меньше неизвестных. Затем используется минимум, а не сумма перцентилей новизны и динамики.</div>${backdrop}</article><div class="cards">${queue.map(item=>{const c=item.card,m=c.metrics.observed;const unknown=item.unknown_checks.map(gateName);const failed=item.failed_checks.map(gateName);return `<article><h3>${item.rank}. ${esc(c.label)}</h3><div class="summary"><span class="pill">${esc(ruStatus(c.status))}</span><span class="pill">Публикаций: ${m.doc_count}</span><span class="pill">Новизна: ${m.novelty_percentile??'неизвестно'}</span><span class="pill">Динамика: ${m.momentum_percentile??'неизвестно'}</span><span class="pill">Последовательных месяцев: ${m.windows_present??'неизвестно'}</span></div><p>${esc(item.why_in_queue)}</p>${failed.length?`<p><b>Не пройдено:</b> ${failed.map(esc).join(', ')}.</p>`:''}${unknown.length?`<p><b>Нужно проверить:</b> ${unknown.map(esc).join(', ')}.</p>`:''}<details><summary>Основания, ограничения и статьи</summary>${c.gates.map(renderGate).join('')}${c.limitations.map(v=>`<p>${esc(v)}</p>`).join('')}<div class="cards">${c.evidence.slice(0,3).map(renderEvidence).join('')}</div></details><details><summary>Сохранить отдельное мнение аналитика</summary><form class="score-review" data-candidate="${c.candidate_id}"><label>Решение<select name="decision"><option value="needs_review">Нужна дополнительная проверка</option><option value="research_line_supported">Исследовательская линия подтверждается</option><option value="noise">Шум или несвязный состав</option><option value="possible_duplicate">Возможный дубль</option><option value="insufficient_evidence">Недостаточно доказательств</option></select></label><label>Имя аналитика<input name="reviewed_by" required maxlength="120"></label><label>Обоснование<textarea name="rationale" required minlength="20" maxlength="5000"></textarea></label><label>HTTPS-ссылки, по одной на строку<textarea name="sources" maxlength="10000"></textarea></label><button type="submit">Сохранить мнение</button><p class="review-state meta"></p><div class="note">Мнение не меняет автоматический статус. Личность пока указывается самим пользователем и не аутентифицируется.</div></form></details></article>`}).join('')}</div>`;
        document.querySelectorAll('.score-review').forEach(form=>form.addEventListener('submit',async event=>{
          event.preventDefault(); const state=form.querySelector('.review-state'),button=form.querySelector('button'); button.disabled=true; state.textContent='Сохраняю…';
          const raw=form.elements.sources.value.trim(); const sources=raw?raw.split(/\n|,/).map(v=>v.trim()).filter(Boolean):[];
          const payload={decision:form.elements.decision.value,reviewed_by:form.elements.reviewed_by.value,rationale:form.elements.rationale.value,sources};
          try{const response=await fetch(`/signals/ai-data-selection-current-p1-v047-openalex-enriched/${form.dataset.candidate}/reviews?score_run_id=${data.score_run_id}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const saved=await response.json();if(!response.ok)throw new Error(saved.detail||JSON.stringify(saved));state.textContent=`Мнение сохранено ${saved.created_at}. Автоматический статус не изменён.`;form.elements.rationale.value='';}
          catch(error){state.textContent=error.message;}finally{button.disabled=false;}
        }));
      } catch(error) {
        document.getElementById('output').innerHTML=`<div class="error">${esc(error.message)}</div>`;
      } finally { button.disabled=false; }
    };
  </script>
</body></html>'''

from saia.portfolio_web import PORTFOLIO_JS

WEB_APP_HTML = WEB_APP_HTML.replace('</script>', PORTFOLIO_JS + '</script>')

from saia.help_web import attach_help_widget

WEB_APP_HTML = attach_help_widget(WEB_APP_HTML)
