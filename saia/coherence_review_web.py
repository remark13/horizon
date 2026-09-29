"""Separate blinded evaluation screen; it never gates scout results."""

COHERENCE_REVIEW_HTML = r'''<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Horizon — проверка связности исследований</title><style>
:root{--blue:#15599d;--ink:#193047;--line:#d8e2ec}*{box-sizing:border-box}
body{margin:0;background:#f5f8fb;color:var(--ink);font:15px/1.5 Arial,sans-serif}
header{background:#123e70;color:white;padding:24px max(20px,calc((100% - 1050px)/2))}
header h1{margin:0;font-size:24px}header p{margin:6px 0 0;opacity:.9}
main{max-width:1050px;margin:auto;padding:18px 20px 60px}section,article{background:white;
border:1px solid var(--line);border-radius:10px;margin:12px 0;padding:18px}
.pair{display:grid;grid-template-columns:1fr 1fr;gap:14px}.paper{background:#f6f9fd;
border:1px solid var(--line);border-radius:8px;padding:12px}.paper h3{font-size:16px;margin:0 0 8px}
.paper p{white-space:pre-wrap;max-height:260px;overflow:auto}.muted{color:#5e7287}
label{display:block;margin:12px 0;font-weight:600}input,select,textarea{display:block;width:100%;
border:1px solid #aebfce;border-radius:6px;padding:9px;margin-top:4px;font:inherit}
textarea{min-height:65px}button{background:var(--blue);color:white;border:0;border-radius:7px;
padding:11px 18px;font-weight:bold;cursor:pointer}button:disabled{opacity:.55}
a{color:var(--blue)}.error{color:#a12424}.ok{color:#116b43}.questions{display:grid;
grid-template-columns:repeat(3,1fr);gap:12px}@media(max-width:750px){.pair,.questions{grid-template-columns:1fr}}
[hidden]{display:none!important}.navigation{display:flex;justify-content:space-between;align-items:center;gap:12px}
</style></head><body><header><h1>Проверка связности двух исследований</h1>
<p>Отдельная слепая оценка для улучшения Horizon. Обычная выдача кандидатов не зависит от заполнения.</p></header>
<main><section><p><a href="/scout">← К поиску</a></p><p>Прочитайте название и аннотацию обеих статей.
Ответьте на три разных вопроса: общая задача, родственный метод и одна узкая технологическая линия.
Если текста недостаточно, выберите «неясно». Статусы и оценки системы здесь скрыты.</p>
<label>Ваш код или имя<input id="reviewer" maxlength="120" required></label>
<label><input id="independent" type="checkbox" style="display:inline;width:auto;margin-right:8px">
Я заполняю анкету самостоятельно, без просмотра оценок других рецензентов</label>
<p id="packet-status" class="muted">Загрузка пакета…</p></section>
<form id="form" novalidate><div id="cases"></div><section><div class="navigation"><button id="previous" type="button">← Предыдущая</button><span id="progress">—</span><button id="next" type="button">Следующая →</button></div><p></p><button id="submit" type="submit">Проверить и скачать ответы</button>
<p id="status" aria-live="polite"></p><p class="muted">Ответы не отправляются экспертам и не сохраняются в Horizon:
после проверки скачайте файл и передайте его ответственному за исследование.</p></section></form></main>
<script>
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const labels={yes:'Да',no:'Нет',unclear:'Неясно по тексту'};let packet=null,current=0;
function options(){return '<option value="">Выберите</option>'+Object.entries(labels).map(([v,l])=>`<option value="${v}">${l}</option>`).join('')}
function paper(item,side){const p=item['paper_'+side];return `<div class="paper"><h3>Статья ${side.toUpperCase()}: ${esc(p.title)}</h3><p>${esc(p.abstract)}</p><a href="${esc(p.source_url)}" target="_blank" rel="noopener">Открыть первоисточник</a></div>`}
function render(item,i){const qs=packet.review_instructions.questions;return `<article data-case="${esc(item.case_id)}"><h2>${i+1} из ${packet.cases.length}</h2><div class="pair">${paper(item,'a')}${paper(item,'b')}</div><div class="questions">${Object.entries(qs).map(([key,q])=>`<label>${esc(q)}<select data-question="${esc(key)}" required>${options()}</select></label>`).join('')}</div><div class="pair"><label>Дословный фрагмент статьи A<input data-quote="a" required maxlength="500"></label><label>Дословный фрагмент статьи B<input data-quote="b" required maxlength="500"></label></div><label>Почему вы так решили?<textarea data-rationale required minlength="20" maxlength="3000"></textarea></label></article>`}
function show(index){const items=[...document.querySelectorAll('article[data-case]')];if(!items.length)return;current=Math.max(0,Math.min(index,items.length-1));items.forEach((el,i)=>el.hidden=i!==current);document.getElementById('progress').textContent=`Пара ${current+1} из ${items.length}`;document.getElementById('previous').disabled=current===0;document.getElementById('next').disabled=current===items.length-1;window.scrollTo(0,0)}
document.getElementById('previous').onclick=()=>show(current-1);document.getElementById('next').onclick=()=>show(current+1);
async function load(){try{const r=await fetch('/coherence-review/packet',{cache:'no-store'});const p=await r.json();if(!r.ok)throw Error(p.detail||'Пакет недоступен');packet=p;document.getElementById('packet-status').textContent=`Закреплённый пакет: ${p.cases.length} пар. SHA-256: ${p.packet_sha256}. Результат этой анкеты не является оценкой точности топ-15.`;document.getElementById('cases').innerHTML=p.cases.map(render).join('');show(0)}catch(e){document.getElementById('packet-status').className='error';document.getElementById('packet-status').textContent=e.message;document.getElementById('submit').disabled=true}}
function download(data){const a=document.createElement('a'),url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));a.href=url;a.download='saia-coherence-review-'+data.reviewer_id.replace(/[^a-zA-Z0-9_-]/g,'_')+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
document.getElementById('form').addEventListener('submit',async e=>{e.preventDefault();const button=document.getElementById('submit'),status=document.getElementById('status');button.disabled=true;try{if(!packet)throw Error('Пакет не загружен');const answers=[...document.querySelectorAll('article[data-case]')].map(el=>({case_id:el.dataset.case,...Object.fromEntries([...el.querySelectorAll('select[data-question]')].map(x=>[x.dataset.question,x.value])),evidence_quote_a:el.querySelector('[data-quote="a"]').value,evidence_quote_b:el.querySelector('[data-quote="b"]').value,rationale:el.querySelector('[data-rationale]').value}));const incomplete=answers.findIndex(a=>!a.same_research_problem||!a.same_technical_mechanism||!a.one_signal_line||!a.evidence_quote_a.trim()||!a.evidence_quote_b.trim()||a.rationale.trim().length<20);if(incomplete>=0){show(incomplete);throw Error(`Заполните пару ${incomplete+1}: три ответа, два фрагмента статей и объяснение.`)}const payload={packet_sha256:packet.packet_sha256,reviewer_id:document.getElementById('reviewer').value,independent_review_declared:document.getElementById('independent').checked,answers};const r=await fetch('/coherence-review/validate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const result=await r.json();if(!r.ok)throw Error(result.detail||'Анкета не прошла проверку');download(result);status.className='ok';status.textContent='Анкета проверена и скачана. Это отдельное мнение, не подтверждение слабых сигналов.'}catch(error){status.className='error';status.textContent=error.message}finally{button.disabled=false}});load();
</script></body></html>'''
