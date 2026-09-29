"""Horizon scout UI. All candidate content comes from saved APIs."""

SCOUT_WEB_HTML = r'''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Horizon — рабочее место скаута</title>
<style>
:root{--blue:#3156ad;--blue-dark:#233f89;--ink:#202b43;--muted:#68758d;--line:#dfe5ef;--soft:#f5f7fb;--white:#fff;--pale:#edf2fc;--ok:#20745a;--amber:#a66013;--red:#aa4350;--shadow:0 12px 34px rgba(29,50,94,.08)}
*{box-sizing:border-box}html{font-family:Inter,Arial,sans-serif;color:var(--ink);background:var(--soft)}body{margin:0}button,input,select,textarea{font:inherit}button{cursor:pointer}button:disabled{cursor:not-allowed;opacity:.55}a{color:var(--blue);text-decoration:none}a:hover{text-decoration:underline}
.top{height:68px;display:flex;align-items:center;justify-content:space-between;padding:0 36px;background:white;border-bottom:1px solid var(--line)}.brand{display:flex;align-items:center;gap:16px}.brand-mark{font-size:23px;font-weight:800;letter-spacing:-1.3px;color:var(--blue-dark)}.brand-line{width:1px;height:28px;background:var(--line)}.brand-product{font-size:17px;font-weight:750;letter-spacing:.02em}.top-right{display:flex;align-items:center;gap:16px;font-size:13px;color:var(--muted)}
.shell{display:grid;grid-template-columns:220px minmax(0,1fr);min-height:calc(100vh - 68px)}.side{background:white;border-right:1px solid var(--line);padding:28px 14px;display:flex;flex-direction:column;gap:8px}.side-label{font-size:11px;font-weight:800;letter-spacing:.1em;text-transform:uppercase;color:#96a2b5;margin:0 14px 12px}.nav{display:block;border:0;border-radius:10px;background:transparent;padding:12px 14px;text-align:left;color:#526079;font-weight:650;text-decoration:none}.nav.active{background:var(--pale);color:var(--blue-dark)}.nav:focus-visible,.button:focus-visible,.row:focus-visible{outline:3px solid #8aa7ee;outline-offset:2px}.side-bottom{margin-top:auto;padding:14px;color:var(--muted);font-size:12px;line-height:1.5;border-top:1px solid var(--line)}
.content{max-width:1500px;width:100%;margin:0 auto;padding:31px 38px 70px}.eyebrow{text-transform:uppercase;letter-spacing:.1em;font-size:11px;font-weight:800;color:var(--blue)}h1{font-size:29px;letter-spacing:-.025em;margin:7px 0 7px}h2{font-size:20px;letter-spacing:-.02em;margin:0}h3{font-size:15px;margin:0}.lead{color:var(--muted);font-size:14px;margin:0 0 25px}.panel{background:white;border:1px solid var(--line);border-radius:18px;box-shadow:var(--shadow)}.search-panel{padding:22px 24px}.search-row{display:grid;grid-template-columns:minmax(300px,1fr) auto;gap:12px}.field{min-height:46px;border:1px solid #cbd5e5;border-radius:10px;padding:10px 13px;background:white;color:var(--ink);outline:none}.field:focus{border-color:var(--blue);box-shadow:0 0 0 3px #dce6ff}.button{border:0;background:var(--blue);color:white;border-radius:10px;padding:11px 18px;font-weight:700;min-height:43px}.button:hover{background:var(--blue-dark)}.button.light{background:#edf2fc;color:var(--blue-dark)}.button.light:hover{background:#e0e9fb}.button.ghost{background:white;color:var(--blue-dark);border:1px solid var(--line)}.button.small{padding:7px 11px;min-height:34px;font-size:12px}.search-meta{font-size:12px;color:var(--muted);margin:12px 0 0}.preview{margin-top:18px;padding:18px;border:1px solid var(--line);border-radius:12px;background:#fafcff}.preview p{font-size:13px;color:var(--muted)}.preview details{margin:12px 0}.preview summary{cursor:pointer;color:var(--blue-dark);font-size:13px;font-weight:650}.preview .button{margin-top:4px}.branches{display:grid;grid-template-columns:repeat(auto-fill,minmax(225px,1fr));gap:8px;margin:12px 0}.branch{display:flex;align-items:center;gap:8px;background:white;padding:9px;border:1px solid var(--line);border-radius:8px;font-size:12px}.branch input{accent-color:var(--blue)}
.notice{padding:13px 16px;border-radius:11px;font-size:13px;line-height:1.5;margin:18px 0;background:#eaf1ff;color:#294b8c;border:1px solid #cfdcf8}.notice.error{background:#fff0ef;color:#8c3332;border-color:#f3cecb}.notice.warning{background:#fff7eb;color:#785319;border-color:#efdaba}.hidden{display:none!important}
.status-summary{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 14px}.status-summary span{display:inline-flex;align-items:center;gap:5px;padding:7px 10px;border-radius:9px;background:#fff;border:1px solid var(--line);color:#53617a;font-size:12px}.status-summary strong{color:var(--ink);font-size:13px}
.results-head{display:flex;align-items:flex-end;justify-content:space-between;gap:20px;margin:30px 0 16px}.results-caption{font-size:13px;color:var(--muted);margin-top:5px}.results-actions{display:flex;gap:10px}.filters{display:flex;gap:9px;margin:0 0 13px;align-items:center}.filter{height:36px;background:white;border:1px solid var(--line);border-radius:9px;padding:0 12px;color:#46536c;font-size:13px}.filter-label{font-size:12px;color:var(--muted);font-weight:650;margin-right:3px}.retrieval-fallback{margin-top:16px;padding:20px 24px}.retrieval-fallback h3{margin-bottom:8px}.retrieval-fallback p{font-size:13px;color:var(--muted);line-height:1.5;margin:6px 0}
.table-wrap{overflow-x:auto}.table{width:100%;border-collapse:collapse;min-width:930px}.table th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.07em;color:#8792a5;font-weight:800;padding:15px 12px;background:#fafbfd;border-bottom:1px solid var(--line)}.table th:first-child,.table td:first-child{padding-left:23px;width:38px}.table th:last-child,.table td:last-child{padding-right:24px}.table td{padding:17px 12px;border-bottom:1px solid #edf0f5;font-size:13px;vertical-align:middle}.table tr:last-child td{border-bottom:0}.table tbody tr:hover{background:#f9fbff}.table input[type=checkbox]{accent-color:var(--blue);width:16px;height:16px}.signal-name{font-weight:750;font-size:14px;color:#243550;line-height:1.35;display:block;text-align:left;background:none;border:0;padding:0}.signal-name:hover{color:var(--blue);text-decoration:underline}.signal-sub{font-size:11px;color:var(--muted);margin-top:5px}.badge{display:inline-flex;align-items:center;border-radius:100px;padding:5px 9px;background:#eaf0ff;color:#3556a3;font-size:11px;font-weight:750;white-space:nowrap}.badge.amber{background:#fff3df;color:#975a0c}.badge.green{background:#e4f4ef;color:#227251}.badge.gray{background:#eef0f4;color:#627188}.badge.red{background:#fff0ef;color:#a33e4b}.muted{color:var(--muted)}.metric{font-weight:750;color:#293b62}.metric.missing{font-weight:500;color:#94a0b2}.pager{display:flex;align-items:center;justify-content:space-between;padding:16px 22px;border-top:1px solid var(--line);font-size:12px;color:var(--muted)}.pager-buttons{display:flex;gap:8px}.selection{position:sticky;bottom:18px;background:#203d83;color:white;border-radius:14px;box-shadow:0 16px 40px #122b6070;display:flex;align-items:center;justify-content:space-between;gap:15px;padding:12px 16px;max-width:560px;margin:16px 0 0 auto;z-index:4;font-size:13px}.selection .button{background:white;color:var(--blue-dark)}
.overlay{position:fixed;inset:0;background:rgba(21,34,60,.3);z-index:10}.drawer{position:fixed;right:0;top:0;bottom:0;width:min(570px,100vw);overflow-y:auto;background:white;box-shadow:-20px 0 55px rgba(12,31,67,.15);z-index:11}.drawer-top{position:sticky;top:0;background:white;z-index:2;border-bottom:1px solid var(--line);padding:18px 26px;display:flex;align-items:center;justify-content:space-between}.close{border:0;background:#f0f3f9;border-radius:9px;width:34px;height:34px;font-size:21px;color:#596982}.drawer-body{padding:22px 26px 55px}.drawer h2{font-size:24px;line-height:1.25;margin:12px 0 8px}.drawer-sub{color:var(--muted);font-size:13px;line-height:1.55}.stat-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:20px 0}.stat{background:#f5f7fc;border-radius:10px;padding:12px}.stat strong{display:block;font-size:17px;margin-top:4px}.stat span{font-size:11px;color:var(--muted)}.block{border-top:1px solid var(--line);padding:20px 0}.block h3{margin-bottom:10px}.block p{font-size:13px;line-height:1.55;color:#5b6980}.source{padding:11px 0;border-bottom:1px solid #edf0f5}.source:last-child{border:0}.source-title{display:block;font-size:13px;font-weight:700;line-height:1.45}.source-meta{font-size:11px;color:var(--muted);margin:5px 0}.empty{border:1px dashed #cfd8e9;background:#fafbfd;border-radius:10px;padding:12px 14px;color:var(--muted);font-size:12px;line-height:1.5}.bar-row{display:flex;align-items:center;gap:8px;margin:5px 0;font-size:11px;color:var(--muted)}.bar-label{width:66px;flex:none}.bar-track{height:8px;background:#e8edf5;border-radius:8px;flex:1}.bar-fill{height:100%;background:#4f71c1;border-radius:8px;min-width:2px}.bar-val{width:120px;text-align:right}.form-grid{display:grid;gap:10px}.form-grid label{font-size:12px;font-weight:700;color:#56647c}.form-grid .field{width:100%;margin-top:5px}.form-grid textarea{min-height:86px;resize:vertical}.form-note{font-size:12px;color:var(--muted);line-height:1.5}.queue-list{display:grid;gap:12px}.queue-item{padding:18px}.queue-item .meta{font-size:12px;color:var(--muted);margin:8px 0}.queue-item .items{font-size:13px;line-height:1.6}.queue-item .items button{background:none;border:0;color:var(--blue);padding:0;text-align:left;cursor:pointer}.queue-item .items button:hover{text-decoration:underline}
.row-actions{margin-top:10px}.dispatch-candidates{padding-left:20px;font-size:13px;line-height:1.5;color:var(--ink)}.dispatch-candidates li{margin:7px 0}.dispatch-success{border:1px solid #b9ddce;background:#f2faf6;border-radius:12px;padding:18px;margin-top:18px}.dispatch-success h3{color:var(--ok);margin-bottom:8px}
@media(max-width:900px){.shell{grid-template-columns:1fr}.side{display:none}.content{padding:24px 16px}.top{padding:0 16px}.top-right{display:none}.results-head{display:block}.results-actions{margin-top:12px}.search-row{grid-template-columns:1fr}}
@media(max-width:1280px){.table{display:block;min-width:0;width:100%}.table thead{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}.table tbody{display:grid;width:100%;gap:12px;padding:12px;background:#f8faff}.table tbody tr{display:grid;width:100%;grid-template-columns:30px repeat(2,minmax(0,1fr));gap:10px 12px;padding:15px;border:1px solid var(--line);border-radius:12px;background:white}.table tbody td{display:block;padding:0!important;border:0;min-width:0}.table tbody td:first-child{grid-column:1;grid-row:1}.table tbody td:nth-child(2){grid-column:2/4;grid-row:1}.table tbody td:nth-child(3){grid-column:2/4;grid-row:2}.table tbody td:nth-child(4){grid-column:2;grid-row:3}.table tbody td:nth-child(5){grid-column:3;grid-row:3}.table tbody td:nth-child(6){grid-column:2/4;grid-row:4}.table tbody td[colspan]{grid-column:1/4;grid-row:1}.table tbody td[data-label]:not(:nth-child(2))::before{content:attr(data-label);display:block;margin-bottom:5px;color:#8792a5;font-size:10px;font-weight:800;letter-spacing:.06em;text-transform:uppercase}.table tbody tr:hover{background:white}}
@media(max-width:560px){.table tbody td:nth-child(4),.table tbody td:nth-child(5),.table tbody td:nth-child(6){grid-column:2/4}.table tbody td:nth-child(4){grid-row:3}.table tbody td:nth-child(5){grid-row:4}.table tbody td:nth-child(6){grid-row:5}.pager{align-items:flex-start;gap:12px}.pager-buttons{flex-wrap:wrap;justify-content:flex-end}.filters{flex-wrap:wrap}}
</style></head><body>
<header class="top"><div class="brand"><span class="brand-mark">Газпромбанк</span><span class="brand-line"></span><span class="brand-product">Horizon</span></div><div class="top-right"><span>Рабочее место технологического скаута</span></div></header>
<div class="shell"><aside class="side"><div class="side-label">Исследование</div><button class="nav active" id="nav-signals">Слабые сигналы</button><button class="nav" id="nav-experts">Экспертиза</button><button class="nav" id="nav-radar">Технологический радар</button><a class="nav" href="/public-signals">Публичные подборки</a><button class="nav" id="nav-sources">Источники данных</button><a class="nav" href="/help" data-horizon-help="quick-start">Справка</a></aside>
<main class="content"><div id="signals-view"><h1>Поиск слабых сигналов</h1><p class="lead">Введите технологическое направление на русском языке.</p>
<section class="panel search-panel"><form id="search-form"><div class="search-row"><input class="field" id="query" minlength="2" maxlength="160" placeholder="Например, беспилотные авиационные системы" aria-label="Технологическое направление"><button class="button" id="search-submit" type="submit">Найти сигналы</button></div></form><details class="source-options"><summary>Источники исследования</summary><p>Научные публикации — OpenAlex и arXiv. Новости, патенты и инвестиционные сведения автоматически дополняют оценку видимых кандидатов. Другие источники можно выбрать для карточки.</p><button class="button ghost small" id="open-sources" type="button">Выбрать источники</button></details><div id="preview" class="preview hidden"></div></section>
<div id="notice" class="notice hidden" role="status"></div><div class="results-head"><div><h2 id="results-title">Кандидаты в слабые сигналы</h2></div><div class="results-actions"><button class="button ghost small" id="open-radar">Радар</button><button class="button ghost small" id="export-results">Выгрузить</button><button class="button light small" id="reload" disabled>Обновить</button></div></div><div id="status-summary" class="status-summary hidden" aria-label="Состав выдачи"></div><div class="filters"><span class="filter-label">Показать</span><select id="strength" class="filter" aria-label="Фильтр по состоянию"><option value="all">Все темы</option><option value="growth_observed">Признаки роста</option><option value="mixed_evidence">Есть противоречия</option><option value="insufficient_data">Данных недостаточно</option><option value="growth_not_confirmed">Рост не подтверждён</option></select></div>
<section class="panel"><div class="table-wrap"><table class="table"><thead><tr><th><input id="select-page" type="checkbox" aria-label="Выбрать видимые темы"></th><th>Тема исследований</th><th>Состояние</th><th title="Полнота имеющихся оснований, не вероятность подтверждения сигнала">Полнота оснований</th><th>Первая работа в выборке</th><th title="Если покрытие периодов не проверено, показывается только изменение числа найденных работ, а не подтверждённый рост темы">Динамика публикаций</th></tr></thead><tbody id="rows"><tr><td colspan="6" class="muted">Загрузка…</td></tr></tbody></table></div><div class="pager"><span id="page-label">—</span><div class="pager-buttons"><button class="button ghost small" id="prev">← Предыдущие</button><button class="button ghost small" id="next">Следующие 15 →</button></div></div></section><section id="retrieval-fallback" class="panel retrieval-fallback hidden" aria-live="polite"></section><div id="selection" class="selection hidden"><span id="selection-count">Выбрано: 0</span><button class="button small" id="dispatch-open">Передать на экспертизу</button></div></div>
<div id="experts-view" class="hidden"><h1>Экспертиза <a class="help-context" href="/help#expertise" data-horizon-help="expertise" aria-label="Справка об экспертизе">?</a></h1><p class="lead">Кандидаты, переданные на проверку, и мнения экспертов.</p><div id="requests" class="queue-list"></div></div></main></div>
<div id="overlay" class="overlay hidden"></div><aside id="drawer" class="drawer hidden" role="dialog" aria-modal="true" aria-labelledby="drawer-title"><div class="drawer-top"><strong id="drawer-kicker">Карточка сигнала</strong><button class="close" id="drawer-close" aria-label="Закрыть">×</button></div><div class="drawer-body" id="drawer-body"></div></aside>
<script>
(()=>{'use strict';
const state={mission:null,score:null,packet:null,page:0,selected:new Set(),selectedPublic:new Set(),requests:[],current:null,plan:null,view:'signals',job:null,searchBusy:false,retrievedWorks:[],discoveryJob:null};
const pendingKey='saia-scout-pending-search-v1';
const searchWaitLimitMs=20*60*1000;
const pendingSearch=()=>{try{return JSON.parse(sessionStorage.getItem(pendingKey)||'null')}catch{return null}};
const savePending=p=>{try{sessionStorage.setItem(pendingKey,JSON.stringify(p))}catch{}};
const clearPending=()=>{try{sessionStorage.removeItem(pendingKey)}catch{}};
const phraseHints={
  'unmanned-aircraft-systems/uas-airframes':['UAV airframe','drone airframe','morphing UAV','unmanned aerial vehicle design'],
  'unmanned-aircraft-systems/uas-propulsion':['UAV propulsion','drone propulsion','electric UAV','hybrid UAV'],
  'unmanned-aircraft-systems/autonomous-flight-control':['autonomous flight','UAV flight control','autonomous UAV'],
  'unmanned-aircraft-systems/gnss-denied-navigation':['GNSS denied navigation','GPS denied navigation','GNSS-denied UAV','GPS-denied UAV'],
  'unmanned-aircraft-systems/detect-and-avoid':['detect and avoid UAV','UAV collision avoidance','drone obstacle avoidance'],
  'unmanned-aircraft-systems/drone-swarms':['drone swarms','UAV swarms','multi-UAV systems'],
  'unmanned-aircraft-systems/uas-communications':['UAV communication','drone communication','UAV command and control'],
  'unmanned-aircraft-systems/uas-certification':['UAV certification','unmanned aircraft safety','drone airspace integration'],
};
const phrasesFor=s=>s.phrases_en||phraseHints[s.suggestion_id]||[s.query_en];
const $=id=>document.getElementById(id),esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const url=u=>{try{const x=new URL(u);return x.protocol==='https:'||x.protocol==='http:'?x.href:null}catch{return null}};
const request=async(path,options)=>{const r=await fetch(path,options);const x=await r.json();if(!r.ok)throw Error(typeof x.detail==='string'?x.detail:JSON.stringify(x.detail||x));return x};
const post=(path,payload)=>request(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
const notice=(message,type='info')=>{$('notice').className='notice'+(type==='error'?' error':type==='warning'?' warning':'')+(message?'':' hidden');$('notice').textContent=message};
function searchError(error){
  const pending=pendingSearch();
  if(String(error?.message||'')==='SAIA_SEARCH_WAIT_LIMIT'){
    notice('Результат ещё не готов за 20 минут. Задача сохранена; можно вернуться позже или продолжить ожидание.','warning');
    const retry=document.createElement('button');retry.type='button';retry.className='button ghost small';retry.textContent='Продолжить ожидание';retry.style.marginLeft='12px';retry.onclick=()=>{const saved=pendingSearch();if(saved){savePending({...saved,deadline_at:Date.now()+searchWaitLimitMs});resumePending()}};$('notice').append(retry);return;
  }
  const tooBroad=/cohort exceeds|exceeds.*limit|корпус.*превыш/i.test(String(error?.message||''));
  notice(tooBroad?'Запрос охватывает слишком много публикаций. Выберите более узкие темы и повторите поиск. Прежние результаты сохранены.':pending?'Поиск прервался. Его можно продолжить.':'Поиск не завершился. Попробуйте ещё раз.','error');
  if(!pending)return;
  const retry=document.createElement('button');retry.type='button';retry.className='button ghost small';retry.textContent='Продолжить поиск';retry.style.marginLeft='12px';retry.onclick=resumePending;$('notice').append(retry);
}
const setSearchBusy=busy=>{state.searchBusy=busy;$('search-submit').disabled=busy;$('search-submit').textContent=busy?'Поиск идёт…':'Найти сигналы';const launch=$('launch-search');if(launch)launch.disabled=busy};
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const validNumber=v=>typeof v==='number'&&Number.isFinite(v);
const worksWord=n=>n%10===1&&n%100!==11?'работа':n%10>=2&&n%10<=4&&(n%100<12||n%100>14)?'работы':'работ';
const publicationsWord=n=>n%10===1&&n%100!==11?'публикация':n%10>=2&&n%10<=4&&(n%100<12||n%100>14)?'публикации':'публикаций';
const fmtDate=d=>{if(!d)return 'не установлена';const raw=String(d);if(/^\d{4}$/.test(raw))return raw+' г.';if(/^\d{4}-\d{2}$/.test(raw))return raw+' (месяц)';const day=raw.slice(0,10);if(!/^\d{4}-\d{2}-\d{2}$/.test(day))return 'не указана';const parsed=new Date(day+'T12:00:00');return Number.isFinite(parsed.getTime())?new Intl.DateTimeFormat('ru-RU',{day:'2-digit',month:'2-digit',year:'numeric'}).format(parsed):'не указана'};
const observed=c=>c.metrics?.observed||{};
const series=c=>c.metrics?.publication_series||{};
const confidence=c=>validNumber(c.evidence_confidence)?`${Math.round(c.evidence_confidence)}%`:'не рассчитана';
const growth=c=>{const s=series(c);if(s.coverage_comparable===true&&validNumber(s.share_slope_per_window)){const pp=s.share_slope_per_window*100;return `доля ${pp>0?'+':''}${pp.toFixed(2).replace('.',',')} п.п./период`}const count=s.observed_sample?.count;if(count?.available===true&&validNumber(count.change)&&['increasing','decreasing'].includes(count.direction)){const delta=count.change;return `в выборке: ${delta>0?'+':''}${delta} ${worksWord(Math.abs(delta))}`}return 'не установлена'};
const screening=q=>q.screening||{state:'insufficient_data',label:'Данных недостаточно',explanation:'Оценка роста для этой темы не подготовлена.',failed_reasons:[],unknown_reasons:[]};
const badgeClass=q=>screening(q).state==='growth_observed'?'green':screening(q).state==='growth_not_confirmed'?'red':'amber';
const requested=c=>state.requests.some(r=>r.mission_id===state.mission&&r.score_run_id===state.score&&r.candidate_ids.includes(c.candidate_id));
const all=()=>state.packet?.result_items||state.packet?.queue||[];
const filtered=()=>all().filter(q=>{const origin=$('origin').value,st=$('strength').value;return (origin==='all'||(isPublic(q)?origin==='public_signal':origin==='saia_candidate'))&&(st==='all'||(!isPublic(q)&&screening(q).state===st))});
function render(){
  const items=filtered();state.page=Math.min(Math.max(0,state.page),Math.max(0,Math.ceil(items.length/15)-1));const start=state.page*15,visible=items.slice(start,start+15);
  const counts={growth_observed:0,mixed_evidence:0,insufficient_data:0,growth_not_confirmed:0};
  for(const q of all()){if(isPublic(q))continue;const status=screening(q).state;if(status in counts)counts[status]++}
  const summary=$('status-summary');summary.classList.toggle('hidden',!all().length);
  summary.innerHTML=`<span>Признаки роста <strong>${counts.growth_observed}</strong></span><span>Есть противоречия <strong>${counts.mixed_evidence}</strong></span><span>Данных недостаточно <strong>${counts.insufficient_data}</strong></span><span>Рост не подтверждён <strong>${counts.growth_not_confirmed}</strong></span>`;
  const publicCount=all().filter(isPublic).length;
  if(publicCount)summary.insertAdjacentHTML('beforeend',`<span>Из публичных источников <strong>${publicCount}</strong></span>`);
  $('rows').innerHTML=visible.length?visible.map(q=>{
    if(isPublic(q))return publicRow(q);
    const c=q.card,sel=state.selected.has(c.candidate_id);
    return `<tr><td><input type="checkbox" data-select="${c.candidate_id}" ${sel?'checked':''} aria-label="Выбрать ${esc(c.label)}"></td><td><button class="signal-name" data-open="${c.candidate_id}">${esc(c.display_label||c.label)}</button><div class="signal-sub">#${q.rank} · ${esc(observed(c).doc_count??'—')} ${publicationsWord(observed(c).doc_count??0)}</div>${(q.published_references||[]).length?'<div class="public-reference-badge"><span class="badge public">Также в публичной подборке</span></div>':''}<div class="row-actions"><button class="button light small" data-dispatch-candidate="${c.candidate_id}" aria-label="${requested(c)?'Открыть экспертизу':'Передать на экспертизу'}: ${esc(c.display_label||c.label)}">${requested(c)?'Открыть экспертизу':'На экспертизу'}</button></div></td><td data-label="Состояние"><span class="badge ${badgeClass(q)}">${esc(screening(q).label)}</span>${requested(c)?' <span class="badge green">На экспертизе</span>':''}</td><td data-label="Оценка кандидата"><span class="metric list-score">${scoreText(q.assessment)} <small>/ 100</small></span></td><td data-label="Первая работа в выборке">${fmtDate(c.research_birth||c.first_found)}</td><td data-label="Динамика публикаций"><span class="metric ${growth(c)==='не установлена'?'missing':''}">${growth(c)}</span></td></tr>`;
  }).join(''):`<tr><td colspan="6" class="muted">${!state.packet?'Введите технологическое направление в строке поиска.':all().length?'По выбранным фильтрам ничего не найдено.':state.retrievedWorks.length?'Карточки пока не сформированы. Найденные публикации — ниже.':'Отдельные темы по этому запросу не выделены. Попробуйте более широкую формулировку.'}</td></tr>`;
  $('page-label').textContent=items.length?`${start+1}–${Math.min(start+15,items.length)} из ${items.length}`:'0 результатов';
  $('prev').disabled=state.page===0;$('next').disabled=start+15>=items.length;
  $('reload').disabled=!state.packet;
  $('select-page').checked=visible.length>0&&visible.every(itemSelected);
  $('selection').classList.toggle('hidden',selectedCount()===0);$('selection-count').textContent=`Выбрано: ${selectedCount()}`;
  renderRetrievalFallback();
}
function renderRetrievalFallback(){
  const box=$('retrieval-fallback');
  if(all().length||!state.retrievedWorks.length){box.classList.add('hidden');box.innerHTML='';return}
  const works=state.retrievedWorks;
  box.innerHTML=`<h3>Публикации найдены, но отдельная тема пока не выделена</h3><p>Найдено ${works.length} записей о публикациях. Их релевантность и возможные дубли ещё не проверены. Это исходные материалы, а не готовые слабые сигналы: для вывода о росте нужно проверить, что работы относятся к одной технологии и сопоставимы по времени.</p>`+
    works.slice(0,20).map(w=>{const link=(w.urls||[]).map(url).find(Boolean),name=esc(w.title||'Без названия');return `<div class="source">${link?`<a class="source-title" href="${esc(link)}" target="_blank" rel="noopener noreferrer">${name}</a>`:`<span class="source-title">${name}</span>`}<div class="source-meta">${fmtDate(w.published_at)} · ${esc((w.sources||[]).join(', '))}</div></div>`}).join('')+
    (works.length>20?'<p>Показаны первые 20 публикаций из найденного набора.</p>':'');
  box.classList.remove('hidden');
}
async function load(mission,score,discoveryJob=null){
  closeDrawer();state.mission=mission;state.score=score;state.selected.clear();state.selectedPublic.clear();state.page=0;state.discoveryJob=discoveryJob;state.retrievedWorks=[];notice('Загружаю результаты…');
  try{
    const args=score?`?score_run_id=${encodeURIComponent(score)}&limit=100`:'?limit=100';
    const [packet,requests]=await Promise.all([request(`/scout-results/${encodeURIComponent(mission)}${args}`),request('/expert-requests?limit=100').catch(()=>({requests:[]}))]);
    if(state.mission!==mission)return;state.packet=packet;state.score=packet.score_run_id;state.requests=requests.requests||[];
    if(discoveryJob){const sourceJob=await request(`/jobs/${encodeURIComponent(discoveryJob)}`).catch(()=>null);if(state.mission!==mission)return;if(sourceJob?.status==='succeeded'){const original=(sourceJob.payload?.branches||[]).find(b=>b.origin==='user_query');if(original?.query&&!$('query').value.trim())$('query').value=original.query;if(!packet.queue?.length)state.retrievedWorks=Array.isArray(sourceJob.result?.works)?sourceJob.result.works:[]}}
    $('results-title').textContent='Кандидаты в слабые сигналы';render();notice('');enrichVisible();
  }catch(e){if(state.mission!==mission)return;state.packet=null;$('rows').innerHTML='<tr><td colspan="6">Не удалось открыть результаты.</td></tr>';notice('Не удалось загрузить результаты. Обновите страницу или попробуйте позже.','error')}
}
function openDrawer(kicker,html){$('drawer-kicker').textContent=kicker;$('drawer-body').innerHTML=html;$('overlay').classList.remove('hidden');$('drawer').classList.remove('hidden');document.body.style.overflow='hidden';$('drawer').scrollTop=0;$('drawer-close').focus()}
function closeDrawer(){$('overlay').classList.add('hidden');$('drawer').classList.add('hidden');document.body.style.overflow='';state.current=null}
function bars(c){const pts=(series(c).points||[]).filter(p=>p.complete!==false).slice(-8);if(!pts.length)return '<div class="empty">Динамика пока не рассчитана.</div>';const max=Math.max(1,...pts.map(p=>p.topic_works||0));return pts.map(p=>`<div class="bar-row"><span class="bar-label">${esc((p.start||p.window||p.label||p.period||'период').slice(0,7))}</span><span class="bar-track"><span class="bar-fill" style="display:block;width:${Math.max(2,100*(p.topic_works||0)/max)}%"></span></span><span class="bar-val">${esc(p.topic_works??0)} ${worksWord(p.topic_works??0)} · ${validNumber(p.share)?`${(p.share*100).toFixed(1).replace('.',',')}%`:'—'}</span></div>`).join('')+'<p class="form-note">Показаны найденные работы по полным периодам. Неполный текущий период не участвует в оценке изменения. Рост их числа сам по себе не подтверждает тренд: полнота сбора могла меняться. Доля рассчитана только среди найденных публикаций по запросу.</p>'}
const recordTypeLabels={article:'Статья',review:'Обзор',preprint:'Препринт',book:'Книга','book-chapter':'Глава книги','conference-paper':'Доклад конференции','conference-abstract':'Тезис конференции'};
function recordTypeHint(e){const values=(e.openalex_record_types||[]).map(v=>recordTypeLabels[v]||'').filter(Boolean);return values.length?' · Тип записи OpenAlex: '+[...new Set(values)].join(', '):''}
function scientificPassport(e){const venues=[...new Set((e.venue_context||[]).map(v=>v.name).filter(Boolean))],kinds=(e.openalex_record_types||[]).map(t=>recordTypeLabels[t]||'Библиографическая запись');const sourceNames=[...new Set((e.sources||[]).map(s=>({arxiv:'arXiv',openalex:'OpenAlex',doi:'DOI'}[s.type]||s.type)))];return `<details class="source-passport"><summary>Паспорт публикации</summary><p>${esc(venues.join(' / ')||sourceNames.join(' / ')||'Площадка не установлена')} · ${esc([...new Set(kinds)].join(' / ')||'Тип записи не указан')}.</p><p>Оригинальный язык: ${esc(e.language||'не указан в сохранённой карточке')}.</p><p>Первичность исследования и рецензирование отдельно не подтверждены.</p></details>`}
function publications(c){const ev=c.evidence||[];return ev.length?ev.map(e=>{const links=(e.sources||[]).map(s=>{const u=url(s.url);return u?`<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(s.type)}</a>`:''}).filter(Boolean).join(' · ');const venues=[...new Set((e.venue_context||[]).map(v=>v.name).filter(Boolean))];const venue=venues.length?' · '+esc(venues.join(' / ')):'';const d=e.title_diagnostic||{},role=e.role==='раннее основание'?'Самая ранняя работа в выборке':e.role==='рост последнего окна'?'Пример из последнего окна':e.role||'';const formatHint=d.format_hint==='review_or_synthesis'?' · Обзор по названию':d.format_hint==='nonresearch_format'?' · Не исследовательская статья по названию':'';const contextHint=d.context_hint==='acceptance_or_market_context'?' · По названию: восприятие технологии, не технический метод':'';const matchHint=d.title_match==='anchor_not_shown_in_title'?' · Связь с темой требует проверки':'';return `<div class="source"><span class="source-title">${esc(e.title)}</span><div class="source-meta">${fmtDate(e.published_at)} · ${links||'Ссылка отсутствует'}${venue}${role?' · '+esc(role):''}${esc(recordTypeHint(e)+formatHint+contextHint+matchHint)}</div>${scientificPassport(e)}</div>`}).join(''):'<div class="empty">Публикации пока не найдены.</div>'}
async function loadCandidatePublications(c,offset=0){
  const block=$('full-publication-list'),mission=state.mission,score=state.score;
  if(!block||block.dataset.loading==='1')return;block.dataset.loading='1';
  const more=block.querySelector('[data-more-publications]');if(more)more.disabled=true;
  if(offset===0)block.innerHTML='<p class="form-note">Загружаю публикации…</p>';
  try{
    const packet=await request(`/signals/${encodeURIComponent(mission)}/${c.candidate_id}/publications?score_run_id=${encodeURIComponent(score)}&limit=20&offset=${offset}`);
    if(state.current?.candidate_id!==c.candidate_id||state.mission!==mission||state.score!==score||$('full-publication-list')!==block)return;
    if(packet.composition_sha256!==c.composition_sha256)throw Error('Publication composition mismatch');
    const content=packet.works.map(w=>{const links=(w.sources||[]).map(s=>{const u=url(s.url);return u?{url:u,label:s.type}:null}).filter(Boolean);const title=esc(w.title||'Без названия');return `<div class="source">${links.length?`<a class="source-title" href="${esc(links[0].url)}" target="_blank" rel="noopener noreferrer">${title}</a>`:`<span class="source-title">${title}</span>`}<div class="source-meta">${fmtDate(w.published_at)} · ${links.map(l=>`<a href="${esc(l.url)}" target="_blank" rel="noopener noreferrer">${esc(l.label)}</a>`).join(' · ')||'Ссылка отсутствует'}</div>${scientificPassport({...w,openalex_record_types:w.record_type?[w.record_type]:[]})}</div>`}).join('');
    if(offset===0)block.innerHTML='<div data-publication-rows></div><p class="form-note" data-publication-count></p>';
    block.querySelector('[data-publication-rows]').insertAdjacentHTML('beforeend',content);
    block.querySelector('[data-publication-count]').textContent=`Показано ${offset+packet.works.length} из ${packet.total} работ. Принадлежность каждой работы одной технологии ещё требует проверки.`;
    block.querySelector('[data-more-publications]')?.remove();
    if(packet.next_offset!==null){const button=document.createElement('button');button.className='button light small';button.dataset.morePublications='1';button.textContent='Ещё 20 публикаций';button.onclick=()=>loadCandidatePublications(c,packet.next_offset);block.append(button)}
    block.dataset.loaded='1';
  }catch(e){
    if($('full-publication-list')!==block)return;
    console.error('Candidate publications unavailable',e);
    const message=document.createElement('p');message.className='form-note';message.textContent='Не удалось загрузить публикации. Повторите попытку.';
    const retry=document.createElement('button');retry.className='button light small';retry.textContent='Повторить';retry.onclick=()=>{message.remove();retry.remove();loadCandidatePublications(c,offset)};
    if(offset===0)block.innerHTML='';if(more)more.remove();block.append(message,retry);
  }finally{delete block.dataset.loading}
}
const externalNames={gdelt_doc_2_0:'Публикация в СМИ',epo_ops:'Патент',nih_reporter:'Исследовательское финансирование',deps_dev:'Программное обеспечение',semantic_scholar:'Научная публикация',ukri_gtr:'Исследовательский грант',eu_funding_tenders:'Программа ЕС',huggingface_hub:'Открытая модель или набор данных',clinicaltrials_gov:'Клиническое исследование',europe_pmc:'Научная публикация',nasa_ntrs:'Публикация NASA',usaspending:'Государственный контракт США'};
async function loadExternalLinks(id){
  try{
    const packet=await request(`/signals/${encodeURIComponent(state.mission)}/${id}/external-links?score_run_id=${encodeURIComponent(state.score)}`);
    if(state.current?.candidate_id!==id||!packet.links?.length)return;
    const block=$('external-links');if(!block)return;
    const records=packet.links.map(item=>{
      const u=url(item.record_url),title=item.record_snapshot?.title||item.record_url;
      if(!u)return null;
      const kind=externalNames[item.source]||'Внешний источник';
      return {source:item.source,html:`<div class="source"><a class="source-title" href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(title)}</a><div class="source-meta">${esc(kind)} · ${item.assessment==='background_only'?'Для контекста':'По теме'} · добавлено аналитиком</div></div>`};
    }).filter(Boolean);
    const patents=records.filter(item=>item.source==='epo_ops');
    if(patents.length){$('patent-links').classList.remove('empty');$('patent-links').innerHTML=patents.map(item=>item.html).join('')+'<p class="form-note">Эти записи привязаны аналитиком и не входят в научную оценку.</p>'}
    const other=records.filter(item=>item.source!=='epo_ops');
    if(other.length){block.innerHTML='<h3>Дополнительные материалы · не входят в оценку</h3>'+other.map(item=>item.html).join('');block.classList.remove('hidden')}
  }catch(e){console.error('External links unavailable',e)}
}
function card(id){
  const q=all().find(q=>!isPublic(q)&&q.card.candidate_id===id);if(!q)return;
  const c=q.card,s=screening(q);state.current=c;const os=observed(c);
  const cachedOpenAlex=(c.limitations||[]).some(line=>line.includes('локального кэша'));
  const failed=(s.failed_reasons||[]).slice(0,4),unknown=(s.unknown_reasons||[]).slice(0,4);
  openDrawer('Карточка темы',`<span class="badge ${badgeClass(q)}">${esc(s.label)}</span> ${requested(c)?'<span class="badge green">На экспертизе</span>':'<span class="badge gray">Без экспертной проверки</span>'}
    <h2 id="drawer-title">${esc(c.display_label||c.label)}</h2>
    <div class="stat-grid"><div class="stat"><span>Оценка кандидата, баллы</span><strong id="card-overall-score">${scoreText(q.assessment)} / 100</strong></div><div class="stat"><span>Первая работа в выборке</span><strong>${fmtDate(c.research_birth||c.first_found)}</strong></div><div class="stat"><span>Динамика публикаций</span><strong style="font-size:13px">${growth(c)}</strong></div></div>
    <nav class="card-tabs" aria-label="Разделы карточки"><a href="#card-overview">О сигнале</a><a href="#card-assessment">Оценка и графики</a><a href="#card-dynamics">Динамика</a><a href="#card-science">Публикации</a><a href="#sources-block">Другие источники</a><a href="#card-analysis" id="open-card-analysis">PESTLE и влияние</a><button class="button ghost small" id="card-sources-collapse">Свернуть источники</button></nav>
    <div class="impact-toolbar"><button class="button" id="card-dispatch">${requested(c)?'Открыть экспертизу':'Передать на экспертизу'}</button><button class="button light small" id="card-select">${state.selected.has(id)?'Убрать из выбора':'Выбрать'}</button><a class="button ghost small" id="download-card" href="#">Скачать карточку</a></div>
    <div class="block" id="card-overview"><h3>Почему тема показана</h3><p>${esc(readableSourceDescription(s.explanation))}</p><details class="compact-details"><summary>Что уже проверено и что ещё неизвестно</summary>${failed.length?`<p><b>Не пройдено:</b> ${esc(failed.join(', '))}${(s.failed_reasons||[]).length>4?' и другие проверки':''}.</p>`:''}${unknown.length?`<p><b>Пока не проверено:</b> ${esc(unknown.join(', '))}${(s.unknown_reasons||[]).length>4?' и другие данные':''}.</p>`:''}<p class="form-note">Полнота оснований не означает вероятность подтверждения сигнала. Дата относится к найденной выборке, а не к первому появлению технологии.</p></details></div>
    ${assessmentControls(q)}
    ${publishedReferencesControls(q)}
    ${presentationControls()}
    <div class="block" id="card-dynamics"><h3>Динамика публикаций <a class="help-context" href="/help#dynamics" data-horizon-help="dynamics" aria-label="Как читать динамику публикаций">?</a></h3>${bars(c)}</div>
    <details class="block source-content-group" id="card-science"><summary>Публикации и препринты · ${esc(os.doc_count??'—')} в теме</summary><details class="compact-details"><summary>О составе публикаций</summary><p class="form-note">Тип источника и первичность результата ещё не подтверждены.</p>${cachedOpenAlex?'<p class="form-note">Часть данных OpenAlex загружена ранее. Полнота и актуальность требуют проверки.</p>':''}</details>${publications(c)}<details id="all-publications"><summary style="cursor:pointer;color:var(--blue);font-size:13px;font-weight:700;margin-top:12px">Все публикации темы</summary><div id="full-publication-list"></div></details></details>
    <div class="block hidden" id="external-links"></div>
    ${contextControls()}
    <details class="block source-content-group"><summary>Новости и коммерческие публикации</summary><div id="context-commercial" class="empty">Материалы загружаются…</div></details>
    <details class="block source-content-group"><summary>Патенты</summary><div id="context-patents" class="empty">Материалы загружаются…</div><div id="patent-links"></div></details>
    <details class="block source-content-group"><summary>Гранты, программы и госконтракты</summary><div id="context-funding" class="empty">Материалы загружаются…</div></details>
    <details class="block source-content-group"><summary>Дополнительные научные источники</summary><div id="context-science" class="empty">Источник не выбран.</div></details>
    <details class="block source-content-group"><summary>Модели, данные и ПО</summary><div id="context-software" class="empty">Источник не выбран.</div></details>
    <details class="block source-content-group"><summary>Клинические исследования</summary><div id="context-clinical" class="empty">Источник не выбран.</div></details>
    <details class="block source-content-group"><summary>Инвестиционные сделки и сообщения</summary><div id="context-investment" class="empty">Материалы загружаются…</div></details>
    ${analysisControls()}`);
  $('card-dispatch').onclick=()=>requested(c)?expertView():dispatchCandidate(id);
  $('card-select').onclick=()=>{state.selected.has(id)?state.selected.delete(id):state.selected.add(id);render();card(id)};
  $('all-publications').ontoggle=()=>{if($('all-publications').open&&!$('full-publication-list').dataset.loaded)loadCandidatePublications(c)};
  $('card-sources-collapse').onclick=()=>{for(const group of $('drawer-body').querySelectorAll('details.source-content-group'))group.open=false;$('sources-block').scrollIntoView({behavior:'smooth',block:'start'})};
  $('drawer-body').querySelector('.card-tabs').addEventListener('click',e=>{const anchor=e.target.closest('a[href^="#"]');if(!anchor||anchor.id==='open-card-analysis')return;const section=$(anchor.getAttribute('href').slice(1));if(!section)return;e.preventDefault();if(section instanceof HTMLDetailsElement)section.open=true;section.scrollIntoView({behavior:'smooth',block:'start'})});
  $('context-collect').onclick=()=>loadCardContext(c,true);
  $('assessment-refresh').onclick=async()=>{const button=$('assessment-refresh');button.disabled=true;try{await loadCardContext(c,true);await refreshAssessment(c)}finally{if(button.isConnected)button.disabled=false}};
  loadCardContext(c);
  $('presentation-generate').onclick=()=>loadCardPresentation(c,true);
  loadCardPresentation(c);
  setupAnalysis(c);
  loadExternalLinks(id);
}
async function prepareSearch(e){
  e.preventDefault();if(state.searchBusy)return;const query=$('query').value.trim();if(query.length<2)return;
  setSearchBusy(true);notice('Подбираю направления поиска…');
  try{
    const plan=await post('/query-plan/preview',{query,max_suggestions:12});state.plan=plan;
    const suggestions=plan.suggestions||[];$('preview').classList.remove('hidden');
    $('preview').innerHTML=`<h3>Поиск по направлению «${esc(plan.original_query)}»</h3>${suggestions.length?`<details><summary>Уточнить темы поиска (${suggestions.length})</summary><div class="branches">${suggestions.map(s=>`<label class="branch"><input type="checkbox" data-branch="${esc(s.suggestion_id)}" checked><span>${esc(s.label_ru||s.query_en)}</span></label>`).join('')}</div></details><p>Для анализа используются выбранные темы; исходный запрос сохраняется.</p>`:'<p>Поиск начнётся по вашему запросу. Англоязычные публикации могут не найтись по русским словам.</p><details><summary>Указать английскую научную формулировку</summary><input class="field" id="english-phrase" maxlength="160" placeholder="Например, acoustic levitation"></details>'}<button class="button" id="launch-search">Запустить поиск</button>`;
    $('launch-search').onclick=launchSearch;notice('');setSearchBusy(false);await launchSearch();
  }catch(e){notice('Не удалось подготовить поиск. Попробуйте ещё раз.','error')}
  finally{if(state.searchBusy)setSearchBusy(false)}
}
async function pollJob(id,stage,deadline){
  let connectionErrors=0;
  for(let i=0;i<1200;i++){
    if(Date.now()>=deadline)throw Error('SAIA_SEARCH_WAIT_LIMIT');
    const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),Math.min(10000,Math.max(1,deadline-Date.now())));
    let j;try{j=await request(`/jobs/${encodeURIComponent(id)}`,{signal:controller.signal});connectionErrors=0}catch(e){
      if(Date.now()>=deadline)throw Error('SAIA_SEARCH_WAIT_LIMIT');
      if(++connectionErrors>3)throw e;
      notice('Связь прервалась. Восстанавливаю поиск…','warning');await wait(3000);continue;
    }finally{clearTimeout(timeout)}state.job=j;
    if(j.status==='succeeded')return j;
    if(['failed','cancelled'].includes(j.status)){clearPending();throw Error(j.error||`Задача завершилась: ${j.status}`)}
    notice(stage==='collect'?'Собираю публикации. Это может занять несколько минут.':'Анализирую темы. Это может занять несколько минут.');await wait(3000);
  }
  throw Error('Анализ продолжается дольше обычного. Задача: '+id);
}
async function continueSearch(pending){
  if(!Number.isFinite(pending.deadline_at)){pending={...pending,deadline_at:Date.now()+searchWaitLimitMs};savePending(pending)}
  if(Date.now()>=pending.deadline_at)throw Error('SAIA_SEARCH_WAIT_LIMIT');
  if(pending.phase==='collect'){
    const done=await pollJob(pending.job_id,'collect',pending.deadline_at);
    const foundWorks=Array.isArray(done.result?.works)?done.result.works:[];
    if(!foundWorks.length){
      const sourceErrors=done.result?.errors||{};
      clearPending();
      notice(Object.keys(sourceErrors).length?'Не удалось получить публикации из доступных источников. Попробуйте позже.':'По выбранным поисковым фразам публикации не найдены. Попробуйте другую формулировку.','warning');
      return;
    }
    pending={phase:'enqueue_analysis',discovery_job_id:done.job_id,operation_id:crypto.randomUUID(),deadline_at:pending.deadline_at};savePending(pending);
  }
  if(pending.phase==='enqueue_analysis'){
    if(Date.now()>=pending.deadline_at)throw Error('SAIA_SEARCH_WAIT_LIMIT');
    const full=await post(`/jobs/${encodeURIComponent(pending.discovery_job_id)}/full-analysis`,{requested_by:'scout-ui',max_records:10000,top_n:100,operation_id:pending.operation_id});
    pending={phase:'analyze',job_id:full.analysis_job.job_id,discovery_job_id:pending.discovery_job_id,deadline_at:pending.deadline_at};savePending(pending);
  }
  if(pending.phase==='analyze'){
    const final=await pollJob(pending.job_id,'analyze',pending.deadline_at);
    const score=final.result?.runs?.score,mission=final.mission_id;
    if(final.result?.input_status==='no_eligible_publications'){
      clearPending();
      notice('Публикации найдены, но ни одна не прошла проверку соответствия теме. Это не означает, что слабых сигналов нет. Попробуйте уточнить запрос.','warning');
      return;
    }
    if(!score||!mission){clearPending();throw Error('Карточки не найдены после анализа. Задача: '+final.job_id)}
    const discoveryJob=pending.discovery_job_id||null;
    clearPending();history.replaceState(null,'',`/scout?mission=${encodeURIComponent(mission)}&score=${encodeURIComponent(score)}${discoveryJob?`&discovery=${encodeURIComponent(discoveryJob)}`:''}`);
    $('preview').classList.add('hidden');await load(mission,score,discoveryJob);
  }
}
async function launchSearch(){
  const p=state.plan;if(!p||state.searchBusy)return;
  const ids=[...$('preview').querySelectorAll('[data-branch]:checked')].map(el=>el.dataset.branch);
  setSearchBusy(true);clearPending();
  try{
    if(state.packet)$('results-title').textContent='Предыдущие результаты';
    notice('Запускаю поиск…');
    const approved=await post('/query-plan/approve',{query:p.original_query,max_suggestions:12,preview_payload_sha256:p.plan_payload_sha256,selected_branch_ids:ids,approved_by:'scout-ui',operation_id:crypto.randomUUID()});
    const offered=new Map((p.suggestions||[]).map(s=>[s.suggestion_id,s]));
    const extraEnglish=$('english-phrase')?.value.trim()||'';
    const branch_specs=approved.approved_plan.branches.map(b=>({branch_id:b.branch_id,included_phrases:b.branch_id==='original-query'?[b.query,...(extraEnglish?[extraEnglish]:[])]:phrasesFor(offered.get(b.branch_id)||{suggestion_id:b.branch_id,query_en:b.query}),excluded_phrases:[]}));
    const compiled=await post(`/query-plans/${encodeURIComponent(approved.plan_id)}/compile`,{branch_specs,compiled_by:'scout-ui',operation_id:crypto.randomUUID()});
    const today=new Date(),now=new Date(today.getFullYear(),today.getMonth(),1);
    const from=new Date(now.getFullYear()-5,now.getMonth(),1);
    const localDate=d=>new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,10);
    const discovery=await post(`/query-plans/${encodeURIComponent(approved.plan_id)}/jobs/discovery`,{requested_by:'scout-ui',date_from:localDate(from),as_of_date:localDate(now),limit_per_source:100,max_results:300,compiled_query_plan_id:compiled.compilation_id,openalex_collection_mode:'live_with_cache_fallback',operation_id:crypto.randomUUID()});
    const pending={phase:'collect',job_id:discovery.job_id,deadline_at:Date.now()+searchWaitLimitMs};savePending(pending);
    await continueSearch(pending);
  }catch(e){
    console.error('Horizon search failed',e);searchError(e);
  }finally{setSearchBusy(false)}
}
async function resumePending(){let pending=pendingSearch();if(!pending||state.searchBusy)return;if(Number.isFinite(pending.deadline_at)&&Date.now()>=pending.deadline_at){searchError(Error('SAIA_SEARCH_WAIT_LIMIT'));return}setSearchBusy(true);notice('Продолжаю поиск…');try{await continueSearch(pending)}catch(e){console.error('Horizon search resume failed',e);searchError(e)}finally{setSearchBusy(false)}}
function dispatchCandidate(id){
  if(!all().some(q=>!isPublic(q)&&q.card.candidate_id===id))return;
  dispatchForm({candidateIds:[id],publicIds:[]});
}
function dispatchPublic(id){
  if(!all().some(q=>isPublic(q)&&q.public_signal.id===id))return;
  dispatchForm({candidateIds:[],publicIds:[id]});
}
function dispatchForm(selection){
  const candidateIds=[...(selection?.candidateIds??state.selected)],publicIds=[...(selection?.publicIds??state.selectedPublic)];
  const n=candidateIds.length+publicIds.length;if(!n)return;
  if(n>15){notice('За одну заявку можно передать не более 15 кандидатов. Уменьшите выбор.','warning');return}
  const mission=state.mission,score=state.score;
  const chosen=all().filter(q=>isPublic(q)?publicIds.includes(q.public_signal.id):candidateIds.includes(q.card.candidate_id));
  if(!mission||!score||chosen.length!==n){notice('Обновите результаты и выберите кандидатов ещё раз.','warning');return}
  const noun=n%10===1&&n%100!==11?'кандидат':n%10>=2&&n%10<=4&&(n%100<12||n%100>14)?'кандидата':'кандидатов';
  state.current=null;
  openDrawer('Экспертиза',`<h2 id="drawer-title">Передать на экспертизу</h2><p class="drawer-sub">${n===1?'Выбран':'Выбрано'} ${n} ${noun}.</p><ul class="dispatch-candidates">${chosen.map(q=>`<li>${esc(isPublic(q)?publicTitle(q.public_signal):q.card.display_label||q.card.label)}${isPublic(q)?' <span class="badge public">Из публичного источника</span>':''}</li>`).join('')}</ul><form id="dispatch-form" class="form-grid"><label>Ваше имя или команда<input class="field" id="dispatch-by" value="Технологический скаут" maxlength="120" required></label><label>Кому направить<input class="field" id="dispatch-to" placeholder="Имя эксперта или название группы" maxlength="120" required></label><label>Комментарий<textarea class="field" id="dispatch-note" maxlength="2000" placeholder="Что именно попросить проверить?"></textarea></label><button class="button" id="dispatch-send" type="submit">Передать на экспертизу</button><p id="dispatch-state" class="form-note" role="status">Заявка сохранится в разделе «Экспертиза». Уведомления не отправляются.</p></form>`);
  const form=$('dispatch-form'),button=$('dispatch-send'),message=$('dispatch-state');
  const current=()=>$('dispatch-form')===form&&form.isConnected;
  let operation=crypto.randomUUID(),lastContent=null,inFlight=false,saved=false;
  form.onsubmit=async e=>{
    e.preventDefault();if(inFlight||saved||!form.reportValidity())return;
    const payload={mission_id:mission,score_run_id:score,candidate_ids:candidateIds,public_signal_ids:publicIds,requested_by:$('dispatch-by').value.trim(),recipient:$('dispatch-to').value.trim(),note:$('dispatch-note').value.trim()};
    if(!payload.requested_by||!payload.recipient){message.textContent='Укажите ваше имя и получателя заявки.';return}
    const content=JSON.stringify(payload);
    if(content!==lastContent){operation=crypto.randomUUID();lastContent=content}
    inFlight=true;button.disabled=true;button.textContent='Передаю…';
    try{
      const r=await post('/expert-requests',{...payload,operation_id:operation});saved=true;
      state.requests=[r,...state.requests.filter(item=>item.request_id!==r.request_id)];
      if(state.mission===mission&&state.score===score){
        candidateIds.forEach(id=>state.selected.delete(id));publicIds.forEach(id=>state.selectedPublic.delete(id));render();
        if(current())notice('Заявка сохранена в разделе «Экспертиза».');
      }
      if(!current())return;
      form.innerHTML=`<div class="dispatch-success"><h3>Передано на экспертизу</h3><p class="form-note">Получатель: ${esc(payload.recipient)}. Заявка и материалы доступны в разделе «Экспертиза».</p><button class="button" id="dispatch-view" type="button">Открыть экспертизу</button></div>`;
      $('dispatch-view').onclick=expertView;
    }catch(e){
      console.error('Expert request failed',e);
      if(current()){message.textContent='Не удалось сохранить заявку. Проверьте поля и повторите попытку.';button.disabled=false;button.textContent='Повторить отправку'}
    }finally{inFlight=false}
  };
  $('dispatch-to').focus();
}
async function expertView(){
  activateScoutView('experts');
  $('requests').innerHTML='<div class="panel queue-item">Загрузка…</div>';
  try{
    const d=await request('/expert-requests?limit=100');state.requests=d.requests||[];
    $('requests').innerHTML=state.requests.length?state.requests.map(r=>{
      const label=r.status==='reviewed'?'Мнение по всем карточкам получено':r.status==='partially_reviewed'?'Часть карточек рассмотрена':'Ожидает мнения эксперта';
      const color=r.status==='reviewed'?'green':'amber';
      return `<div class="panel queue-item"><span class="badge ${color}">${label}</span><div class="meta">${esc(r.requested_by)} → ${esc(r.recipient)} · ${esc(r.reviewed_count??0)}/${esc(r.total_count??r.candidate_snapshot?.length??r.candidate_ids.length)} рассмотрено</div><div class="items">${(r.candidate_snapshot||[]).map(c=>c.item_kind==='public_signal'?`<button data-expert-public="${esc(r.request_id)}|${esc(c.public_signal_id)}">${esc(c.label)}</button> <span class="badge public">Из публичного источника</span>`:`<button data-expert-card="${esc(r.mission_id)}|${r.score_run_id}|${c.candidate_id}">${esc(c.label)}</button>`).join('<br>')}</div>${r.note?`<p class="form-note">${esc(r.note)}</p>`:''}</div>`;
    }).join(''):'<div class="panel queue-item">Заявок пока нет.</div>';
  }catch(e){console.error('Expert queue failed',e);$('requests').innerHTML='<div class="notice error">Не удалось открыть экспертизу. Попробуйте позже.</div>'}
}
async function expertCard(mission,score,id){
  try{
    const packet=await request(`/scout-results/${encodeURIComponent(mission)}?score_run_id=${score}&limit=100`);
    const q=packet.queue.find(q=>q.card.candidate_id===id);if(!q)throw Error('Карточка отсутствует в этом прогоне.');const c=q.card;
    const externalRefs=(q.assessment?.records||[]).filter(r=>r.url?.startsWith('https://')&&r.exclusion_reason!=='Нет отдельной первичной ссылки на запись').slice(0,10);
    openDrawer('Мнение эксперта',`<span class="badge amber">Ожидает проверки</span><h2 id="drawer-title">${esc(c.display_label||c.label)}</h2><p class="drawer-sub">Проверьте конкретность темы, первичность исследований, сопоставимость периодов и независимость внешних материалов. Новость не доказывает спрос; патент не доказывает внедрение.</p><div class="block">${q.assessment?.visualization_html||''}</div><div class="block"><h3>Публикации для проверки</h3>${publications(c)}</div><div class="block form-grid"><label>Решение<select id="review-decision" class="field"><option value="needs_review">Нужна дополнительная проверка</option><option value="research_line_supported">Исследовательская линия подтверждается</option><option value="noise">Шум</option><option value="possible_duplicate">Возможный дубль</option><option value="insufficient_evidence">Недостаточно данных</option></select></label><label>Имя эксперта<input id="review-by" class="field" maxlength="120"></label><label>Обоснование (не менее 20 символов)<textarea id="review-rationale" class="field" maxlength="5000"></textarea></label>${externalRefs.length?'<h3>Внешние материалы, проверенные вами</h3>'+externalRefs.map(r=>`<label class="source-check"><input type="checkbox" data-review-source="${esc(r.url)}"><span>${esc(r.title)}</span></label>`).join(''):''}<button id="review-send" class="button">Сохранить мнение</button><p id="review-state" class="form-note">Мнение и выбранные ссылки сохраняются отдельно. Личность эксперта пока не проверяется системой.</p></div>`);
    $('review-send').onclick=async()=>{const btn=$('review-send');btn.disabled=true;try{await post(`/signals/${encodeURIComponent(mission)}/${id}/reviews?score_run_id=${score}`,{decision:$('review-decision').value,reviewed_by:$('review-by').value,rationale:$('review-rationale').value,sources:[...$('drawer-body').querySelectorAll('[data-review-source]:checked')].map(el=>el.dataset.reviewSource),operation_id:crypto.randomUUID()});$('review-state').textContent='Мнение сохранено. Автоматическая оценка не изменена.';notice('Экспертное мнение сохранено отдельно от автоматической оценки.')}catch(e){console.error('Expert review failed',e);$('review-state').textContent='Не удалось сохранить мнение. Проверьте заполнение полей и повторите попытку.';btn.disabled=false}};
  }catch(e){console.error('Expert card failed',e);notice('Не удалось открыть карточку. Попробуйте позже.','error')}
}
$('search-form').onsubmit=prepareSearch;
$('search-submit').onclick=prepareSearch;
$('reload').onclick=()=>{if(state.mission)load(state.mission,state.score,state.discoveryJob)};
$('strength').onchange=()=>{state.page=0;render();enrichVisible()};
$('prev').onclick=()=>{state.page--;render();enrichVisible()};
$('next').onclick=()=>{state.page++;render();enrichVisible()};
$('select-page').onchange=e=>{filtered().slice(state.page*15,state.page*15+15).forEach(q=>selectItem(q,e.target.checked));render()};
$('rows').onclick=e=>{const dispatch=e.target.closest('[data-dispatch-candidate]');if(dispatch){const id=Number(dispatch.dataset.dispatchCandidate);requested({candidate_id:id})?expertView():dispatchCandidate(id);return}const dispatchPublished=e.target.closest('[data-dispatch-public]');if(dispatchPublished){const id=dispatchPublished.dataset.dispatchPublic;publicRequested(id)?expertView():dispatchPublic(id);return}const published=e.target.closest('[data-public-open]');if(published){publicCard(published.dataset.publicOpen);return}const open=e.target.closest('[data-open]');if(open)card(Number(open.dataset.open))};
$('rows').onchange=e=>{const published=e.target.closest('[data-public-select]');if(published){published.checked?state.selectedPublic.add(published.dataset.publicSelect):state.selectedPublic.delete(published.dataset.publicSelect);render();return}const el=e.target.closest('[data-select]');if(el){el.checked?state.selected.add(Number(el.dataset.select)):state.selected.delete(Number(el.dataset.select));render()}};
$('dispatch-open').onclick=()=>dispatchForm();
$('drawer-close').onclick=closeDrawer;
$('overlay').onclick=closeDrawer;
document.onkeydown=e=>{if(e.key==='Escape')closeDrawer()};
$('nav-experts').onclick=expertView;
$('nav-signals').onclick=()=>{state.view='signals';$('experts-view').classList.add('hidden');$('signals-view').classList.remove('hidden');$('nav-experts').classList.remove('active');$('nav-signals').classList.add('active');render()};
$('requests').onclick=e=>{const published=e.target.closest('[data-expert-public]');if(published){const [requestId,id]=published.dataset.expertPublic.split('|');publicExpertCard(requestId,id);return}const b=e.target.closest('[data-expert-card]');if(b){const [m,s,id]=b.dataset.expertCard.split('|');expertCard(m,Number(s),Number(id))}};
const params=new URLSearchParams(location.search);const m=params.get('mission'),s=params.get('score'),discoveryJob=params.get('discovery');
if(m)load(m,s?Number(s):null,discoveryJob).then(resumePending);else{render();resumePending()}
})();
</script></body></html>'''

