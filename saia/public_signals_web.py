"""Source/year/type-labelled public catalogue, with pre-2023 editions archived."""

from saia.public_signal_content_web import PUBLIC_CONTENT_CONTROLS, PUBLIC_CONTENT_SCRIPT, PUBLIC_CONTENT_STYLE

PUBLIC_SIGNALS_HTML = r'''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Horizon — публичные сигналы</title>
<style>
:root{--blue:#3156ad;--ink:#202b43;--muted:#68758d;--line:#dfe5ef;--soft:#f5f7fb}
*{box-sizing:border-box}body{margin:0;background:var(--soft);color:var(--ink);font:14px/1.5 Inter,Arial,sans-serif}button,input,select{font:inherit}a{color:var(--blue);text-decoration:none}a:hover{text-decoration:underline}
header{height:68px;background:white;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;padding:0 36px}.brand{font-weight:800;font-size:21px;color:#233f89}.brand span{border-left:1px solid var(--line);margin-left:12px;padding-left:15px;color:var(--ink);font-size:17px}.back{font-size:13px}.header-actions{display:flex;align-items:center;gap:20px;font-size:13px}
main{max-width:1160px;margin:0 auto;padding:32px 24px 60px}h1{font-size:28px;margin:0 0 7px}p.lead{color:var(--muted);margin:0 0 22px}.panel{background:white;border:1px solid var(--line);border-radius:15px;padding:21px;box-shadow:0 10px 30px rgba(30,50,90,.05)}.filters{display:grid;grid-template-columns:minmax(240px,1fr) 160px auto;gap:10px}.secondary-filters{grid-column:1/-1;display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px}.field{min-height:42px;padding:9px 11px;border:1px solid #cbd5e5;border-radius:9px;background:white;color:var(--ink);width:100%;min-width:0}button{border:0;border-radius:9px;background:var(--blue);color:white;font-weight:700;padding:9px 16px;cursor:pointer}.note{color:var(--muted);font-size:12px;margin:15px 0 0}.summary{display:flex;justify-content:space-between;align-items:center;margin:24px 0 12px;gap:12px}.summary h2{margin:0;font-size:19px}.summary span{color:var(--muted);font-size:12px}.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(285px,1fr));gap:12px}.source-group{grid-column:1/-1;margin-top:9px}.source-group h3{font-size:17px;margin:0}.source-group p{font-size:12px;color:var(--muted);margin:2px 0 0}.card{background:white;border:1px solid var(--line);border-radius:12px;padding:17px;min-height:200px;display:flex;flex-direction:column}.card h3{font-size:16px;line-height:1.35;margin:10px 0 5px}.original{font-size:12px;color:var(--muted);margin:0 0 12px}.badges{display:flex;flex-wrap:wrap;gap:5px}.badge{font-size:11px;color:#3556a3;background:#eaf0ff;border-radius:100px;padding:4px 8px;font-weight:700}.badge.type{color:#536274;background:#f0f3f7}.meta{color:var(--muted);font-size:12px;margin-top:auto}.card a{font-weight:700;font-size:12px;margin-top:13px}.pager{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:17px;color:var(--muted);font-size:12px}.pager button{background:white;border:1px solid var(--line);color:var(--blue)}.pager button:disabled{opacity:.5;cursor:not-allowed}.error{color:#8c3332;background:#fff0ef;border:1px solid #f3cecb;padding:12px;border-radius:9px}.source-note{margin-top:22px}.source-note summary{font-weight:700;cursor:pointer}.source-note p{font-size:12px;color:var(--muted);margin:12px 0}.source-note h3{font-size:14px;margin:18px 0 8px}
.card-open{margin:10px 0;font-size:12px;text-align:left;align-self:flex-start;background:#edf2fc;color:#3156ad}.public-overlay{position:fixed;inset:0;background:#13264f55;z-index:20}.public-drawer{position:fixed;inset:0 0 0 auto;width:min(650px,100vw);height:100dvh;overflow-y:auto;background:white;z-index:21;padding:25px 28px;box-shadow:-15px 0 40px #18294d33}.public-drawer-head{display:flex;justify-content:space-between;align-items:center;gap:15px;margin-bottom:20px}.public-drawer-head button{background:#f0f3f7;color:#3156ad}.public-drawer h2{font-size:23px;line-height:1.35}.public-drawer h3{font-size:16px}.public-drawer .form-note{font-size:12px;color:#68758d;line-height:1.55}.public-drawer .public-content-language{margin:10px 0}.public-drawer .drawer-section{border-top:1px solid #dfe5ef;margin-top:18px;padding-top:15px}.public-drawer [data-public-brief]{display:inline-block;margin-top:18px;font-size:13px;font-weight:700}.public-overlay[hidden],.public-drawer[hidden]{display:none}
@media(max-width:850px){header{padding:0 16px}main{padding:24px 16px}.filters,.secondary-filters{grid-template-columns:1fr}.back{display:none}.summary{align-items:flex-start;flex-direction:column}}
</style></head><body>
<header><div class="brand">Газпромбанк <span>Horizon</span></div><nav class="header-actions" aria-label="Навигация"><a class="back" href="/scout">← К поиску слабых сигналов</a><a href="/help" data-horizon-help="quick-start">Справка</a></nav></header>
<main><div class="public-content-heading"><div><h1>Публичные сигналы <a class="help-context" href="/help#public-signals" data-horizon-help="public-signals" aria-label="Справка о публичных сигналах">?</a></h1></div>__PUBLIC_CONTENT_CONTROLS__</div>
<section class="panel"><form id="filter-form" class="filters"><input class="field" id="query" maxlength="160" placeholder="Название на русском или английском" aria-label="Поиск по названию"><select class="field" id="year" aria-label="Год подборки"><option value="">Все годы</option></select><button type="submit">Показать</button><div class="secondary-filters"><select class="field" id="source" aria-label="Источник"><option value="">Все источники</option></select><select class="field" id="kind" aria-label="Тип подборки"><option value="">Все типы подборок</option></select><select class="field" id="category" aria-label="Категория"><option value="">Все категории</option></select></div></form></section>
<div class="summary"><h2>Опубликованные подборки</h2><span id="count">Загрузка…</span></div><div class="cards" id="cards" aria-live="polite"></div><div class="pager"><span id="page">—</span><div><button id="prev">← Назад</button> <button id="next">Следующие 15 →</button></div></div>
<details class="panel source-note"><summary>О подборках и первоисточниках</summary><p>Год относится к выпуску подборки. Актуальность сигнала требует отдельной проверки.</p><div id="source-notes"></div><h3>Дополнительные подборки — пока только ссылки</h3><div id="link-notes"></div><p id="archive-note"></p></details></main>
<div class="public-overlay" id="public-overlay" hidden></div>
<aside class="public-drawer" id="public-drawer" role="dialog" aria-modal="true" aria-labelledby="public-drawer-title" hidden><div class="public-drawer-head"><span class="badge">Из публичного источника</span><button type="button" id="public-drawer-close" aria-label="Закрыть карточку">Закрыть</button></div><div id="public-drawer-content"></div></aside>
<script>

(()=>{
'use strict';
const $=id=>document.getElementById(id),esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
/* PUBLIC_CONTENT_SCRIPT */
let offset=0,total=0,controller;
let visibleRecords=new Map(),drawerRecord=null,drawerReturnFocus=null;
const locator=r=>r.source_page!=null?`стр. ${r.source_page}`:r.source_section||'Раздел на сайте';
function closePublicDrawer(){if(!drawerRecord)return;$('public-drawer').hidden=true;$('public-overlay').hidden=true;document.querySelector('main').inert=false;document.querySelector('header').inert=false;document.body.style.overflow='';drawerRecord=null;if(drawerReturnFocus?.isConnected)drawerReturnFocus.focus()}
function openPublicDrawer(id){
  const r=visibleRecords.get(id);if(!r)return;
  drawerReturnFocus=document.activeElement;drawerRecord=r;
  $('public-drawer-content').innerHTML=publicContentControlMarkup+`<h2 id="public-drawer-title" ${publicTitleAttrs(r)}>${esc(publicTitle(r))}</h2><p class="form-note">${esc(r.source_short_label)} · подборка ${r.source_year} · ${esc(locator(r))}</p>`
    +publicExplanationHtml(r)+`<section class="drawer-section"><h3>Оценка и динамика <a class="help-context" href="/help#public-signals" data-horizon-help="public-signals" aria-label="Об оценке и динамике публичных сигналов">?</a></h3><p class="form-note">Расчёт по источникам пока не выполнен.</p></section><section class="drawer-section"><h3>Первоисточник</h3><a href="${esc(r.source_url)}" target="_blank" rel="noopener noreferrer">Открыть первоисточник ↗</a><br><a data-public-brief="/public-signals/${encodeURIComponent(id)}/brief" href="/public-signals/${encodeURIComponent(id)}/brief?lang=${publicContentLang}">Скачать карточку</a></section>`;
  $('public-drawer').hidden=false;$('public-overlay').hidden=false;$('public-drawer').scrollTop=0;
  document.querySelector('main').inert=true;document.querySelector('header').inert=true;document.body.style.overflow='hidden';
  bindPublicContentLanguage();$('public-drawer-close').focus();
}
$('public-overlay').onclick=closePublicDrawer;$('public-drawer-close').onclick=closePublicDrawer;
document.addEventListener('keydown',e=>{if(!drawerRecord)return;if(e.key==='Escape'){e.preventDefault();closePublicDrawer()}else if(e.key==='Tab'){const items=[...$('public-drawer').querySelectorAll('button,a[href],summary')].filter(el=>el.getClientRects().length);const first=items[0],last=items[items.length-1];if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus()}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus()}}});
$('cards').addEventListener('click',e=>{const button=e.target.closest('[data-public-detail]');if(button)openPublicDrawer(button.dataset.publicDetail)});
function options(id,placeholder,rows,value,label){
  if($(id).dataset.ready)return;
  $(id).innerHTML=`<option value="">${placeholder}</option>`+rows.map(row=>{
    const canonical=value(row),contentAttrs=id==='category'?' '+publicCategoryAttrs(canonical):'';
    return `<option value="${esc(canonical)}"${contentAttrs}>${esc(label(row))}</option>`;
  }).join('');
  $(id).dataset.ready='1';
}
async function load(){
  closePublicDrawer();
  controller?.abort();
  const active=new AbortController();controller=active;
  const params=new URLSearchParams({limit:'15',offset:String(offset)});
  for(const [id,key] of [['query','query'],['source','source_id'],['category','category'],['kind','source_type'],['year','year']]){
    if($(id).value.trim())params.set(key,$(id).value.trim());
  }
  $('cards').textContent='Загрузка…';
  try{
    const response=await fetch('/api/public-signals?'+params,{signal:active.signal});
    const data=await response.json();
    if(!response.ok)throw Error(data.detail||'Не удалось загрузить каталог.');
    if(controller!==active)return;
    visibleRecords=new Map(data.records.map(row=>[row.id,row]));
    total=data.total;
    $('count').textContent=`Записей: ${total} · подборок: ${data.matched_source_count} (2023+)`;
    $('page').textContent=total?`${offset+1}–${Math.min(offset+15,total)} из ${total}`:'0 результатов';
    $('prev').disabled=offset===0;$('next').disabled=offset+15>=total;
    const sources=new Map(data.sources.map(s=>[s.source_id,s]));let previous=null;
    $('cards').innerHTML=data.records.length?data.records.map(r=>{
      const source=sources.get(r.source_id);
      const heading=previous===r.source_id?'':`<div class="source-group"><h3>${esc(source.short_label)}</h3><p>${esc(source.type_label)} · год подборки ${r.source_year}</p></div>`;
      previous=r.source_id;
      return heading+`<article class="card"><div class="badges"><span class="badge">Из публичного источника · ${r.source_year}</span><span class="badge type">${esc(r.type_label)}</span></div><h3 ${publicTitleAttrs(r)}>${esc(publicTitle(r))}</h3><button type="button" class="card-open" data-public-detail="${esc(r.id)}">Карточка сигнала</button><div class="meta">${esc(r.source_short_label)} · ${esc(locator(r))}</div><a href="${esc(r.source_url)}" target="_blank" rel="noopener noreferrer">Открыть первоисточник →</a></article>`;
    }).join(''):'<div class="panel">Совпадений нет. Попробуйте другую формулировку или снимите фильтры.</div>';
    options('source','Все источники',data.sources,s=>s.source_id,s=>s.short_label);
    options('category','Все категории',data.categories,s=>s,s=>publicCategory(s));
    options('year','Все годы',data.years,s=>s,s=>s);
    options('kind','Все типы подборок',data.source_types,s=>s.value,s=>s.label);
    $('source-notes').innerHTML=data.sources.map(s=>`<p><b>${esc(s.short_label)} — ${esc(s.type_label)}</b> · ${s.extracted_total} записей. <a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">Открыть издание</a>.<br>${esc(s.coverage_note_ru)}</p>`).join('');
    $('link-notes').innerHTML=data.link_only_sources.map(s=>`<p><a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.title)}</a> · ${s.edition_year}. ${esc(s.reason_ru)}</p>`).join('');
    $('archive-note').textContent=`${data.archived_records} записи из подборок до 2023 года сохранены в архиве и не включаются в актуальную выдачу.`;
    syncPublicContentLanguage();
  }catch(e){
    if(e.name!=='AbortError'&&controller===active)$('cards').innerHTML=`<div class="error">${esc(e.message)}</div>`;
  }
}
$('filter-form').onsubmit=e=>{e.preventDefault();offset=0;load()};
for(const id of ['source','category','kind','year'])$(id).onchange=()=>{offset=0;if(id==='source')$('category').value='';load()};
$('prev').onclick=()=>{offset=Math.max(0,offset-15);load()};
$('next').onclick=()=>{if(offset+15<total){offset+=15;load()}};
bindPublicContentLanguage();
load();
})();
</script></body></html>'''

PUBLIC_SIGNALS_HTML = PUBLIC_SIGNALS_HTML.replace('__PUBLIC_CONTENT_CONTROLS__', PUBLIC_CONTENT_CONTROLS).replace(
    '/* PUBLIC_CONTENT_SCRIPT */', PUBLIC_CONTENT_SCRIPT).replace('</style>', PUBLIC_CONTENT_STYLE + '</style>', 1)

from saia.help_web import attach_help_widget

PUBLIC_SIGNALS_HTML = attach_help_widget(PUBLIC_SIGNALS_HTML)
