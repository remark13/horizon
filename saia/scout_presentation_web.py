"""Explicit model choice and referenced Russian draft explanations in cards."""

PRESENTATION_SCRIPT = r'''
let presentationModels=null;
let selectedPresentationModel='';
try{selectedPresentationModel=localStorage.getItem('saia-presentation-model-v1')||''}catch{}
function presentationControls(){return `<div class="block" id="presentation-block"><div class="channel-header"><h3>Описание и ценность</h3><span id="presentation-status" class="badge gray">Пояснение к публикациям</span></div><div id="presentation-content" class="empty">Русское пояснение ещё не подготовлено.</div><details class="context-settings"><summary>Подготовить или обновить описание</summary><p>Локальная модель пересказывает найденные публикации. Научный рейтинг не меняется.</p><div class="context-query-row"><select class="field" id="presentation-model" aria-label="Модель пояснения"><option value="">Выберите модель</option></select><a class="help-context" href="/help#models" data-horizon-help="models" aria-label="Справка о моделях пояснения">?</a><button class="button light small" id="presentation-generate">Подготовить</button></div><p class="form-note" id="presentation-message"></p></details></div>`}
function presentationClaim(name,claim,evidence){const titles={description:'Суть',problem:'Какую проблему решает',advantage:'В чём преимущество',case:'Пример исследования'};if(!claim)return `<div style="margin-top:13px"><h3>${titles[name]}</h3><p class="form-note">В выбранных фрагментах публикаций не установлено.</p></div>`;const refs=claim.evidence_ids.map(id=>{const row=evidence.find(e=>e.id===id);const u=(row?.sources||[]).map(s=>url(s.url)).find(Boolean);return u?`<a href="${esc(u)}" title="${esc(row.title)}" target="_blank" rel="noopener noreferrer">[${id}]</a>`:`[${id}]`}).join(' ');return `<div style="margin-top:13px"><h3>${titles[name]}</h3><p>${esc(claim.text)} ${refs}</p></div>`}
function showPresentation(value,c){const box=$('presentation-content');if(!box)return;box.classList.remove('empty');box.innerHTML='<p class="form-note">Машинный черновик: термины и выводы могут быть неточными.</p>'+['description','problem','advantage','case'].map(name=>presentationClaim(name,value.content[name],value.evidence)).join('')+`<p class="form-note">Машинное пояснение · ${esc(value.model)}. Ссылки проверены по составу карточки; точность пересказа требует проверки.</p><details class="source-passport"><summary>Оригинальные названия источников</summary>${value.evidence.map(e=>`<p>[${e.id}] ${esc(e.title)} · ${fmtDate(e.published_at)}</p>`).join('')}</details>`;$('presentation-status').textContent='Машинный черновик';$('drawer-title').textContent=value.content.title_ru;const original=document.createElement('p');original.className='source-meta';original.textContent='Исходное название: '+c.label;$('presentation-original')?.remove();original.id='presentation-original';$('drawer-title').after(original)}
async function loadCardPresentation(c,generate=false){
  const mission=state.mission,score=state.score,status=$('presentation-status'),message=$('presentation-message'),select=$('presentation-model'),button=$('presentation-generate');if(!status)return;
  const current=()=>state.current?.candidate_id===c.candidate_id&&state.mission===mission&&state.score===score&&$('presentation-status')===status;
  try{
    if(!presentationModels)presentationModels=await request('/presentation-models');if(!current())return;
    select.innerHTML='<option value="">Выберите модель</option>'+presentationModels.models.map(m=>`<option value="${esc(m.id)}">${esc(m.label)}</option>`).join('');
    if(presentationModels.models.some(m=>m.id===selectedPresentationModel))select.value=selectedPresentationModel;
    button.disabled=!presentationModels.models.length;
    const unavailableMessage=!presentationModels.models.length?'Локальная модель пока недоступна. Исходные публикации остаются доступны.':'';
    message.textContent=unavailableMessage;
    select.onchange=()=>{selectedPresentationModel=select.value;try{localStorage.setItem('saia-presentation-model-v1',selectedPresentationModel)}catch{}};
    const params=new URLSearchParams({score_run_id:String(score)});let packet;
    if(generate){
      const model=selectedPresentationModel;if(!model){message.textContent='Сначала выберите модель.';return}
      button.disabled=true;status.textContent='Готовлю пояснение…';message.textContent='Это может занять около минуты.';
      packet=await post(`/signals/${encodeURIComponent(mission)}/${c.candidate_id}/presentation?${params}`,{model});
    }else packet=await request(`/signals/${encodeURIComponent(mission)}/${c.candidate_id}/presentation?${params}`);
    if(!current())return;const value=packet.presentation;
    if(value){if(value.binding.composition_sha256!==c.composition_sha256)throw Error('Presentation composition mismatch');showPresentation(value,c)}else status.textContent='Ещё не подготовлено';
    message.textContent=unavailableMessage;
  }catch(e){if(current()){status.textContent='Пояснение не подготовлено';message.textContent='Не удалось подготовить пояснение. Выберите другую модель или повторите позже.'}console.error('Presentation unavailable',e)}
  finally{if(current())button.disabled=!presentationModels?.models?.length}
}
'''
