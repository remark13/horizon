"""Local searchable product help and a context-preserving help dialog."""
from html import escape
import json

from saia.help_content import HELP_ARTICLES, HELP_UPDATED, HELP_VERSION


def _script_json(value):
    return json.dumps(value, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')


HELP_STYLE = r'''
:root{--blue:#3156ad;--ink:#202b43;--muted:#68758d;--line:#dfe5ef;--soft:#f5f7fb}*{box-sizing:border-box}body{margin:0;font:15px/1.65 Inter,Arial,sans-serif;background:var(--soft);color:var(--ink)}a{color:var(--blue);text-decoration:none}a:hover{text-decoration:underline}button,input{font:inherit}button{cursor:pointer}button:focus-visible,a:focus-visible,input:focus-visible{outline:3px solid #9cb4ec;outline-offset:3px}[hidden]{display:none!important}.help-top{height:68px;padding:0 32px;background:white;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:center;gap:16px}.help-brand{display:flex;gap:16px;align-items:center;font-weight:800;font-size:19px}.help-brand span{border-left:1px solid var(--line);padding-left:16px;color:var(--ink);font-size:17px}.help-home{font-size:13px}.help-wrap{max-width:1250px;margin:auto;padding:30px 28px 64px}.help-hero h1{font-size:30px;line-height:1.25;margin:0 0 8px;letter-spacing:-.025em}.help-hero p{color:var(--muted);margin:0 0 20px;font-size:14px}.help-search-box{display:flex;background:white;border:1px solid #cbd5e5;border-radius:12px;align-items:center;gap:10px;padding:5px 6px 5px 16px;max-width:780px}.help-search-box input{border:0;outline:none;background:transparent;flex:1;min-width:0;padding:9px 0;color:var(--ink)}.help-clear{border:0;background:var(--soft);border-radius:8px;padding:8px 12px;color:var(--blue);font-size:12px}.help-layout{display:grid;grid-template-columns:235px minmax(0,1fr);gap:24px;margin-top:24px}.help-nav{align-self:start;position:sticky;top:18px}.help-nav summary{font-weight:750;font-size:13px;color:var(--muted);margin-bottom:10px;cursor:pointer}.help-nav details{border:0}.help-nav-group{margin:0 0 18px}.help-nav-group h2{font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:#8590a3;margin:12px 10px 6px}.help-nav a{display:block;padding:8px 10px;border-radius:8px;font-size:13px;line-height:1.35;color:#4e5c74}.help-nav a[aria-current=page]{color:#233f89;background:#e6edfc;font-weight:750}.help-main{min-width:0}.help-article{background:white;border:1px solid var(--line);border-radius:16px;padding:28px 32px;box-shadow:0 10px 28px #233f8907}.help-breadcrumb{font-size:12px;color:var(--muted);margin:0 0 8px}.help-article h2{font-size:27px;letter-spacing:-.025em;line-height:1.25;margin:0 0 12px;scroll-margin-top:20px}.help-article .help-summary{font-size:16px;color:#53647f;line-height:1.55;margin:0 0 24px}.help-article h3{font-size:18px;line-height:1.4;margin:25px 0 10px}.help-article h4{font-size:15px;margin:20px 0 8px}.help-article p{margin:10px 0}.help-article li{margin:8px 0}.help-article ul,.help-article ol{padding-left:22px}.help-article strong{font-weight:700}.help-article code{overflow-wrap:anywhere;font-size:13px;background:var(--soft);padding:2px 4px;border-radius:4px}.help-article table{width:100%;border-collapse:collapse;font-size:13px;display:block;overflow-x:auto}.help-article th,.help-article td{text-align:left;padding:9px;border-bottom:1px solid var(--line);vertical-align:top}.help-article th{background:var(--soft)}.help-article dl{margin:12px 0}.help-article dt{font-weight:700;margin-top:16px}.help-article dd{margin:4px 0 0;color:#53647f}.help-article blockquote,.help-article .note{margin:18px 0;padding:12px 16px;background:#f0f4fc;border-left:3px solid #819bd0;border-radius:3px;font-size:14px}.help-footer{border-top:1px solid var(--line);margin-top:28px;padding-top:14px;color:var(--muted);font-size:11px;display:flex;gap:12px;flex-wrap:wrap;justify-content:space-between}.help-results h2{font-size:21px;margin:0 0 15px}.help-result{display:block;background:white;padding:18px 22px;border:1px solid var(--line);border-radius:12px;margin-bottom:10px;color:var(--ink)}.help-result:hover{border-color:#8fa9df;text-decoration:none}.help-result h3{color:var(--blue);font-size:17px;margin:0 0 6px}.help-result p{font-size:13px;color:var(--muted);margin:0}.help-result small{font-size:11px;color:#8390a5}.help-empty{padding:24px;border:1px dashed #b7c6df;border-radius:12px;background:white}.help-empty p{color:var(--muted)}.help-status{color:var(--muted);font-size:12px;margin:8px 0 0;min-height:20px}.help-skip{position:fixed;top:-100px;left:12px;padding:10px;background:white;z-index:20}.help-skip:focus{top:10px}.help-categories{display:flex;gap:6px;flex-wrap:wrap;margin:14px 0}.help-category{border:1px solid var(--line);border-radius:20px;padding:6px 11px;background:white;color:#596984;font-size:12px}.help-category[aria-pressed=true]{background:#e6edfc;color:#233f89;border-color:#c5d3f0}.help-embedded .help-top,.help-embedded .help-hero h1,.help-embedded .help-hero>p{display:none}.help-embedded .help-wrap{padding:18px 22px 36px}.help-embedded .help-layout{grid-template-columns:185px minmax(0,1fr);gap:16px;margin-top:16px}.help-embedded .help-article{padding:23px}.help-embedded .help-article h2{font-size:24px}
@media(max-width:760px){.help-layout,.help-embedded .help-layout{grid-template-columns:1fr;gap:14px}.help-nav{position:static}.help-nav details{border:1px solid var(--line);border-radius:10px;padding:10px 14px;background:white}.help-nav summary{margin:0}.help-article{padding:22px}.help-wrap{padding:24px 16px 40px}.help-top{padding:0 16px}.help-brand{font-size:17px}.help-home{font-size:12px}.help-embedded .help-wrap{padding:14px}.help-article h2{font-size:24px}}
@media print{.help-top,.help-hero,.help-nav,.help-search-box,.help-categories,.help-status{display:none}.help-layout{display:block}.help-wrap{padding:0}.help-article{box-shadow:none;border:0}body{background:white}}
'''

HELP_SCRIPT = r'''
(()=>{'use strict';
const articles=__HELP_META__,byId=new Map(articles.map(a=>[a.id,a]));
const $=id=>document.getElementById(id),esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function normalizeHelpText(value){return String(value||'').normalize('NFKC').toLowerCase().replace(/ё/g,'е').replace(/[^\p{L}\p{N}]+/gu,' ').trim()}
function helpTokens(query){const words=normalizeHelpText(query).split(/\s+/).filter(Boolean),stop=new Set(['как','что','это','почему','не','нет','и','или','в','во','на','по','с','со','из','для','ли','у','мне','могу','я','можно','от','до','то','так','где']);const useful=words.filter(word=>!stop.has(word));return (useful.length?useful:words).map(word=>/^[а-я]+$/u.test(word)&&word.length>=5?word.slice(0,-2):word)}
function helpMatches(article,query){const text=normalizeHelpText([article.title,article.summary,...article.keywords,article.bodyText||''].join(' '));return helpTokens(query).every(word=>text.includes(word))}
function helpRelevance(article,query){const title=normalizeHelpText(article.title),keywords=normalizeHelpText(article.keywords.join(' ')),summary=normalizeHelpText(article.summary);return helpTokens(query).reduce((sum,word)=>sum+(title.includes(word)?20:keywords.includes(word)?12:summary.includes(word)?5:1),0)}
function helpSearchResults(articles,query,category='all'){return articles.filter(a=>(category==='all'||a.category===category)&&helpMatches(a,query)).sort((a,b)=>helpRelevance(b,query)-helpRelevance(a,query))}
const embedded=new URLSearchParams(location.search).get('embed')==='1';
let selected='quick-start',category='all';
if(embedded)document.body.classList.add('help-embedded');
for(const article of articles)article.bodyText=$('help-article-'+article.id).textContent;
function announceTopic(id){if(embedded&&window.parent!==window)window.parent.postMessage({type:'horizon-help-topic',topic:id},location.origin)}
function showTopic(id,push=false){
  if(!byId.has(id)){id='quick-start';$('help-status').textContent='Раздел не найден. Выберите тему в оглавлении.'}else $('help-status').textContent='';
  selected=id;category='all';$('help-search').value='';$('help-clear').hidden=true;$('help-results').hidden=true;
  for(const article of articles)$('help-article-'+article.id).hidden=article.id!==id;
  for(const link of document.querySelectorAll('.help-nav [data-help-topic]')){if(link.dataset.helpTopic===id)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current')}
  for(const button of document.querySelectorAll('[data-help-category]'))button.setAttribute('aria-pressed',String(button.dataset.helpCategory==='all'));
  if(push){history.pushState(null,'',location.pathname+location.search+'#'+encodeURIComponent(id));window.scrollTo(0,0);$('help-title-'+id).focus({preventScroll:true})}
  document.title=byId.get(id).title+' · Справка Horizon';announceTopic(id);
}
function searchHelp(){
  const query=$('help-search').value.trim();$('help-clear').hidden=!query&&category==='all';
  if(!query&&category==='all'){showTopic(selected);return}
  for(const article of articles)$('help-article-'+article.id).hidden=true;
  const matches=helpSearchResults(articles,query,category);
  $('help-status').textContent=`Найдено разделов: ${matches.length}`;
  $('help-results').hidden=false;
  $('help-results').innerHTML='<h2>Найденные разделы</h2>'+(matches.length?matches.map(a=>`<a class="help-result" href="/help#${a.id}" data-help-topic="${a.id}"><small>${esc(a.category)}</small><h3>${esc(a.title)}</h3><p>${esc(a.summary)}</p></a>`).join(''):'<div class="help-empty"><strong>Совпадений нет</strong><p>Попробуйте короткий запрос, например «оценка», «источники» или «экспертиза».</p><button class="help-clear" type="button" data-help-reset>Сбросить поиск</button></div>');
}
document.addEventListener('click',event=>{
  if(event.target.closest('.help-skip')){event.preventDefault();$('help-main').focus();return}
  const link=event.target.closest('[data-help-topic]');
  if(link&&!event.ctrlKey&&!event.metaKey&&!event.shiftKey&&!event.altKey){event.preventDefault();showTopic(link.dataset.helpTopic,true);return}
  const button=event.target.closest('[data-help-category]');if(button){category=button.dataset.helpCategory;for(const item of document.querySelectorAll('[data-help-category]'))item.setAttribute('aria-pressed',String(item===button));searchHelp()}
  if(event.target.closest('[data-help-reset]')){showTopic(selected);$('help-search').focus()}
});
$('help-search').addEventListener('input',searchHelp);
$('help-search').addEventListener('keydown',event=>{if(event.key==='Enter'&&!$('help-results').hidden){const first=$('help-results').querySelector('[data-help-topic]');if(first){event.preventDefault();showTopic(first.dataset.helpTopic,true)}}});
$('help-clear').onclick=()=>{showTopic(selected);$('help-search').focus()};
function hashTopic(){try{return decodeURIComponent(location.hash.slice(1))||'quick-start'}catch{return 'missing-topic'}}
window.addEventListener('popstate',()=>showTopic(hashTopic()));
window.addEventListener('hashchange',()=>showTopic(hashTopic()));
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&embedded){event.preventDefault();window.parent.postMessage({type:'horizon-help-close'},location.origin)}else if(event.key==='/'&&!event.ctrlKey&&!event.metaKey&&!['INPUT','TEXTAREA','SELECT'].includes(document.activeElement?.tagName)){event.preventDefault();$('help-search').focus()}});
const narrow=matchMedia('(max-width:760px)');$('help-toc').open=!narrow.matches;
showTopic(hashTopic());
})();
'''


def render_help() -> str:
    categories = list(dict.fromkeys(a['category'] for a in HELP_ARTICLES))
    navigation = ''.join('<section class="help-nav-group"><h2>' + escape(category) + '</h2>' + ''.join(
        f'<a href="/help#{escape(a["id"])}" data-help-topic="{escape(a["id"])}">{escape(a["title"])}</a>'
        for a in HELP_ARTICLES if a['category'] == category) + '</section>' for category in categories)
    articles = ''.join(
        f'<article class="help-article" id="help-article-{escape(a["id"])}" aria-labelledby="help-title-{escape(a["id"])}"'
        + ('' if a['id'] == 'quick-start' else ' hidden') + '>'
        + f'<p class="help-breadcrumb">{escape(a["category"])}</p><h2 id="help-title-{escape(a["id"])}" tabindex="-1">{escape(a["title"])}</h2>'
        + f'<p class="help-summary">{escape(a["summary"])}</p>' + a['body']
        + f'<footer class="help-footer"><span>Horizon {escape(HELP_VERSION)} · Обновлено {escape(HELP_UPDATED)}</span>'
        + f'<a href="/help#{escape(a["id"])}" target="_blank" rel="noopener">Открыть раздел отдельно ↗</a></footer></article>' for a in HELP_ARTICLES)
    metadata = [{key: a[key] for key in ('id', 'title', 'category', 'summary', 'keywords')} for a in HELP_ARTICLES]
    category_buttons = '<button type="button" class="help-category" data-help-category="all" aria-pressed="true">Все темы</button>' + ''.join(
        f'<button type="button" class="help-category" data-help-category="{escape(category)}" aria-pressed="false">{escape(category)}</button>' for category in categories)
    return ('<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Справка Horizon</title><style>' + HELP_STYLE + '</style></head><body>'
            '<a class="help-skip" href="#help-main">К содержанию</a><header class="help-top"><a class="help-brand" href="/scout">Газпромбанк <span>Horizon</span></a><a class="help-home" href="/scout">К поиску сигналов ↗</a></header>'
            '<main class="help-wrap"><section class="help-hero"><h1>Справка Horizon</h1><p>От первого запроса до обоснованного решения по сигналу.</p>'
            '<div class="help-search-box"><input type="search" id="help-search" maxlength="180" placeholder="Поиск по справке, например «оценка сигнала»" aria-label="Поиск по справке" autocomplete="off"><button type="button" class="help-clear" id="help-clear" hidden>Сбросить</button></div>'
            '<div class="help-categories" aria-label="Категории справки">' + category_buttons + '</div><p class="help-status" id="help-status" role="status" aria-live="polite"></p></section>'
            '<div class="help-layout"><nav class="help-nav" aria-label="Оглавление справки"><details id="help-toc" open><summary>Оглавление</summary>' + navigation + '</details></nav>'
            '<div class="help-main" id="help-main" tabindex="-1"><section class="help-results" id="help-results" aria-label="Результаты поиска по справке" hidden></section>' + articles + '</div></div></main><script>'
            + HELP_SCRIPT.replace('__HELP_META__', _script_json(metadata)) + '</script></body></html>')


HELP_WIDGET_STYLE = r'''
.help-context{display:inline-flex;align-items:center;justify-content:center;vertical-align:middle;width:23px;height:23px;flex-shrink:0;margin-left:6px;border:1px solid #cbd5e5;border-radius:50%;font:700 12px/1 Arial,sans-serif;color:#3156ad;background:#f5f7fb;text-decoration:none!important}.help-context:hover{background:#e6edfc}.help-context:focus-visible{outline:3px solid #9cb4ec;outline-offset:2px}.horizon-help-dialog{position:fixed;inset:0 0 0 auto;margin:0;border:0;padding:0;width:min(1020px,100vw);max-width:100vw;height:100dvh;max-height:100dvh;background:#f5f7fb;color:#202b43;box-shadow:-15px 0 50px #13264f33;overflow:hidden}.horizon-help-dialog::backdrop{background:#13264f55}.horizon-help-dialog[open]{display:flex;flex-direction:column}.horizon-help-bar{flex:none;display:flex;align-items:center;gap:14px;padding:16px 22px;background:white;border-bottom:1px solid #dfe5ef;font:700 16px/1.4 Inter,Arial,sans-serif}.horizon-help-bar a{margin-left:auto;font-size:12px;font-weight:500;color:#3156ad;text-decoration:none}.horizon-help-bar button{border:0;border-radius:9px;width:34px;height:34px;font:22px/1 Arial,sans-serif;cursor:pointer;color:#596982;background:#f0f3f9}.horizon-help-bar :focus-visible{outline:3px solid #9cb4ec;outline-offset:2px}.horizon-help-frame{border:0;display:block;width:100%;flex:1;min-height:0;background:#f5f7fb}@media(max-width:600px){.horizon-help-bar{padding:12px;gap:8px}.horizon-help-bar a{font-size:11px}}
'''

HELP_WIDGET_SCRIPT = r'''
(()=>{'use strict';
const ids=new Set(__HELP_IDS__),dialog=document.getElementById('horizon-help-dialog'),frame=document.getElementById('horizon-help-frame'),full=document.getElementById('horizon-help-full');
const close=()=>{if(dialog.open)dialog.close()};
document.addEventListener('click',event=>{
  const link=event.target.closest('a[data-horizon-help]');if(!link||event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;
  if(typeof dialog.showModal!=='function')return;
  event.preventDefault();const topic=ids.has(link.dataset.horizonHelp)?link.dataset.horizonHelp:'quick-start';
  full.href='/help#'+topic;frame.src='/help?embed=1#'+topic;dialog.showModal();document.getElementById('horizon-help-close').focus();
});
document.getElementById('horizon-help-close').onclick=close;
document.addEventListener('keydown',event=>{if(dialog.open&&event.key==='Escape'){event.preventDefault();event.stopImmediatePropagation();close()}},true);
dialog.addEventListener('click',event=>{if(event.target===dialog)close()});
window.addEventListener('message',event=>{if(event.origin!==location.origin||event.source!==frame.contentWindow)return;if(event.data?.type==='horizon-help-close')close();if(event.data?.type==='horizon-help-topic'&&ids.has(event.data.topic))full.href='/help#'+event.data.topic});
})();
'''


def attach_help_widget(html: str) -> str:
    """Attach once; authored links retain /help fallback with JS unavailable."""
    if 'id="horizon-help-dialog"' in html:
        return html
    widget = ('<dialog class="horizon-help-dialog" id="horizon-help-dialog" aria-labelledby="horizon-help-title">'
              '<div class="horizon-help-bar"><strong id="horizon-help-title">Справка Horizon</strong>'
              '<a id="horizon-help-full" href="/help" target="_blank" rel="noopener">Открыть отдельно ↗</a>'
              '<button id="horizon-help-close" type="button" aria-label="Закрыть справку">×</button></div>'
              '<iframe class="horizon-help-frame" id="horizon-help-frame" title="Справка по работе с Horizon"></iframe></dialog>'
              '<script>' + HELP_WIDGET_SCRIPT.replace('__HELP_IDS__', _script_json([a['id'] for a in HELP_ARTICLES])) + '</script>')
    return html.replace('</head>', '<style>' + HELP_WIDGET_STYLE + '</style></head>', 1).replace('</body>', widget + '</body>', 1)
