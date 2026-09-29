"""Explainable assessment and bounded automatic enrichment in the scout UI."""

ASSESSMENT_STYLE = r'''
.drawer{width:min(650px,100vw)}.assessment-summary{display:flex;align-items:center;justify-content:space-between;gap:12px}.assessment-summary strong{display:block;font-size:31px;color:var(--blue-dark);margin:7px 0}.assessment-summary small{font-size:13px;font-weight:500;color:var(--muted)}.assessment-caption{font-size:12px;color:var(--muted)}.assessment-summary p{margin:0;font-size:12px}.assessment-radar{max-width:400px;margin:0 auto}.assessment-radar svg{display:block;width:100%;height:auto}.contribution-list{display:grid;gap:10px;margin:15px 0}.contribution-item{display:grid;grid-template-columns:85px 1fr 55px;align-items:center;gap:10px;font-size:12px}.contribution-item strong{text-align:right}.contribution-track{height:10px;background:var(--soft);border-radius:8px;overflow:hidden}.contribution-track i{display:block;height:100%;border-radius:8px}.assessment-metrics{display:grid;grid-template-columns:1fr auto;gap:8px;font-size:12px}.assessment-metrics dd{margin:0;color:var(--ink);font-weight:650}.assessment-validation{padding:13px;background:#edf2fc;border-radius:10px;font-size:12px}.assessment-validation p{margin:6px 0 0}.assessment-trend{border:1px solid var(--line);border-radius:12px;padding:12px;margin:12px 0}.assessment-trend h4{margin:0 0 12px;font-size:13px}.assessment-trend svg{width:100%;display:block}.assessment-values{border-collapse:collapse;width:100%;font-size:11px}.assessment-values th,.assessment-values td{padding:6px;text-align:left;border-bottom:1px solid var(--line)}.assessment-values th{color:var(--muted)}.evidence-progress{font-size:12px;color:var(--muted);margin:10px 0;min-height:16px}.list-score{font-size:17px}.score-detail{font-size:10px;color:var(--muted);margin-top:5px;max-width:185px;line-height:1.5}.assessment-retry{margin-top:10px}
'''

ASSESSMENT_SCRIPT = r'''
const enrichedCandidates=new Set();
let enrichmentActive=false;
let enrichmentPending=false;
const scoreText=a=>validNumber(a?.overall_score)?a.overall_score.toFixed(1).replace('.',','):'—';
function setEvidenceProgress(message=''){const status=$('evidence-progress');status.textContent=message;status.classList.toggle('hidden',!message)}
function assessmentControls(q){return `<div class="block" id="card-assessment"><h3>Оценка и основания сигнала <a class="help-context" href="/help#scores" data-horizon-help="scores" aria-label="Как рассчитывается оценка сигнала">?</a></h3><div id="assessment-content">${q.assessment?.visualization_html||'<div class="empty">Подготавливаю понятный расчёт…</div>'}</div><button class="button ghost small assessment-retry" id="assessment-refresh" type="button">Обновить основания и оценку</button><p class="form-note" id="assessment-message"></p></div>`}
function rankAssessments(){const groups={growth_observed:0,insufficient_data:1,mixed_evidence:2,growth_not_confirmed:3};state.packet.queue.sort((a,b)=>(groups[a.screening?.state]??1)-(groups[b.screening?.state]??1)||(a.failed_checks||[]).length-(b.failed_checks||[]).length||((validNumber(b.assessment?.overall_score)?b.assessment.overall_score:-1)-(validNumber(a.assessment?.overall_score)?a.assessment.overall_score:-1))||(a.scientific_rank||0)-(b.scientific_rank||0)||String(a.candidate_id).localeCompare(String(b.candidate_id)));state.packet.queue.forEach((q,i)=>q.rank=i+1);rebuildResultItems()}
async function refreshAssessment(c){const mission=state.mission,score=state.score,box=$('assessment-content');if(!box)return;const isCurrent=()=>state.current?.candidate_id===c.candidate_id&&state.mission===mission&&state.score===score&&$('assessment-content')===box;try{const value=await request(`/signals/${encodeURIComponent(mission)}/${c.candidate_id}/assessment?score_run_id=${score}`);if(!isCurrent())return;if(value.composition_sha256!==c.composition_sha256)throw Error('Assessment composition mismatch');const q=state.packet.queue.find(q=>q.card.candidate_id===c.candidate_id);if(q)q.assessment=value;rankAssessments();box.innerHTML=value.visualization_html;if($('card-overall-score'))$('card-overall-score').textContent=scoreText(value)+' / 100';render()}catch{if(isCurrent()&&$('assessment-message'))$('assessment-message').textContent='Не удалось обновить оценку. Научные материалы сохранены.'}}
async function enrichVisible(force=false){
  if(!state.packet)return;
  if(enrichmentActive){enrichmentPending=true;return}
  const mission=state.mission,score=state.score,key=id=>`${mission}|${score}|${id}`;
  const ids=filtered().slice(state.page*15,state.page*15+15).filter(q=>!isPublic(q)).map(q=>q.card.candidate_id).filter(id=>force||!enrichedCandidates.has(key(id)));
  if(!ids.length)return;enrichmentActive=true;setEvidenceProgress('Обновляю данные…');
  try{
    const packet=await post(`/scout-results/${encodeURIComponent(mission)}/enrich?score_run_id=${score}`,{candidate_ids:ids});
    if(state.mission!==mission||state.score!==score)return;
    if(packet.score_run_id!==score)throw Error('Score mismatch');state.packet=packet;
    for(const row of packet.enrichment?.cards||[])if(row.status==='collected')enrichedCandidates.add(key(row.candidate_id));
    render();setEvidenceProgress();
    const current=state.current;if(current?.candidate_id){refreshAssessment(current);loadCardContext(current)}
  }catch{if(state.mission===mission&&state.score===score)setEvidenceProgress('Не удалось обновить дополнительные данные. Попробуйте ещё раз.')}
  finally{enrichmentActive=false;if(enrichmentPending){enrichmentPending=false;queueMicrotask(()=>enrichVisible())}}
}
'''
