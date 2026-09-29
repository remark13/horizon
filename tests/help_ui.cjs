const vm=require('node:vm'),assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {search,widget}=JSON.parse(readFileSync(0,'utf8'));
const context={};vm.createContext(context);vm.runInContext(search,context);
const match=(article,q)=>context.helpMatches(article,q);
const article={title:'Оценка сигнала',summary:'Источники и публикации',keywords:['скор','score','уверенность'],bodyText:'Динамика расчёта'};
assert.equal(match(article,'ОЦЕНКА сигнала'),true);
assert.equal(match(article,'публикаций'),true);
assert.equal(match(article,'расчета'),true);
assert.equal(match(article,'score'),true);
assert.equal(match(article,'скор'),true);
assert.equal(match(article,'оценка отсутствует'),false);
assert.equal(match(article,'несуществующий раздел'),false);
assert.equal(match(article,'  '),true);
assert.equal(match(article,'<script>alert(1)</script>'),false);
assert.equal(match(article,'почему нет оценки'),true);
const overview={title:'Начало работы',summary:'Первый запрос',keywords:[],bodyText:'Оценка сигнала',category:'Start'};
const direct={...article,category:'Data'};
assert.equal(context.helpSearchResults([overview,direct],'оценка')[0].title,'Оценка сигнала');
assert.equal(context.helpSearchResults([overview,direct],'оценка','Start').length,1);

// Execute actual shared widget with a tiny host DOM. No navigation or page state resets.
const events={},windowEvents={};let closed=0,opened=0,focused=0;
const frame={contentWindow:{}},full={},button={focus(){focused++}};
const dialog={open:false,showModal(){this.open=true;opened++},close(){this.open=false;closed++},addEventListener(name,fn){events['dialog:'+name]=fn}};
const elements={'horizon-help-dialog':dialog,'horizon-help-frame':frame,'horizon-help-full':full,'horizon-help-close':button};
const host={document:{getElementById:id=>elements[id],addEventListener:(name,fn)=>{events[name]=fn}},window:{addEventListener:(name,fn)=>{windowEvents[name]=fn}},location:{origin:'http://localhost'},Set};
vm.runInNewContext(widget,host);
function click(topic,modified=false){let prevented=false;events.click({target:{closest:()=>({dataset:{horizonHelp:topic}})},ctrlKey:modified,preventDefault(){prevented=true}});return prevented}
assert.equal(click('scores'),true);assert.equal(frame.src,'/help?embed=1#scores');assert.equal(full.href,'/help#scores');assert.equal(opened,1);assert.equal(focused,1);
let stopped=false;events.keydown({key:'Escape',preventDefault(){},stopImmediatePropagation(){stopped=true}});assert.equal(stopped,true);assert.equal(closed,1);
assert.equal(click('scores',true),false);assert.equal(opened,1);
click('unknown');assert.equal(frame.src,'/help?embed=1#quick-start');
windowEvents.message({origin:'http://evil.test',source:frame.contentWindow,data:{type:'horizon-help-close'}});assert.equal(dialog.open,true);
windowEvents.message({origin:'http://localhost',source:{},data:{type:'horizon-help-close'}});assert.equal(dialog.open,true);
windowEvents.message({origin:'http://localhost',source:frame.contentWindow,data:{type:'horizon-help-topic',topic:'javascript:evil'}});assert.equal(full.href,'/help#quick-start');
windowEvents.message({origin:'http://localhost',source:frame.contentWindow,data:{type:'horizon-help-topic',topic:'sources'}});assert.equal(full.href,'/help#sources');
windowEvents.message({origin:'http://localhost',source:frame.contentWindow,data:{type:'horizon-help-close'}});assert.equal(dialog.open,false);
console.log('Help search and context-preservation checks passed');
