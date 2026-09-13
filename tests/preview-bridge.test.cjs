const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

function bridge(embedded = true, autoHeight = false) {
  const handlers = {}, messages = [];
  const events = {};
  const properties = {};
  let height = 2400.2, resize;
  const content = {getBoundingClientRect:()=>({height})};
  const window = {addEventListener(name, handler){(events[name] ??= []).push(handler)}, parent:{postMessage:(data,origin)=>messages.push({data,origin})}};
  if (!embedded) window.parent = window;
  const document = {
    documentElement:{classList:{add(){},toggle(){}},style:{setProperty:(name,value)=>{properties[name]=value}}},
    createElement:()=>({}),head:{append(){}},
    querySelector:selector=>selector === '.inner' ? content : null,
    addEventListener:(name,handler,capture)=>{handlers[name]={handler,capture}}
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../web/static/preview-bridge.js'),'utf8'), {
    window,document,URL,URLSearchParams,
    ResizeObserver:class {constructor(callback){resize=callback} observe(){}},
    location:{search:'?previewEmbed=1'+(autoHeight?'&autoHeight=1':''),hash:'#explore/articles',origin:'http://localhost:8023'}
  });
  return {handlers,messages,properties,
    receive:(height, trusted=true)=>events.message?.forEach(fn=>fn({origin:trusted?'http://localhost:8023':'https://other.example',source:window.parent,data:{type:'sab-preview-theme',theme:'light',viewportHeight:height}})),
    load:()=>events.load?.forEach(fn=>fn()),resize:value=>{height=value;resize?.()}};
}
test('embedded list sizing follows the host viewport, ignoring invalid or untrusted heights',()=>{
  const frame=bridge(true,true);
  frame.receive(900);
  assert.equal(frame.properties['--host-viewport-height'],'900px');
  frame.receive(640);
  for(const value of [-1,0,'1000',Infinity]) frame.receive(value);
  frame.receive(2000,false);
  assert.equal(frame.properties['--host-viewport-height'],'640px');
});
test('auto-height reports content growth and shrink without repeating unchanged heights',()=>{
  const frame = bridge(true,true);
  frame.load();
  frame.resize(3200);
  frame.resize(3200);
  frame.resize(450);
  assert.deepEqual(frame.messages.filter(m=>m.data.type==='sab-preview-height').map(m=>m.data.height),[2401,3200,450]);
});
test('fixed-height embeds and standalone pages do not publish heights',()=>{
  for (const frame of [bridge(true,false),bridge(false,true)]) {
    frame.load();frame.resize(3200);
    assert.equal(frame.messages.filter(m=>m.data.type==='sab-preview-height').length,0);
  }
});
test('embedded article click opens the host detail instead of the inner drawer',()=>{
  const {handlers,messages}=bridge();
  let prevented=false,stopped=false;
  handlers.click?.handler({target:{closest:()=>({dataset:{item:'124072'}})},
    preventDefault:()=>{prevented=true},stopImmediatePropagation:()=>{stopped=true}});
  assert.equal(messages.length,1,'article must be forwarded to the host');
  assert.equal(messages[0].data.type,'sab-preview-item');
  assert.equal(messages[0].data.id,124072);
  assert.equal(messages[0].origin,'http://localhost:8023');
  assert.equal(handlers.click.capture,true);
  assert.equal(prevented&&stopped,true,'the nested drawer must not open as well');
});
test('non-article and invalid IDs are not intercepted; standalone site is untouched',()=>{
  const {handlers,messages}=bridge();
  for(const target of [null,{dataset:{item:'-1'}},{dataset:{item:'bad'}},{dataset:{item:'1.2'}}]) {
    handlers.click?.handler({target:{closest:()=>target},preventDefault(){throw Error('intercepted')},stopImmediatePropagation(){throw Error('intercepted')}});
  }
  assert.equal(messages.length,0);
  assert.equal(bridge(false).handlers.click,undefined);
});
