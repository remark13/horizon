// Minimal form DOM for behavioural checks. Production function bodies come from Python.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const {readFileSync} = require('node:fs');
const {script} = JSON.parse(readFileSync(0, 'utf8'));
const plain = value => JSON.parse(JSON.stringify(value));

function fixture() {
  let nodes = {}, uuid = 0;
  const state = {mission:'m', score:12, selected:new Set([7,8]), selectedPublic:new Set(['p']), requests:[]};
  const calls = [], notices = [];
  function element(id) {
    return {id, value: id==='dispatch-by'?'Scout':id==='dispatch-to'?'Experts':'',
      isConnected:true, disabled:false, textContent:'', focus(){}, reportValidity(){return true},
      set innerHTML(html) { for(const [,key] of html.matchAll(/id="([^"]+)"/g)) nodes[key]=element(key); }};
  }
  const context = {
    state, console:{error(){}}, crypto:{randomUUID:()=>`operation-${++uuid}`},
    all:()=>[...Array.from({length:20},(_,i)=>({card:{candidate_id:i+1,label:`Topic ${i+1}`}})),
      {item_kind:'public_signal',public_signal:{id:'p',title:'Public <signal>'}}],
    isPublic:q=>q.item_kind==='public_signal', publicTitle:r=>r.title,
    esc:v=>String(v).replaceAll('<','&lt;').replaceAll('>','&gt;'),
    $:id=>nodes[id], notice:(...args)=>notices.push(args), render(){}, expertView(){},
    openDrawer(kicker, html) { nodes={};element('root').innerHTML=html;context.html=html; },
    post:async(path,body)=>{calls.push(plain(body));return {...plain(body),request_id:body.operation_id};},
  };
  vm.createContext(context);vm.runInContext(script, context);
  return {context,state,calls,notices,get: id=>nodes[id],
    submit:()=>nodes['dispatch-form'].onsubmit({preventDefault(){}}),
    run:code=>vm.runInContext(code,context)};
}

(async()=>{
  // Single sends must not absorb unrelated bulk selections.
  let f=fixture();f.run('dispatchCandidate(7)');await f.submit();
  assert.deepEqual(f.calls[0].candidate_ids,[7]);assert.deepEqual(f.calls[0].public_signal_ids,[]);
  assert.deepEqual([...f.state.selected],[8]);assert.deepEqual([...f.state.selectedPublic],['p']);
  assert.match(f.context.html,/Topic 7/);assert.doesNotMatch(f.context.html,/Topic 8/);
  assert.ok(f.get('dispatch-view'));assert.equal(f.state.requests.length,1);

  f=fixture();f.run("dispatchPublic('p')");await f.submit();
  assert.deepEqual(f.calls[0].candidate_ids,[]);assert.deepEqual(f.calls[0].public_signal_ids,['p']);
  assert.equal(f.state.selected.size,2);assert.equal(f.state.selectedPublic.size,0);
  assert.match(f.context.html,/Public &lt;signal&gt;/);

  // Freeze IDs and run when the form opens, including a mixed request.
  f=fixture();f.run('dispatchForm()');f.state.selected.add(9);f.state.score=13;
  await f.submit();assert.equal(f.calls[0].score_run_id,12);
  assert.deepEqual(f.calls[0].candidate_ids,[7,8]);assert.deepEqual(f.calls[0].public_signal_ids,['p']);
  assert.deepEqual([...f.state.selected],[7,8,9]);assert.deepEqual([...f.state.selectedPublic],['p']);

  // Identical retry is idempotent. Edited payload obtains a new key.
  f=fixture();let attempts=0;
  f.context.post=async(path,body)=>{f.calls.push(plain(body));if(++attempts<3)throw Error('offline');return {...body,request_id:body.operation_id};};
  f.run('dispatchCandidate(7)');await f.submit();await f.submit();
  assert.equal(f.calls[0].operation_id,f.calls[1].operation_id);
  assert.deepEqual([...f.state.selected],[7,8]);assert.equal(f.get('dispatch-send').disabled,false);
  f.get('dispatch-to').value='Other experts';await f.submit();
  assert.notEqual(f.calls[1].operation_id,f.calls[2].operation_id);
  await f.submit();assert.equal(f.calls.length,3); // saved forms cannot send twice

  // A response for an old drawer must not modify a newly opened form or its notice.
  for(const fail of [false,true]){
    f=fixture();let settle;
    f.context.post=(path,body)=>new Promise((resolve,reject)=>{settle=()=>fail?reject(Error('offline')):resolve({...body,request_id:'old'});});
    f.run('dispatchCandidate(7)');const pending=f.submit();f.run('dispatchCandidate(8)');
    const newMessage=f.get('dispatch-state'),newButton=f.get('dispatch-send');settle();await pending;
    assert.equal(newMessage.textContent,'');assert.equal(newButton.disabled,false);assert.equal(f.notices.length,0);
    assert.equal(f.get('dispatch-state'),newMessage);
  }

  // The combined 15-item limit includes public records, and invalid IDs cannot dispatch.
  f=fixture();f.state.selected=new Set(Array.from({length:15},(_,i)=>i+1));f.run('dispatchForm()');
  assert.equal(f.get('dispatch-form'),undefined);assert.match(f.notices[0][0],/не более 15/);
  f.state.selected.delete(15);f.run('dispatchForm()');await f.submit();assert.equal(f.calls[0].candidate_ids.length,14);
  f=fixture();f.run('dispatchCandidate(999)');assert.equal(f.get('dispatch-form'),undefined);
  f.run('dispatchCandidate(7)');f.get('dispatch-to').value='   ';await f.submit();assert.equal(f.calls.length,0);

  // Prevent double-click submission while a request is pending.
  f=fixture();let resolve;
  f.context.post=(path,body)=>{f.calls.push(plain(body));return new Promise(done=>{resolve=()=>done({...body,request_id:'one'});});};
  f.run('dispatchCandidate(7)');const first=f.submit();await f.submit();assert.equal(f.calls.length,1);resolve();await first;
  console.log('Dispatch behaviour checks passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
