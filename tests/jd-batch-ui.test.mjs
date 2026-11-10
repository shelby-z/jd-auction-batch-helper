import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import assert from 'node:assert/strict';
const script=readFileSync(new URL('../public/jd-batch.js',import.meta.url),'utf8');
function context(extra={}) {
  const context=vm.createContext({...extra});
  vm.runInContext(script.slice(script.indexOf('function userError('),script.indexOf('async function busy(')),context);
  return context;
}
test('browser transport errors become actionable Chinese messages',async()=>{
  const ctx=context({fetch:async()=>{throw new TypeError('Failed to fetch');}});
  await assert.rejects(ctx.api('/api/templates'),error=>error.network&&error.message.includes('本机资料助手')&&!error.message.includes('Failed'));
  assert.match(ctx.userError(new Error('Load failed')),/重新双击/);
  assert.match(ctx.userError(new SyntaxError('Unexpected token')),/操作未完成/);
});
test('API failure does not expose raw English response',async()=>{
  const ctx=context({fetch:async()=>({ok:false,json:async()=>({error:'Internal server error'})})});
  await assert.rejects(ctx.api('/api/test'),/操作未完成/);
});
function polling(responses) {
  const saved=new Map([['jd-batch-job','saved']]);let calls=[],accepted=false;
  const ctx=context({localStorage:{removeItem:key=>saved.delete(key)},setTimeout:fn=>fn(),accept:()=>{accepted=true;},recent:async()=>{},showStep:async()=>{},project:{lots:[]}});
  ctx.status=()=>{};ctx.updateProgress=()=>{};
  ctx.api=async path=>{calls.push(path);if(path.includes('/projects/'))return {};const next=responses.shift();if(next instanceof Error)throw next;return next;};
  vm.runInContext(script.slice(script.indexOf('async function waitAnalysis('),script.lastIndexOf('await busy(async()=>')),ctx);
  return {ctx,saved,calls,accepted:()=>accepted};
}
test('temporary poll disconnect resumes without posting duplicate analysis',async()=>{
  const check=polling([Object.assign(new Error('网络中断'),{network:true}),{state:'completed'}]);
  await check.ctx.waitAnalysis({id:'job',projectId:'project',state:'running'});
  assert.equal(check.saved.size,0);assert.equal(check.calls.length,3);assert.ok(check.accepted());
  assert.ok(check.calls.every(path=>!path.includes('analyze')));
});
test('persistent disconnect keeps job for next-page recovery',async()=>{
  const check=polling(Array.from({length:6},()=>Object.assign(new Error('网络中断'),{network:true})));
  await assert.rejects(check.ctx.waitAnalysis({id:'job',projectId:'project',state:'running'}),/网络中断/);
  assert.equal(check.saved.size,1);assert.equal(check.accepted(),false);
});

test('progress displays measured ETA and discards stale estimates',()=>{
  const elements=Object.fromEntries(['progress-message','progress-bar','progress-percent','progress-time','progress-request'].map(id=>[id,{dataset:{}}]));
  const ctx=vm.createContext({$:id=>elements[id],Date});
  vm.runInContext(script.slice(script.indexOf('function updateProgress('),script.indexOf("$('progress-dialog').addEventListener")),ctx);
  ctx.updateProgress({message:'正在处理图片',percent:25,remainingSeconds:30,progressAt:Date.now()/1000});
  assert.equal(elements['progress-percent'].textContent,'25%');assert.match(elements['progress-time'].textContent,/30 秒/);
  ctx.updateProgress({remainingSeconds:30,progressAt:Date.now()/1000-20});
  assert.match(elements['progress-time'].textContent,/正在估算/);
  ctx.updateProgress({requestCount:2,requestStartedAt:Date.now()/1000});
  assert.match(elements['progress-request'].textContent,/第 2 次 AI 请求/);assert.equal(elements['progress-request'].hidden,false);
  ctx.updateProgress({requestCount:2,requestStartedAt:null,lastRequestSeconds:6.2});
  assert.match(elements['progress-request'].textContent,/耗时 6.2 秒/);assert.equal(elements['progress-request'].dataset.started,'');
});

test('failed empty import can retry but existing lots remain protected',()=>{
  const ctx=vm.createContext({});
  vm.runInContext(script.slice(script.indexOf('function canRetryRead('),script.indexOf('function render()')),ctx);
  const failed={analyzed:true,lots:[],files:[{kind:'document',status:'读取失败',text:''}]};
  assert.equal(ctx.canRetryRead(failed),true);
  assert.equal(ctx.canRetryRead({...failed,lots:[{id:'already-reviewed'}]}),false);
  assert.equal(ctx.canRetryRead({...failed,files:[{kind:'document',status:'已读取',text:'已有资料'}]}),false);
  assert.equal(ctx.canRetryRead(null),false);
});