from saia.scout_sources_web import SOURCES_SCRIPT, SOURCES_STYLE, SOURCES_VIEWS
from saia.scout_presentation_web import PRESENTATION_SCRIPT
from saia.scout_analysis_web import ANALYSIS_SCRIPT, ANALYSIS_STYLE
from saia.scout_assessment_web import ASSESSMENT_SCRIPT, ASSESSMENT_STYLE
from saia.scout_public_signals_web import PUBLIC_SCRIPT, PUBLIC_STYLE
from saia.public_signal_content_web import PUBLIC_CONTENT_CONTROLS

# Keep the mature search/validation flow; compose the new presentation layer.
SCOUT_WEB_HTML = SCOUT_WEB_HTML.replace('</style>', SOURCES_STYLE + ANALYSIS_STYLE + ASSESSMENT_STYLE + PUBLIC_STYLE + '</style>', 1)
SCOUT_WEB_HTML = SCOUT_WEB_HTML.replace('</main></div>', SOURCES_VIEWS + '</main></div>', 1)
SCOUT_WEB_HTML = SCOUT_WEB_HTML.replace('const params=new URLSearchParams(location.search);',
                                       SOURCES_SCRIPT + PRESENTATION_SCRIPT + ANALYSIS_SCRIPT + ASSESSMENT_SCRIPT + PUBLIC_SCRIPT + '\nconst params=new URLSearchParams(location.search);', 1)
