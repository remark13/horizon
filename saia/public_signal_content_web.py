"""Shared content-only language control for the catalogue and public cards."""
import json

from saia.public_signal_i18n import load_translations


PUBLIC_CONTENT_CONTROLS = '''<div class="public-content-language" role="group" aria-label="Язык содержимого"><span>Содержание</span><button type="button" data-public-content-language="ru" aria-pressed="true">Русский</button><button type="button" data-public-content-language="en" aria-pressed="false">English</button></div>'''

PUBLIC_CONTENT_STYLE = r'''
.jrc-content h4{font-size:13px;margin:16px 0 5px}.jrc-content p{font-size:13px;line-height:1.6}.jrc-content .form-note{font-size:11px;color:#68758d}.jrc-applications{background:#f5f7fb;border-radius:8px;padding:9px 11px;color:#3156ad}.jrc-details{margin:12px 0;font-size:12px}.jrc-details summary{cursor:pointer;color:#3156ad;font-weight:650}.jrc-details li{margin:5px 0}.jrc-metrics{display:grid;grid-template-columns:1fr 1fr;gap:12px}.jrc-metrics div{background:#f5f7fb;padding:12px;border-radius:9px}.jrc-metrics dt{font-size:11px;color:#68758d}.jrc-metrics dd{font-size:21px;font-weight:700;color:#202b43;margin:4px 0 0}
.public-content-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-bottom:22px}.public-content-heading p.lead{margin-bottom:0}.public-content-heading>.public-content-language{margin-top:4px}
.public-content-language{display:inline-flex;align-items:center;gap:4px;flex-wrap:wrap;flex-shrink:0}.public-content-language>span{font-size:12px;color:#68758d;margin-right:5px}.public-content-language button{font:600 12px/1.4 Inter,Arial,sans-serif;border:1px solid #dfe5ef;background:white;color:#3156ad;border-radius:8px;padding:7px 11px;cursor:pointer}.public-content-language button[aria-pressed="true"]{background:#3156ad;color:white;border-color:#3156ad}.public-content-language button:focus-visible{outline:3px solid #b8cbf7;outline-offset:2px}.public-content-language button:hover{border-color:#3156ad}
'''


def _script_json(value):
    # Even a future malicious catalogue title cannot close the inline script.
    return json.dumps(value, ensure_ascii=False).replace("<", "\\u003c").replace(
        ">", "\\u003e").replace("&", "\\u0026").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


PUBLIC_CONTENT_SCRIPT = r'''
const publicContentControlMarkup=__PUBLIC_CONTROLS_JSON__;
const publicContentTitlesRu=__PUBLIC_TITLES_JSON__;
const publicContentCategoriesRu=__PUBLIC_CATEGORIES_JSON__;
const publicContentLanguageKey='saia.public-content-language.v1';
let publicContentLang='ru';
let publicContentStorageBound=false;
try{const saved=localStorage.getItem(publicContentLanguageKey);if(saved==='en'||saved==='ru')publicContentLang=saved}catch{}
const publicTitleRu=r=>r.title_ru||(Object.hasOwn(publicContentTitlesRu,r.title)?publicContentTitlesRu[r.title]:r.title);
const publicTitle=r=>publicContentLang==='en'?r.title:publicTitleRu(r);
const publicCategoryRu=value=>Object.hasOwn(publicContentCategoriesRu,value)?publicContentCategoriesRu[value]:value;
const publicCategory=value=>publicContentLang==='en'?value:publicCategoryRu(value);
const publicExplanationHtml=r=>(r.source_explanations||[]).map(value=>value.html||'').join('');
const publicTitleAttrs=r=>`data-public-content data-public-en="${esc(r.title)}" data-public-ru="${esc(publicTitleRu(r))}" lang="${publicContentLang}"`;
const publicCategoryAttrs=value=>`data-public-content data-public-en="${esc(value)}" data-public-ru="${esc(publicCategoryRu(value))}" lang="${publicContentLang}"`;
const publicSelectionAttrs=r=>`data-public-content-aria data-public-en="${esc(r.title)}" data-public-ru="${esc(publicTitleRu(r))}"`;
function syncPublicContentLanguage(){
  document.querySelectorAll('[data-public-content-language]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.publicContentLanguage===publicContentLang)));
  document.querySelectorAll('[data-public-content]').forEach(node=>{node.textContent=(publicContentLang==='en'?node.dataset.publicEn:node.dataset.publicRu)||node.dataset.publicEn||'';node.lang=publicContentLang});
  document.querySelectorAll('[data-public-content-aria]').forEach(node=>node.setAttribute('aria-label','Выбрать '+((publicContentLang==='en'?node.dataset.publicEn:node.dataset.publicRu)||node.dataset.publicEn||'')));
  document.querySelectorAll('[data-public-brief]').forEach(node=>node.setAttribute('href',node.dataset.publicBrief+'?lang='+publicContentLang));
}
function bindPublicContentLanguage(){
  document.querySelectorAll('[data-public-content-language]').forEach(button=>{button.onclick=()=>{publicContentLang=button.dataset.publicContentLanguage==='en'?'en':'ru';try{localStorage.setItem(publicContentLanguageKey,publicContentLang)}catch{}syncPublicContentLanguage()}});
  if(!publicContentStorageBound){window.addEventListener('storage',event=>{if(event.key===publicContentLanguageKey||event.key===null){publicContentLang=event.key!==null&&event.newValue==='en'?'en':'ru';syncPublicContentLanguage()}});publicContentStorageBound=true}
  syncPublicContentLanguage();
}
'''.replace('__PUBLIC_CONTROLS_JSON__', _script_json(PUBLIC_CONTENT_CONTROLS)).replace('__PUBLIC_TITLES_JSON__', _script_json(load_translations()['titles'])).replace(
    '__PUBLIC_CATEGORIES_JSON__', _script_json(load_translations()['categories']))
