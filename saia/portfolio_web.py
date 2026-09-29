"""Dependency-free accessible browser views for a pinned candidate portfolio."""

PORTFOLIO_JS = r'''
    let portfolioData=null,portfolioPage=0,currentPortfolioCandidate=null,currentReviewHistory=null;
    const reviewAttemptKeys=new Map();
    const gateValue=v=>v==null?'неизвестно':esc(typeof v==='boolean'?(v?'да':'нет'):v);
    function publicationPanel(c){
      const o=c.publication_observation;if(!o)return '';
      const v=o.observed;
      return `<section><h4>Автоматический отбор по публикационным признакам</h4><p>${esc(o.display_name)}. Не является подтверждённым слабым сигналом.</p><p>Полных окон: ${v.used_full_windows} из необходимых ${v.required_full_windows}; последовательных активных: ${gateValue(v.consecutive_active_windows)}.</p><p>Количество работ: изменение ${gateValue(v.count_change)}; изменение доли ${v.share_change==null?'неизвестно':`${(v.share_change*100).toFixed(4)} п.п.`}.</p><p>Различимые авторские группы: ${gateValue(o.diffusion.distinct_author_groups_proxy)}; организации: ${gateValue(o.diffusion.distinct_organisations_proxy)}. Расширение диффузии во времени здесь ещё не измерено.</p>${o.limitations.map(v=>`<p>${esc(v)}</p>`).join('')}<details><summary>Паспорт описательного отбора</summary><p>${esc(o.version)} · ${esc(o.policy_hash)}</p></details></section>`;
    }
    function assessmentPanel(c){
      if(!c.assessment)return '';
      return `<section><h4>Проверки выбранной сохранённой оценки</h4><p>Статус: ${esc(ruStatus(c.status))}. Балл: ${gateValue(c.assessment.emergence_score)}. Качество библиографических оснований: ${gateValue(c.assessment.evidence_confidence)} — не вероятность истинности сигнала.</p>
        <div style="overflow:auto"><table><thead><tr><th>Проверка</th><th>Результат</th><th>Наблюдение / порог</th><th>Объяснение</th></tr></thead>
        <tbody>${(c.display_gates||c.assessment.gates).map(g=>`<tr><td>${esc(g.display_name||g.gate)}</td><td>${g.passed==null?'Не установлено':g.passed?'Пройдена':'Не пройдена'}${g.severity==='block'?' (обязательная)':' (предупреждение)'}</td><td>${gateValue(g.observed)} / ${gateValue(g.threshold)}</td><td>${esc(g.display_explanation||'Пояснение этой версии недоступно; смотрите сохранённое правило ниже.')}</td></tr>`).join('')}</tbody></table></div>
        <details><summary>Измерения и неизвестные данные</summary><pre>${esc(JSON.stringify(c.measured,null,2))}</pre></details>
        <details><summary>Исходные правила сохранённой версии</summary><p>Формулировки причин в старой методике могут описывать случай нарушения даже для пройденной проверки. Результат указан в таблице выше; правила здесь не пересчитаны.</p><pre>${esc(JSON.stringify(c.assessment.gates.map(({display_name,display_explanation,...g})=>g),null,2))}</pre></details></section>`;
    }
    const percent=v=>v==null?'неизвестно':`${(v*100).toFixed(3)}%`;
    const sectorName=v=>({lexical:'Буквальный',semantic:'Семантический',both:'Оба канала'}[v]||v);
    const candidateColour=v=>({lexical:'#215fb5',semantic:'#19734a',both:'#8441a5'}[v]);
    const sourceLinks=items=>(items||[]).filter(s=>typeof s.url==='string'&&s.url.startsWith('https://')).map(s=>`<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.type)}</a>`).join(' · ');
    const pointButton=(c,x,y)=>`<circle cx="${x.toFixed(2)}" cy="${y.toFixed(2)}" r="5" fill="${candidateColour(c.radar_sector)}" stroke="white" role="button" tabindex="0" data-candidate="${esc(c.candidate_id)}" aria-label="Открыть ${esc(c.label)}"><title>${esc(c.label)}</title></circle>`;
    function mapSvg(rows){
      const known=rows.filter(c=>c.map_position!==null),shown=known.slice(0,100);
      if(!known.length)return '<div class="note">На двумерной карте нет точек: изменение доли неизвестно. Кандидаты не потеряны — они доступны в таблице ниже с долей последнего полного окна.</div>';
      const xmax=Math.max(.001,...known.map(c=>c.share)),ys=known.map(c=>c.share_slope_per_window);
      const ymin=Math.min(-.0001,...ys),ymax=Math.max(.0001,...ys),y=v=>260-(v-ymin)/(ymax-ymin)*210;
      return `<p>На карте ${shown.length} из ${known.length} пригодных точек текущего фильтра; первые по алфавиту, не лучшие. Размер одинаковый — не сила сигнала.</p><svg viewBox="0 0 600 330" style="width:100%;max-width:700px" role="group" aria-label="Карта доли и изменения доли"><path d="M70 45 V260 H560" fill="none" stroke="#687b91"/><path d="M70 ${y(0)} H560" stroke="#b9c8db" stroke-dasharray="4 4"/><text x="70" y="285">0%</text><text x="470" y="285">${percent(xmax)}</text><text x="8" y="50">${(ymax*100).toFixed(3)} п.п.</text><text x="8" y="260">${(ymin*100).toFixed(3)} п.п.</text><text x="100" y="315">Доля в последнем полном окне</text><text x="80" y="20">Наклон доли, п.п. за ${portfolioData.window_step==='year'?'годовое':'квартальное'} окно</text>${shown.map(c=>pointButton(c,70+c.share/xmax*490,y(c.share_slope_per_window))).join('')}</svg>`;
    }
    function radarSvg(rows,observed=false){
      const sectors=['lexical','semantic','both'],shown=rows.slice(0,100);
      const circles=sectors.map((sector,k)=>{
        const group=shown.filter(c=>c.radar_sector===sector);
        return group.map((c,i)=>{const angle=(k*120+15+(i+.5)/Math.max(1,group.length)*90)*Math.PI/180,r=observed?({publication_signal_candidate:70,growth_watch:105,single_group_growth:105,stable_or_mixed:140,declining_activity:175,widespread_topic:175}[c.publication_observation.stage]||155):({candidate:70,watch:105,forming:140,widespread:175,mature:175}[c.radar_ring]||155);return pointButton(c,280+r*Math.cos(angle),200+r*Math.sin(angle));}).join('');
      }).join('');
      const rings=observed?[[70,'Публикационный кандидат'],[105,'Наблюдать рост'],[140,'Стабильный / смешанный'],[175,'Затухает / массовый']]:portfolioData.saved_assessment?[[70,'Кандидат'],[105,'Наблюдать'],[140,'Формируется'],[175,'Распространён']]:[[155,'Состав не оценён']];
      return `<p>Радар показывает ${shown.length} из ${rows.length} кандидатов, по алфавиту. Кольца — категории ${observed?'наблюдаемой динамики, не экспертного подтверждения':'выбранной оценки'}, не шкала силы или зрелости; угол — порядок названий.</p><svg viewBox="0 0 570 425" style="width:100%;max-width:600px" role="group" aria-label="Радар происхождения и статусов кандидатов">${rings.map(([r,label])=>`<circle cx="280" cy="200" r="${r}" fill="none" stroke="#b9c8db"/><text x="285" y="${200-r+13}">${esc(label)}</text>`).join('')}<text x="365" y="410">Буквальный</text><text x="10" y="205">Семантический</text><text x="360" y="20">Оба канала</text>${circles}</svg>`;
    }
    function selectedCandidate(id){
      const raw=portfolioData.candidates.find(c=>c.candidate_id===id);if(!raw)return;
      const c={...raw,contexts:raw.contexts.filter((v,i,all)=>all.findIndex(x=>x.work_id===v.work_id&&x.field===v.field&&x.context===v.context)===i)};
      currentPortfolioCandidate=id;currentReviewHistory=null;
      document.getElementById('portfolio-detail').innerHTML=`<article><h3>${esc(c.label)}</h3><p>Кандидат: ${esc(c.candidate_id)}. Состав ещё не оценён; итогового балла нет.</p><p>Работ в составе: ${c.document_support}; впервые наблюдался в этом корпусе: ${esc(c.first_observed_in_corpus)}.</p><p>Доля последнего полного окна: ${percent(c.share)}; изменение: ${c.share_slope_per_window==null?'неизвестно':`${(c.share_slope_per_window*100).toFixed(4)} п.п. за окно`}.</p>${c.map_missing_reasons.concat(c.unknown_checks).map(v=>`<p>${esc(v)}</p>`).join('')}<h4>Ряд публикаций</h4><div style="overflow:auto"><table style="width:100%;text-align:left"><caption>Числа в загруженном корпусе, не размер мирового рынка</caption><thead><tr><th>Начало окна</th><th>Работ темы / корпуса</th><th>Доля</th><th>Сопоставимость</th></tr></thead><tbody>${c.publication_series.points.map(p=>`<tr><td>${esc(p.start)}${p.complete?'':' (неполное)'}</td><td>${p.topic_works} / ${p.corpus_works??'неизвестно'}</td><td>${percent(p.share)}</td><td>${p.coverage_comparable?'подтверждена':'не подтверждена'}</td></tr>`).join('')}</tbody></table></div><h4>Фрагменты публикаций</h4>${c.contexts.length?c.contexts.map(v=>`<p>${esc(v.published_at)} · ${esc(v.context)}</p>${sourceLinks(v.sources)}`).join(''):'<p>В этом снимке нет буквальных фрагментов; отсутствие не восполняется выдуманным описанием.</p>'}<details><summary>Состав и пересечения</summary><p>ID работ: ${c.work_ids.map(esc).join(', ')}.</p>${c.overlap_links.map(l=>`<p>Пересечение с ${esc(l.candidate_id)}: ${l.shared_work_ids.length} работ. Не доказательство синонимии или независимости.</p>`).join('')}</details></article>`;
      if(c.assessment){
        document.getElementById('portfolio-detail').querySelector('p').textContent=`Кандидат: ${c.candidate_id}. Открыта сохранённая оценка: ${ruStatus(c.status)}; новый расчёт не запускался.`;
        document.getElementById('portfolio-detail').insertAdjacentHTML('beforeend',assessmentPanel(c));
      }
      document.getElementById('portfolio-detail').insertAdjacentHTML('afterbegin',publicationPanel(c));
      document.getElementById('portfolio-detail').scrollIntoView({behavior:'smooth',block:'nearest'});
      document.getElementById('portfolio-detail').insertAdjacentHTML('beforeend',`<article><h3>Экспертное мнение</h3><div class="note">Автор указывает имя самостоятельно — учётная запись и квалификация не проверены. Это мнение по сохранённому составу, не истинная метка, консенсус или подтверждение рынка. Время решения — сегодня, не дата исторического среза. Исправление сохраняется новым мнением, старое остаётся.</div><form id="review-form" class="query-form"><label>Автор мнения<input id="review-actor" required maxlength="120"></label><label>Решение<select id="review-decision"><option value="needs_review">Нужна проверка</option><option value="research_line_supported">Есть основания для исследовательской линии</option><option value="noise">Считаю шумом</option><option value="possible_duplicate">Возможный дубликат</option><option value="insufficient_evidence">Недостаточно доказательств</option></select></label><label>Объяснение<textarea id="review-rationale" required minlength="20" maxlength="5000"></textarea></label><label>Источники — HTTPS-ссылка на строку<textarea id="review-sources"></textarea></label><button type="submit" id="review-save">Сохранить отдельное мнение</button></form><p id="review-state" aria-live="polite"></p><h4>История мнений</h4><div id="review-history">Загрузка истории…</div><button type="button" class="secondary" id="review-export" disabled>Скачать показанные мнения (до 100)</button></article>`);
      const sid=portfolioData.snapshot_id;
      document.getElementById('review-form').onsubmit=async e=>{
        e.preventDefault();const button=document.getElementById('review-save');button.disabled=true;
        const payload={decision:document.getElementById('review-decision').value,reviewed_by:document.getElementById('review-actor').value,rationale:document.getElementById('review-rationale').value,sources:document.getElementById('review-sources').value.split('\n').map(s=>s.trim()).filter(Boolean)};
        try{
          const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify(payload))),hash=Array.from(new Uint8Array(bytes)).map(b=>b.toString(16).padStart(2,'0')).join('');
          const key=`saia-review:${sid}:${id}:${hash}`;
          let stored=null;try{stored=sessionStorage.getItem(key);}catch(error){}
          payload.operation_id=stored||reviewAttemptKeys.get(key)||crypto.randomUUID();reviewAttemptKeys.set(key,payload.operation_id);
          try{sessionStorage.setItem(key,payload.operation_id);}catch(error){}
          const response=await fetch(`/hybrid/${encodeURIComponent(sid)}/candidates/${encodeURIComponent(id)}/reviews`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}),r=await response.json();
          if(!response.ok)throw new Error(typeof r.detail==='string'?r.detail:'Проверьте длину объяснения и ссылки');
          if(currentPortfolioCandidate===id&&portfolioData.snapshot_id===sid){document.getElementById('review-state').textContent=`${r.replayed?'Повторная отправка: открыта прежняя запись':'Мнение сохранено'} ${r.review_id}. Автоматические метрики и статус не изменены.`;await loadReviewHistory(sid,id);}
        }catch(error){if(currentPortfolioCandidate===id&&portfolioData.snapshot_id===sid)document.getElementById('review-state').textContent=error.message;}finally{button.disabled=false;}
      };
      document.getElementById('review-export').onclick=()=>{
        if(!currentReviewHistory)return;const url=URL.createObjectURL(new Blob([JSON.stringify(currentReviewHistory,null,2)],{type:'application/json'}));
        const a=document.createElement('a');a.href=url;a.download=`saia-opinions-${sid}.json`;a.click();URL.revokeObjectURL(url);
      };
      loadReviewHistory(sid,id);
    }
    const reviewName=v=>({needs_review:'Нужна проверка',research_line_supported:'Есть основания для исследовательской линии',noise:'Считаю шумом',possible_duplicate:'Возможный дубликат',insufficient_evidence:'Недостаточно доказательств'}[v]||v);
    async function loadReviewHistory(sid,id){
      try{
        const response=await fetch(`/hybrid/${encodeURIComponent(sid)}/candidates/${encodeURIComponent(id)}/reviews?limit=100`),h=await response.json();if(!response.ok)throw new Error(h.detail||'История не прочитана');
        if(currentPortfolioCandidate!==id||portfolioData.snapshot_id!==sid)return;currentReviewHistory=h;
        document.getElementById('review-history').innerHTML=`<p>Показано ${h.returned} из ${h.total} мнений; сначала последние. Нет автоматического консенсуса.</p>${h.reviews.map(r=>`<article><h4>${esc(reviewName(r.decision))}</h4><p>${esc(r.reviewed_by)} (имя не проверено) · ${esc(r.created_at)}</p><p>${esc(r.rationale)}</p>${sourceLinks(r.sources.map(url=>({url,type:'Источник мнения'})))}</article>`).join('')||'<p>Мнений пока нет. Это не отрицательная оценка кандидата.</p>'}`;
        document.getElementById('review-export').disabled=false;
      }catch(error){if(currentPortfolioCandidate===id&&portfolioData.snapshot_id===sid)document.getElementById('review-history').textContent=error.message;}
    }
    function renderPortfolio(){
      const term=document.getElementById('portfolio-filter').value.toLocaleLowerCase(),sector=document.getElementById('portfolio-channel').value;
      const min=Number(document.getElementById('portfolio-min').value)||0;
      const stage=document.getElementById('portfolio-stage')?.value||'';
      const rows=portfolioData.candidates.filter(c=>c.label.toLocaleLowerCase().includes(term)&&(!sector||c.radar_sector===sector)&&c.document_support>=min&&(!stage||c.publication_observation.stage===stage));
      const observedRows=rows.map(c=>({...c,map_position:c.publication_observation.map_position,share:c.publication_observation.observed.last_window_share,share_slope_per_window:c.publication_observation.observed.share_slope_per_window}));
      const pages=Math.max(1,Math.ceil(rows.length/30));portfolioPage=Math.min(portfolioPage,pages-1);
      document.getElementById('portfolio-views').innerHTML=`<h3>Наблюдаемая динамика в корпусе</h3><p>Координаты описывают сохранённую выборку. Не подтверждают сопоставимость источников, причинный рост или истинность слабого сигнала.</p>${mapSvg(observedRows)}<h3>Радар публикационных признаков</h3>${radarSvg(rows,true)}<details><summary>Прежняя строгая карта и радар — сохранённые проверки</summary>${mapSvg(rows)}${radarSvg(rows)}</details><h3>Кандидаты и наблюдения</h3><p>Под фильтрами: ${rows.length} из ${portfolioData.candidates.length}. Страница ${portfolioPage+1} из ${pages}, 30 строк на страницу. Порядок алфавитный, не рейтинг.</p><div style="overflow:auto"><table style="width:100%;text-align:left"><thead><tr><th>Кандидат</th><th>Канал</th><th>Работ</th><th>Доля</th><th>Наблюдаемый наклон доли</th><th>Публикационные признаки</th></tr></thead><tbody>${rows.slice(portfolioPage*30,(portfolioPage+1)*30).map(c=>`<tr><td><button class="secondary" type="button" data-candidate="${esc(c.candidate_id)}">${esc(c.label)}</button></td><td>${sectorName(c.radar_sector)}</td><td>${c.document_support}</td><td>${percent(c.share)}</td><td>${c.publication_observation.observed.share_slope_per_window==null?'неизвестно':`${(c.publication_observation.observed.share_slope_per_window*100).toFixed(4)} п.п./окно`}</td><td>${esc(c.publication_observation.display_name)}</td></tr>`).join('')}</tbody></table></div><button type="button" id="portfolio-prev" ${portfolioPage===0?'disabled':''}>Назад</button> <button type="button" id="portfolio-next" ${portfolioPage>=pages-1?'disabled':''}>Далее</button>`;
      document.getElementById('portfolio-prev').onclick=()=>{portfolioPage--;renderPortfolio();};document.getElementById('portfolio-next').onclick=()=>{portfolioPage++;renderPortfolio();};
      if(portfolioData.saved_assessment){
        const table=document.getElementById('portfolio-views').querySelector('table');
        table.querySelector('thead tr').insertAdjacentHTML('beforeend','<th>Статус сохранённой оценки</th>');
        table.querySelectorAll('tbody tr').forEach((tr,i)=>tr.insertAdjacentHTML('beforeend',`<td>${esc(ruStatus(rows[portfolioPage*30+i].status))}</td>`));
      }
      document.querySelectorAll('[data-candidate]').forEach(el=>{el.onclick=()=>selectedCandidate(el.dataset.candidate);el.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();selectedCandidate(el.dataset.candidate);}};});
    }
    document.getElementById('portfolio-id').addEventListener('input',()=>{document.getElementById('portfolio-assessment').innerHTML='<option value="">Без оценки: исходный состав</option>';document.getElementById('assessment-history-state').textContent='История сброшена: изменён номер снимка';});
    document.getElementById('assessment-history-load').onclick=async()=>{
      const sid=document.getElementById('portfolio-id').value.trim(),button=document.getElementById('assessment-history-load');button.disabled=true;
      try{
        const response=await fetch(`/hybrid/${encodeURIComponent(sid)}/assessment-history`),h=await response.json();if(!response.ok)throw new Error(h.detail||'История не прочитана');
        if(document.getElementById('portfolio-id').value.trim()!==sid)return;
        document.getElementById('portfolio-assessment').innerHTML='<option value="">Без оценки: исходный состав</option>'+h.assessments.map(a=>`<option value="${esc(a.assessment_id)}">${esc(a.version)} · сохранено ${esc(a.created_at)} · ${esc(a.assessment_id)}</option>`).join('');
        document.getElementById('assessment-history-state').textContent=`Оценок показано ${h.returned} из ${h.total}. Выберите версию и откройте карту; дата сохранения — не дата исторического обнаружения.`;
      }catch(error){document.getElementById('assessment-history-state').textContent=error.message;}finally{button.disabled=false;}
    };
    document.getElementById('portfolio-open').addEventListener('submit',async e=>{
      e.preventDefault();const button=document.getElementById('portfolio-submit');button.disabled=true;
      document.getElementById('status').textContent='Читаю выбранный сохранённый снимок; новый анализ не запускается';
      try{
        const id=document.getElementById('portfolio-id').value.trim(),aid=document.getElementById('portfolio-assessment').value,response=await fetch(`/hybrid/${encodeURIComponent(id)}/portfolio${aid?'?assessment_id='+encodeURIComponent(aid):''}`);
        const p=await response.json();if(!response.ok)throw new Error(p.detail||'Снимок не прочитан');portfolioData=p;portfolioPage=0;
        document.getElementById('output').innerHTML=`<section><h2>Карта и радар кандидатов</h2><p>Снимок ${esc(p.snapshot_id)}. Период ${esc(p.period_from)} — строго раньше ${esc(p.period_end_exclusive)}.</p><p>Корпус #${p.provenance.normalize_run_id}, качество #${p.provenance.quality_generation_id}, кластеризация #${p.provenance.cluster_run_id}. SHA снимка: ${esc(p.snapshot_content_sha256)}.</p><div class="note">${p.counts.map_positioned} кандидатов с координатами, ${p.counts.map_unpositioned} без установленной динамики. Расположение не меняет статус. Каналы — способы выделения состава, не независимые лаборатории.</div><div class="summary"><label>Название<input id="portfolio-filter" placeholder="Часть названия"></label><label>Канал<select id="portfolio-channel"><option value="">Все</option><option value="lexical">Буквальный</option><option value="semantic">Семантический</option><option value="both">Оба</option></select></label><label>Минимум работ<input id="portfolio-min" type="number" min="0" value="0"></label></div><div id="portfolio-views"></div><div id="portfolio-detail" aria-live="polite"></div><details><summary>Ограничения методики</summary>${p.limitations.map(v=>`<p>${esc(v)}</p>`).join('')}</details></section>`;
        document.getElementById('output').querySelector('.note').textContent=`Наблюдаемые координаты в корпусе: ${p.publication_observations.map_positioned}. Отдельный прежний строгий паспорт: ${p.counts.map_positioned} с подтверждённой динамикой, ${p.counts.map_unpositioned} без неё. Сопоставимость покрытия не подменяется наблюдаемым ростом.`;
        const stages=p.publication_observations.stages,stageLabel=k=>p.candidates.find(c=>c.publication_observation.stage===k)?.publication_observation.display_name||k;
        document.getElementById('portfolio-min').closest('.summary').insertAdjacentHTML('beforeend',`<label>Публикационные признаки<select id="portfolio-stage"><option value="">Все наблюдения</option>${Object.keys(stages).map(k=>`<option value="${esc(k)}">${esc(stageLabel(k))} (${stages[k]})</option>`).join('')}</select></label>`);
        document.getElementById('portfolio-views').insertAdjacentHTML('beforebegin',`<section><h3>Автоматический отбор и подтверждение — разные результаты</h3><p>${Object.keys(stages).map(k=>`${esc(stageLabel(k))}: ${stages[k]}`).join(' · ')}</p><p>${esc(p.publication_observations.interpretation)} Слой ${esc(p.publication_observations.version)}; ${p.publication_observations.map_positioned} наблюдений с координатами в корпусе.</p></section>`);
        if(p.publication_observations.long_window_candidates_with_recent_decline)document.getElementById('portfolio-views').insertAdjacentHTML('beforebegin',`<div class="note">У ${p.publication_observations.long_window_candidates_with_recent_decline} кандидатов старого отбора наблюдается недавнее снижение количества или доли публикаций. Положительный наклон за длинный период не означает текущий рост. Подробности — в карточках; прежние решения не переписаны.</div>`);
        if(p.saved_assessment)document.getElementById('portfolio-views').insertAdjacentHTML('beforebegin',`<p>Отдельная строгая оценка ${esc(p.saved_assessment.assessment_id)}, методика ${esc(p.saved_assessment.version)}. SHA оценки: ${esc(p.saved_assessment.content_sha256)}. Время сохранения ${esc(p.saved_assessment.created_at)}. Её статусы не переписаны.</p>`);
        ['portfolio-filter','portfolio-channel','portfolio-min','portfolio-stage'].forEach(id=>document.getElementById(id).addEventListener('input',()=>{portfolioPage=0;renderPortfolio();}));renderPortfolio();document.getElementById('status').textContent='Наблюдения показаны отдельно; сохранённые оценки не изменены';
      }catch(error){document.getElementById('status').textContent='Не удалось открыть карту';document.getElementById('output').innerHTML=`<div class="error">${esc(error.message)}</div>`;}finally{button.disabled=false;}
    });
'''
