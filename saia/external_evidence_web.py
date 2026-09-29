"""Small non-technical UI for bounded external evidence probes."""

EXTERNAL_EVIDENCE_HTML = r'''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Horizon — внешние источники</title>
<style>
body{margin:0;background:#f5f7fa;color:#12233f;font:15px/1.45 Arial,sans-serif}header{background:linear-gradient(120deg,#10294c,#2468b7);color:#fff;padding:28px max(20px,calc((100% - 1050px)/2))}main{max-width:1050px;margin:22px auto;padding:0 18px 44px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:16px}section,article{background:#fff;border:1px solid #d8e1ee;border-radius:13px;padding:18px}label{display:block;font-weight:bold;font-size:12px;color:#4c6079;margin-top:10px}input,select{width:100%;box-sizing:border-box;margin-top:5px;padding:10px;border:1px solid #b9c8db;border-radius:8px;font:inherit;background:#fff}button,a.button{margin-top:14px;border:0;border-radius:8px;background:#215fb5;color:#fff;font-weight:bold;padding:11px 15px;cursor:pointer;text-decoration:none;display:inline-block}.note{background:#fff8e6;color:#704d00;border-radius:8px;padding:10px;margin:12px 0}.status{font-weight:bold;margin:12px 0}.cards{display:grid;gap:10px;margin-top:14px}.meta{color:#61758d;font-size:12px}.error{background:#fff0f0;color:#8e2020;padding:10px;border-radius:8px}.check{display:flex;gap:8px;align-items:center;font-weight:normal}.check input{width:auto;margin:0}@media(max-width:760px){.grid{grid-template-columns:1fr}}
</style></head><body>
<header><h1>Внешние источники</h1><p>Финансирование, программы, ИИ-артефакты, патенты, новости и научные связи — отдельно от рейтинга сигнала.</p></header>
<main><p><a class="button" href="/">← Основной интерфейс</a></p>
<div class="note">Количество записей в любом внешнем канале не доказывает новизну, рынок или будущий успех. Каждый источник показывает только свой ограниченный срез.</div>
<div class="grid">
<section><h2>Свежие новости · GDELT</h2><form id="news">
<label>Тема / ID<input name="topic_id" value="ai-agents" required></label>
<label>Поисковая фраза<input name="query" value='"agentic AI"' required></label>
<label>Начало<input name="start" type="date" required></label><label>Конец<input name="end" type="date" required></label>
<label>Максимум записей<input name="max_records" type="number" min="1" max="50" value="20"></label>
<label class="check"><input name="save" type="checkbox">Сохранить результат неизменяемо</label><button>Проверить новости</button></form><div id="news-result"></div></section>
<section><h2>Патенты · EPO OPS</h2><form id="patents">
<label>Тема / ID<input name="topic_id" value="ai-agents" required></label>
<label>Фраза на английском<input name="phrase" value="agentic artificial intelligence" required></label>
<label>Начало<input name="start" type="date" required></label><label>Конец<input name="end" type="date" required></label>
<label>Максимум записей<input name="max_records" type="number" min="1" max="25" value="20"></label>
<label class="check"><input name="save" type="checkbox">Сохранить результат неизменяемо</label><button>Проверить патенты</button></form><div id="patent-result"></div></section>
</div>
<div class="grid">
<section><h2>Финансирование · NIH RePORTER</h2><form id="funding">
<label>Тема / ID<input name="topic_id" value="ai-agents" required></label>
<label>Поисковая фраза<input name="query" value="agentic artificial intelligence" required></label>
<label>Начало<input name="start" type="date" required></label><label>Конец<input name="end" type="date" required></label>
<label>Максимум проектов<input name="max_records" type="number" min="1" max="50" value="10"></label>
<label class="check"><input name="save" type="checkbox">Сохранить результат неизменяемо</label><button>Проверить гранты</button></form><div id="funding-result"></div></section>
<section><h2>Программные пакеты · deps.dev</h2><form id="software">
<label>Тема / ID<input name="topic_id" value="ai-agents" required></label>
<label>Экосистема<select name="system"><option value="pypi">PyPI</option><option value="npm">npm</option><option value="maven">Maven</option><option value="go">Go</option><option value="cargo">Cargo</option><option value="nuget">NuGet</option><option value="rubygems">RubyGems</option></select></label>
<label>Точное имя пакета<input name="package" value="transformers" required></label>
<label>Начало<input name="start" type="date" required></label><label>Конец<input name="end" type="date" required></label>
<label>Максимум версий<input name="max_versions" type="number" min="1" max="100" value="10"></label>
<label class="check"><input name="save" type="checkbox">Сохранить результат неизменяемо</label><button>Проверить пакет</button></form><div id="software-result"></div></section>
<section><h2>Научные связи · Semantic Scholar</h2><form id="scholar">
<label>Тема / ID<input name="topic_id" value="ai-agents" required></label>
<label>Поисковая фраза<input name="query" value="agentic artificial intelligence" required></label>
<label>Начало<input name="start" type="date" required></label><label>Конец<input name="end" type="date" required></label>
<label>Максимум работ<input name="max_records" type="number" min="1" max="50" value="10"></label>
<label class="check"><input name="save" type="checkbox">Сохранить результат неизменяемо</label><button>Проверить связи</button></form><div id="scholar-result"></div></section>
</div>
<div class="grid">
<section><h2>Финансирование · UKRI</h2><form id="ukri">
<label>Тема / ID<input name="topic_id" value="ai-agents" required></label>
<label>Поисковая фраза на английском<input name="query" value="agentic artificial intelligence" required></label>
<label>Начало<input name="start" type="date" required></label><label>Конец<input name="end" type="date" required></label>
<label>Максимум проектов<input name="max_records" type="number" min="1" max="50" value="10"></label>
<label class="check"><input name="save" type="checkbox">Сохранить результат неизменяемо</label><button>Проверить UKRI</button></form><div id="ukri-result"></div></section>
<section><h2>Программы · EU Funding &amp; Tenders</h2><form id="eu-programmes">
<label>Тема / ID<input name="topic_id" value="ai-agents" required></label>
<label>Поисковая фраза на английском<input name="query" value="agentic artificial intelligence" required></label>
<label>Начало<input name="start" type="date" required></label><label>Конец<input name="end" type="date" required></label>
<label>Максимум карточек<input name="max_records" type="number" min="1" max="20" value="10"></label>
<label class="check"><input name="save" type="checkbox">Сохранить результат неизменяемо</label><button>Проверить программы ЕС</button></form><div id="eu-programmes-result"></div></section>
<section><h2>ИИ-артефакты · Hugging Face</h2><form id="huggingface">
<label>Тема / ID<input name="topic_id" value="ai-agents" required></label>
<label>Поисковая фраза<input name="query" value="agentic AI" required></label>
<label>Начало<input name="start" type="date" required></label><label>Конец<input name="end" type="date" required></label>
<label>Максимум на тип<input name="max_records" type="number" min="1" max="20" value="8"></label>
<label class="check"><input name="save" type="checkbox">Сохранить результат неизменяемо</label><button>Проверить модели, датасеты и Spaces</button></form><div id="huggingface-result"></div></section>
<section><h2>Клинические исследования · ClinicalTrials.gov</h2><form id="clinical-trials">
<label>Тема / ID<input name="topic_id" value="gene-editing" required></label>
<label>Поисковая фраза на английском<input name="query" value="gene editing" required></label>
<label>Начало<input name="start" type="date" required></label><label>Конец<input name="end" type="date" required></label>
<label>Максимум исследований<input name="max_records" type="number" min="1" max="50" value="10"></label>
<label class="check"><input name="save" type="checkbox">Сохранить результат неизменяемо</label><button>Проверить исследования</button></form><div id="clinical-trials-result"></div></section>
<section><h2>Медико-биологические публикации · Europe PMC</h2><form id="europe-pmc">
<label>Тема / ID<input name="topic_id" value="gene-editing" required></label>
<label>Поисковая фраза на английском<input name="query" value="gene editing" required></label>
<label>Начало<input name="start" type="date" required></label><label>Конец<input name="end" type="date" required></label>
<label>Максимум работ<input name="max_records" type="number" min="1" max="50" value="10"></label>
<label class="check"><input name="save" type="checkbox">Сохранить результат неизменяемо</label><button>Проверить публикации</button></form><div id="europe-pmc-result"></div></section>
<section><h2>Космос и авиация · NASA NTRS</h2><form id="nasa-ntrs">
<label>Тема / ID<input name="topic_id" value="small-satellites" required></label>
<label>Поисковая фраза на английском<input name="query" value="small satellite" required></label>
<label>Начало<input name="start" type="date" required></label><label>Конец<input name="end" type="date" required></label>
<label>Максимум работ<input name="max_records" type="number" min="1" max="50" value="10"></label>
<label class="check"><input name="save" type="checkbox">Сохранить результат неизменяемо</label><button>Проверить отчёты NASA</button></form><div id="nasa-ntrs-result"></div></section>
<section><h2>Госзакупки США · USAspending</h2><form id="usaspending">
<label>Тема / ID<input name="topic_id" value="unmanned-aircraft" required></label>
<label>Поисковая фраза на английском<input name="query" value="unmanned aerial vehicle" required></label>
<label>Начало<input name="start" type="date" required></label><label>Конец<input name="end" type="date" required></label>
<label>Максимум контрактов<input name="max_records" type="number" min="1" max="20" value="10"></label>
<label class="check"><input name="save" type="checkbox">Сохранить результат неизменяемо</label><button>Проверить контракты</button></form><div id="usaspending-result"></div></section>
</div>
<section style="margin-top:16px"><h2>Сохранённая история</h2><button id="history">Обновить историю</button><div id="history-result"></div></section>
</main><script>
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const iso=d=>d.toISOString().slice(0,10), now=new Date(), month=new Date(now);month.setUTCDate(month.getUTCDate()-30);const year=new Date(now);year.setUTCFullYear(year.getUTCFullYear()-2);
for(const form of document.querySelectorAll('form')){form.elements.end.value=iso(now);form.elements.start.value=iso(form.id==='news'?month:year)}
const clinicalMatchLabels={title:'название',intervention:'вмешательство',keyword:'ключевые слова',abstract:'аннотация',brief_summary:'краткое описание'};
const card=item=>`<article><h3>${esc(item.title||item.project_title||item.repo_id||item.publication_id||item.version||item.paper_id)}</h3><div class="meta">${esc(item.seen_date||item.publication_date||item.project_start_date||item.award_start_date||item.first_posted_date||item.opening_date||item.created_at||item.published_at||'дата неизвестна')} · ${esc(item.artifact_type||item.domain||item.country||item.lead_organisation||item.lead_sponsor||item.organization||item.agency||'')}</div>${item.query_match_fields?.length?`<p class="meta">Совпадение в записи: ${item.query_match_fields.map(x=>esc(clinicalMatchLabels[x]||x)).join(', ')}. Связь с технологией требует проверки.</p>`:''}${item.recipient?`<p class="meta">Получатель: ${esc(item.recipient)}</p>`:''}${item.url?`<a href="${esc(item.url)}" target="_blank" rel="noopener">Открыть источник</a>`:''}${item.applicants?.length?`<p>Заявители: ${item.applicants.map(esc).join(', ')}</p>`:''}${item.award_amount_usd?`<p>Сумма записи: ${Number(item.award_amount_usd).toLocaleString('ru-RU')} USD</p>`:''}${item.award_amount_gbp?`<p>Сумма записи: ${Number(item.award_amount_gbp).toLocaleString('ru-RU')} GBP</p>`:''}${item.authors?.length?`<p>Авторы: ${item.authors.map(esc).join(', ')}</p>`:''}</article>`;
const caveats={gdelt_doc_2_0:'Разные домены не считаются независимыми подтверждениями: одна новость может быть перепечатана.',epo_ops:'Патентная публикация не доказывает выдачу патента, внедрение или рынок; семейства пока не склеиваются автоматически.',nih_reporter:'Запись о финансировании не доказывает успех проекта; разные годы одного гранта не являются независимыми проектами.',deps_dev:'Нужна точная идентичность пакета; релизы не доказывают массовое внедрение.',semantic_scholar:'Источник пересекается с OpenAlex и arXiv; текущие цитаты непригодны для строгого ретротеста.',ukri_gtr:'Поиск UKRI ранжирован по релевантности; грант доказывает финансируемую активность, а не успех технологии.',eu_funding_tenders:'Выдача ЕС смешанная и ограниченная первой страницей; карточка программы показывает институциональный интерес, а не внедрение.',huggingface_hub:'Модели, датасеты и Spaces могут быть копиями или спамом; текущие downloads и likes нельзя переносить в прошлое.',clinicaltrials_gov:'Регистрация исследования не означает положительный результат или допуск препарата. Отобраны записи с видимым совпадением запроса; часть релевантных записей могла не попасть. Связь с темой нужно проверять.',europe_pmc:'Эти статьи и препринты могут уже присутствовать в OpenAlex или arXiv. До сопоставления DOI и версий они не считаются независимыми публикациями и не меняют рейтинг сигнала.',nasa_ntrs:'Технический отчёт NASA может уже быть в OpenAlex. Без сопоставления идентификаторов он не добавляет независимую научную публикацию и не меняет рейтинг сигнала.',usaspending:'Государственный контракт показывает закупку, а не частные инвестиции или размер рынка. Рейтинг сигнала не меняется.'};
const render=(target,data)=>{const count=data.observed_article_count??data.observed_publication_count??data.observed_project_record_count??data.observed_project_count??data.observed_topic_count??data.observed_artifact_count??data.observed_version_count??data.observed_paper_count??data.observed_study_count??data.observed_award_count;const stories=data.unique_story_count===undefined||data.unique_story_count===null?'':` · разных заголовков ${data.unique_story_count}`;const saved=data.saved?`<p>Сохранено: ${esc(data.saved.observation_id)}</p>`:'';target.innerHTML=`<p class="status">Статус: ${esc(data.status)}${count===null||count===undefined?'':` · найдено ${count}${stories}`}</p>${saved}${data.status_reason?`<div class="error">${esc(typeof data.status_reason==='string'?data.status_reason:JSON.stringify(data.status_reason))}</div>`:''}<div class="note">Этот слой не изменил научный рейтинг. ${esc(caveats[data.source]||'Результат требует отдельной интерпретации.')}</div><div class="cards">${(data.observations||[]).map(card).join('')}</div>`};
async function submit(form,target,path){const button=form.querySelector('button');button.disabled=true;target.textContent='Запрос выполняется…';const values=Object.fromEntries(new FormData(form));if(values.max_records)values.max_records=Number(values.max_records);if(values.max_versions)values.max_versions=Number(values.max_versions);values.save=Boolean(form.elements.save.checked);values.as_of=values.end;try{const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(values)});const data=await response.json();if(!response.ok)throw new Error(data.detail||JSON.stringify(data));render(target,data)}catch(error){target.innerHTML=`<div class="error">${esc(error.message)}</div>`}finally{button.disabled=false}}
document.getElementById('news').addEventListener('submit',e=>{e.preventDefault();submit(e.target,document.getElementById('news-result'),'/external-evidence/news/gdelt')});
document.getElementById('patents').addEventListener('submit',e=>{e.preventDefault();submit(e.target,document.getElementById('patent-result'),'/external-evidence/patents/epo-ops')});
document.getElementById('funding').addEventListener('submit',e=>{e.preventDefault();submit(e.target,document.getElementById('funding-result'),'/external-evidence/funding/nih-reporter')});
document.getElementById('software').addEventListener('submit',e=>{e.preventDefault();submit(e.target,document.getElementById('software-result'),'/external-evidence/software/deps-dev')});
document.getElementById('scholar').addEventListener('submit',e=>{e.preventDefault();submit(e.target,document.getElementById('scholar-result'),'/external-evidence/scholar/semantic-scholar')});
document.getElementById('ukri').addEventListener('submit',e=>{e.preventDefault();submit(e.target,document.getElementById('ukri-result'),'/external-evidence/funding/ukri')});
document.getElementById('eu-programmes').addEventListener('submit',e=>{e.preventDefault();submit(e.target,document.getElementById('eu-programmes-result'),'/external-evidence/programmes/eu-funding')});
document.getElementById('huggingface').addEventListener('submit',e=>{e.preventDefault();submit(e.target,document.getElementById('huggingface-result'),'/external-evidence/ai-artifacts/hugging-face')});
document.getElementById('clinical-trials').addEventListener('submit',e=>{e.preventDefault();submit(e.target,document.getElementById('clinical-trials-result'),'/external-evidence/clinical-trials/clinicaltrials-gov')});
document.getElementById('europe-pmc').addEventListener('submit',e=>{e.preventDefault();submit(e.target,document.getElementById('europe-pmc-result'),'/external-evidence/biomedical/europe-pmc')});
document.getElementById('nasa-ntrs').addEventListener('submit',e=>{e.preventDefault();submit(e.target,document.getElementById('nasa-ntrs-result'),'/external-evidence/aerospace/nasa-ntrs')});
document.getElementById('usaspending').addEventListener('submit',e=>{e.preventDefault();submit(e.target,document.getElementById('usaspending-result'),'/external-evidence/procurement/usaspending')});
document.getElementById('history').addEventListener('click',async()=>{const target=document.getElementById('history-result');target.textContent='Загрузка…';try{const response=await fetch('/external-evidence/observations?limit=100');const data=await response.json();if(!response.ok)throw new Error(data.detail||JSON.stringify(data));target.innerHTML=`<p>Всего: ${data.total}</p><div class="cards">${data.observations.map(v=>`<article><b>${esc(v.source)}</b> · ${esc(v.topic_id)} · ${esc(v.status)}<div class="meta">${esc(v.created_at)} · ${esc(v.observation_id)}</div><a href="/external-evidence/observations/${encodeURIComponent(v.observation_id)}" target="_blank" rel="noopener">Открыть сохранённую версию</a></article>`).join('')}</div>`}catch(error){target.innerHTML=`<div class="error">${esc(error.message)}</div>`}});
</script></body></html>'''