SCOUT_WEB_HTML = SCOUT_WEB_HTML.replace('<select id="strength"', '<select id="origin" class="filter" aria-label="Происхождение сигналов"><option value="all">Все источники</option><option value="saia_candidate">Найденные Horizon</option><option value="public_signal">Из публичных подборок</option></select><select id="strength"', 1)
SCOUT_WEB_HTML = SCOUT_WEB_HTML.replace('<th>Тема исследований</th>', '<th>Сигнал / тема</th>', 1)
SCOUT_WEB_HTML = SCOUT_WEB_HTML.replace('<div class="results-actions">', '<div class="results-actions">' + PUBLIC_CONTENT_CONTROLS.replace('>Содержание<', '>Публичные записи<'), 1)
SCOUT_WEB_HTML = SCOUT_WEB_HTML.replace('<th>Первая работа в выборке</th>', '<th>Первая работа / выпуск отчёта</th>', 1)
SCOUT_WEB_HTML = SCOUT_WEB_HTML.replace('<div id="status-summary"', '<div id="evidence-progress" class="evidence-progress hidden" role="status" aria-live="polite"></div><div id="status-summary"', 1)
SCOUT_WEB_HTML = SCOUT_WEB_HTML.replace('title="Полнота имеющихся оснований, не вероятность подтверждения сигнала">Полнота оснований', 'title="Рабочий балл ранжирования с вкладом источников; не вероятность успеха">Оценка кандидата', 1)
SCOUT_WEB_HTML = SCOUT_WEB_HTML.replace('Дополнительные материалы из СМИ, патентов и других источников собираются для открытой карточки.', 'Новости и патенты автоматически дополняют оценку первых 15 кандидатов; остальные — при переходе к следующей странице.', 1)
SCOUT_WEB_HTML = SCOUT_WEB_HTML.replace('>Оценка кандидата</th>', '>Оценка кандидата <a class="help-context" href="/help#scores" data-horizon-help="scores" aria-label="Как рассчитывается оценка кандидата">?</a></th>', 1)

from saia.help_web import attach_help_widget

SCOUT_WEB_HTML = attach_help_widget(SCOUT_WEB_HTML)
