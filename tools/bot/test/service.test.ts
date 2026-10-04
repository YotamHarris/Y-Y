import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { Store } from '../src/store.js';
import { agentEnvironment, authorized, loadConfig, type Config } from '../src/config.js';
import { parseResult, normalizeEvent, AgentPaused, Providers } from '../src/providers.js';
import { redact, run } from '../src/process.js';
import { assertChangeScope, statusPaths } from '../src/git.js';
import { Coordinator } from '../src/coordinator.js';
import { GitHub } from '../src/github.js';
import type { Task } from '../src/types.js';
import type { Git } from '../src/git.js';

const input=(eventId='event',kind:Task['kind']='change')=>({eventId,kind,game:'tapdemo',provider:'codex' as const,userId:'developer',channelId:'channel',prompt:'Add a point'});
const config={root:process.cwd(),data:mkdtempSync(resolve(tmpdir(),'yy-test-')),games:{tapdemo:{directory:'games/tapdemo'}},githubToken:'private-token'} as unknown as Config;
function fixture(options:{agent?:()=>Promise<any>;checks?:'pending'|'passed'|'failed';merged?:boolean;main?:string}={}) {
  const store=new Store(':memory:');
  const task=store.update(store.create(input()).id,{threadId:'thread'});
  const calls:{merge:number;dispatch:number;validate:number;review:number;cancel:number;rerun:number}={merge:0,dispatch:0,validate:0,review:0,cancel:0,rerun:0};
  const git={prepare:async()=>({worktree:'isolated',branch:'codex/task',baseSha:'base'}),commit:async()=> 'head',diff:async()=> 'diff',push:async()=>{},assertClean:async()=>{},validate:async()=>{calls.validate++;}} as unknown as Git;
  const github={main:async()=>options.main || 'base',pull:async()=>({number:1,html_url:'pr'}),pullState:async()=>({merged:options.merged || false,state:'open',head:{sha:'head'},merge_commit_sha:'merge'}),
    checks:async()=> options.checks || 'passed',merge:async()=>{calls.merge++;return 'merge';},runs:async()=>[],dispatch:async()=>{calls.dispatch++;},
    buildState:async()=>({status:'processing',url:'run'}),cancelRun:async()=>{calls.cancel++;},rerun:async()=>{calls.rerun++;}} as unknown as GitHub;
  const providers={execute:options.agent || (async(_p:unknown,o:any)=>{if(o.readonly) calls.review++;return {outcome:'completed',summary:'Done',question:'',review:o.readonly ? 'approve' : 'none'};})} as unknown as Providers;
  return {store,task,calls,git,github,coordinator:new Coordinator(config,store,git,github,providers)};
}
test('duplicates are idempotent; transcript events and notifications persist',()=>{
  const path=resolve(config.data,'persist.sqlite');let store=new Store(path);
  const a=store.create(input());assert.equal(store.create(input()).id,a.id);
  assert.equal(store.message(a.id,'user','hello','message'),true);assert.equal(store.message(a.id,'user','hello','message'),false);
  store.notify(a.id,'queued');store.close();store=new Store(path);
  assert.equal(store.get(a.id)?.prompt,'Add a point');assert.equal(store.pending().length,1);assert.match(store.context(a.id),/hello/);store.sent(store.pending()[0]!.id);assert.equal(store.pending().length,0);store.close();
});
test('restart interrupts local work and preserves remote reconciliation',()=>{
  const store=new Store(':memory:');const local=store.create(input('local'));const uploaded=store.create(input('remote'));
  store.update(local.id,{status:'implementing',worktree:'preserved'});store.update(uploaded.id,{status:'processing',runId:4});store.recover();
  assert.equal(store.get(local.id)?.status,'interrupted');assert.equal(store.get(local.id)?.worktree,'preserved');assert.equal(store.get(uploaded.id)?.status,'processing');store.close();
});
test('only both authorized users in the configured guild can act',()=>{
  const auth={guildId:'guild',users:new Set(['one','two'])};assert(authorized(auth,'guild','one'));assert(authorized(auth,'guild','two'));assert(!authorized(auth,'other','one'));assert(!authorized(auth,'guild','outsider'));assert(!authorized(auth,null,'one'));
});
test('subscription subprocess environment excludes service secrets and API billing',()=>{
  const source={PATH:'path',USERPROFILE:'home',GITHUB_TOKEN:'secret',DISCORD_TOKEN:'secret',OPENAI_API_KEY:'api',ANTHROPIC_API_KEY:'api',ANTHROPIC_AUTH_TOKEN:'override'};
  for(const provider of ['codex','claude'] as const) {const env=agentEnvironment(provider,'subscription',source);assert.equal(env.PATH,'path');assert.equal(env.GITHUB_TOKEN,undefined);assert.equal(env.OPENAI_API_KEY,undefined);assert.equal(env.ANTHROPIC_API_KEY,undefined);assert.equal(env.ANTHROPIC_AUTH_TOKEN,undefined);}
  assert.throws(()=>agentEnvironment('codex','api',{}),/requires/);
});
test('provider events and final results are normalized, never assume review approval',()=>{
  assert.deepEqual(normalizeEvent('codex',{type:'thread.started',thread_id:'session'}),{type:'session',sessionId:'session'});
  assert.equal(normalizeEvent('claude',{type:'result',is_error:true,result:'limit'})?.type,'error');
  assert.throws(()=>parseResult('looks good'),/structured/);assert.throws(()=>parseResult({outcome:'completed',summary:'ok'}),/Invalid/);
  assert.equal(parseResult({outcome:'needs_input',summary:'',question:'Which target?',review:'none'}).outcome,'needs_input');
});
test('game-task scope rejects pipelines, other games, and configuration',()=>{
  assertChangeScope(['engine/src/a.cpp','games/tapdemo/src/a.cpp','tests/a.cpp'],'games/tapdemo');
  for(const path of ['.github/workflows/checks.yml','tools/bot/a.ts','games/other/a.cpp','config/games.json']) assert.throws(()=>assertChangeScope([path],'games/tapdemo'));
});
test('implementation checks and independent review precede CI, merge, and build',async()=>{
  const f=fixture();await f.coordinator.tick();assert.equal(f.store.get(f.task.id)?.status,'awaiting_checks');assert.equal(f.calls.validate,1);assert.equal(f.calls.review,1);assert.equal(f.calls.merge,0);
  await f.coordinator.tick();assert.equal(f.calls.merge,1);assert.equal(f.store.get(f.task.id)?.status,'building');assert.equal(f.calls.dispatch,0,'main push starts distribution');f.store.close();
});
test('simultaneous ticks run a single implementation',async()=>{
  let resolveAgent!:(r:any)=>void;let invocations=0;
  const f=fixture({agent:()=>{invocations++;return new Promise(r=>{resolveAgent=r;});}});
  const first=f.coordinator.tick();await new Promise(r=>setImmediate(r));await f.coordinator.tick();assert.equal(invocations,1);
  // Complete implementation; resolve independent review separately.
  resolveAgent({outcome:'completed',summary:'ok',question:'',review:'none'});await new Promise(r=>setImmediate(r));resolveAgent({outcome:'completed',summary:'ok',question:'',review:'approve'});await first;f.store.close();
});
test('stale base returns to queue without merging',async()=>{
  const f=fixture({main:'new-base'});f.store.update(f.task.id,{status:'awaiting_checks',pr:1,headSha:'head',baseSha:'base'});await f.coordinator.tick();assert.equal(f.store.get(f.task.id)?.status,'queued');assert.equal(f.calls.merge,0);f.store.close();
});
test('restart after merge reconciles PR without a second merge',async()=>{
  const f=fixture({merged:true});f.store.update(f.task.id,{status:'merging',pr:1,headSha:'head'});await f.coordinator.tick();assert.equal(f.calls.merge,0);assert.equal(f.store.get(f.task.id)?.mergeSha,'merge');f.store.close();
});
test('authentication/limits pause rather than changing provider',async()=>{
  const f=fixture({agent:async()=>{throw new AgentPaused('Login required');}});await f.coordinator.tick();assert.equal(f.store.get(f.task.id)?.status,'waiting_input');assert.equal(f.calls.merge,0);f.store.close();
});
test('two repair attempts, then retain the failed branch',async()=>{
  let attempts=0;const f=fixture();(f.git as any).validate=async()=>{attempts++;throw new Error('broken build');};await f.coordinator.tick();assert.equal(attempts,3);assert.equal(f.store.get(f.task.id)?.status,'failed');assert.equal(f.store.get(f.task.id)?.worktree,'isolated');f.store.close();
});
test('cancelling merged work reports completion and stops remote build',async()=>{
  const f=fixture();f.store.update(f.task.id,{status:'processing',mergeSha:'merge',runId:7});await f.coordinator.cancel(f.task.id);assert.equal(f.calls.cancel,1);assert.equal(f.store.get(f.task.id)?.status,'cancelled');assert.match(f.store.pending().at(-1)!.content,/Already merged/);f.store.close();
});
test('build resume reruns the same workflow rather than allocating a new upload',async()=>{
  const f=fixture();f.store.update(f.task.id,{status:'failed',mergeSha:'merge',runId:7});await f.coordinator.resume(f.task.id);assert.equal(f.calls.rerun,1);assert.equal(f.calls.dispatch,0);assert.equal(f.store.get(f.task.id)?.runId,7);f.store.close();
});
test('delayed TestFlight processing is not reported ready',async()=>{
  const f=fixture();(f.github as any).runs=async()=>[{id:7,display_title:`iOS [tapdemo] ${f.task.id}`,html_url:'run'}];f.store.update(f.task.id,{status:'building',mergeSha:'merge'});await f.coordinator.tick();assert.equal(f.store.get(f.task.id)?.status,'processing');f.store.close();
});
test('Actions API requires all three named jobs on the requested commit',async()=>{
  const passed=['automation','windows','ios'].map(name=>({name,status:'completed',conclusion:'success'}));
  for(const scenario of [
    {head:'sha',jobs:passed,expected:'passed'},
    {head:'sha',jobs:passed.slice(0,2),expected:'pending'},
    {head:'sha',jobs:[...passed.slice(0,2),{name:'ios',status:'in_progress',conclusion:null}],expected:'pending'},
    {head:'different-sha',jobs:passed,expected:'pending'}
  ]) {
    const request=(async(input:URL|string|Request)=>{
      const url=String(input);assert(!url.includes('/check-runs'));
      if(url.includes('/jobs?')) {assert.match(url,/filter=latest/);return new Response(JSON.stringify({jobs:scenario.jobs}));}
      assert.match(url,/\/actions\/workflows\/checks.yml\/runs\?head_sha=sha/);
      return new Response(JSON.stringify({workflow_runs:[{id:7,head_sha:scenario.head,status:'completed',conclusion:'success'}]}));
    }) as typeof fetch;
    const github=new GitHub({repository:'owner/repo',githubToken:'token'},request);assert.equal(await github.checks('sha'),scenario.expected);
  }
});
test('Actions API rejects a failed job and a cancelled workflow',async()=>{
  for(const status of ['in_progress','completed']) {
    const request=(async(input:URL|string|Request)=>new Response(JSON.stringify(String(input).includes('/jobs?')
      ? {jobs:[{name:'windows',status:'completed',conclusion:'failure'}]}
      : {workflow_runs:[{id:7,head_sha:'sha',status,conclusion:status==='completed' ? 'cancelled' : null}]}))) as typeof fetch;
    const github=new GitHub({repository:'owner/repo',githubToken:'token'},request);assert.equal(await github.checks('sha'),'failed');
  }
});
test('newer Actions run supersedes an older success for the same commit',async()=>{
  const request=(async(input:URL|string|Request)=>{
    const url=String(input);
    if(url.includes('/jobs?')) {assert.match(url,/\/runs\/8\/jobs/);return new Response(JSON.stringify({jobs:[]}));}
    return new Response(JSON.stringify({workflow_runs:[{id:7,head_sha:'sha',status:'completed',conclusion:'success'},{id:8,head_sha:'sha',status:'queued',conclusion:null}]}));
  }) as typeof fetch;
  const github=new GitHub({repository:'owner/repo',githubToken:'token'},request);assert.equal(await github.checks('sha'),'pending');
});
test('process execution preserves arguments containing shell metacharacters and redacts errors',async()=>{
  const text=await run(process.execPath,['-e','console.log(process.argv[1])','hello & $(whoami)'],{cwd:process.cwd(),timeout:5000});assert.equal(text.trim(),'hello & $(whoami)');
  assert.equal(redact('token=supersecret123',{GITHUB_TOKEN:'supersecret123'}),'token=[REDACTED]');
});
test('rename scope checks both the old and new filename',()=>{
  const paths=statusPaths('R  games/tapdemo/new file.cpp\0games/tapdemo/old file.cpp\0?? engine/new.cpp\0');
  assert.deepEqual(paths,['games/tapdemo/new file.cpp','games/tapdemo/old file.cpp','engine/new.cpp']);
  assert.throws(()=>assertChangeScope(statusPaths('R  games/tapdemo/new.cpp\0tools/bot/old.ts\0'),'games/tapdemo'));
});
test('native provider adapters consume structured streams and preserve explicit auth mode',async()=>{
  for(const provider of ['codex','claude'] as const) {
    const adapterConfig={...config,auth:{codex:'subscription',claude:'subscription'},executables:{codex:'codex',claude:'claude'},timeout:1000} as Config;
    let invocation=0;
    const runner:typeof run=async(_executable,args,options)=>{
      invocation++;assert.equal(options.env?.GITHUB_TOKEN,undefined);assert.equal(options.env?.OPENAI_API_KEY,undefined);
      if(invocation===1) return provider==='codex' ? 'Logged in using ChatGPT' : '{"loggedIn":true,"authMethod":"claude.ai"}';
      assert.match(options.input!,/prompt & literal/);
      const result={outcome:'completed',summary:'ok',question:'',review:'none'};
      if(provider==='codex') {
        assert(args.includes('workspace-write'));options.onLine?.('{"type":"thread.started","thread_id":"session"}');
        options.onLine?.(JSON.stringify({type:'item.completed',item:{type:'agent_message',text:JSON.stringify(result)}}));
      } else {assert(args.includes('--permission-prompts'));options.onLine?.(JSON.stringify({type:'system',subtype:'init',session_id:'session'}));options.onLine?.(JSON.stringify({type:'result',is_error:false,structured_output:result}));}
      return '';
    };
    const result=await new Providers(adapterConfig,runner).execute(provider,{cwd:config.root,prompt:'prompt & literal',signal:new AbortController().signal});
    assert.equal(result.sessionId,'session');assert.equal(result.summary,'ok');assert.equal(invocation,2);
  }
});
test('cancellation racing an in-flight merge records the completed merge and defers build cancellation',async()=>{
  const f=fixture();f.store.update(f.task.id,{status:'awaiting_checks',pr:1,headSha:'head',baseSha:'base'});
  let resolveMerge!:(sha:string)=>void;(f.github as any).merge=()=>new Promise<string>(r=>{resolveMerge=r;});
  const operation=f.coordinator.tick();await new Promise(r=>setImmediate(r));assert.equal(f.store.get(f.task.id)?.status,'merging');
  await f.coordinator.cancel(f.task.id);resolveMerge('already-merged');await operation;
  assert.equal(f.store.get(f.task.id)?.status,'cancelled');assert.equal(f.store.get(f.task.id)?.mergeSha,'already-merged');assert.equal(f.store.get(f.task.id)?.cancelPending,true);assert.equal(f.calls.dispatch,0);f.store.close();
});
test('validated candidate merges without requiring branch protection',async()=>{
  const f=fixture();
  f.store.update(f.task.id,{status:'awaiting_checks',pr:1,headSha:'head',baseSha:'base'});await f.coordinator.tick();
  assert.equal(f.calls.merge,1);assert.equal(f.store.get(f.task.id)?.mergeSha,'merge');f.store.close();
});
test('main advancing after checks is refreshed before merge',async()=>{
  const f=fixture();let reads=0;(f.github as any).main=async()=>++reads===1 ? 'base' : 'new-base';
  f.store.update(f.task.id,{status:'awaiting_checks',pr:1,headSha:'head',baseSha:'base'});await f.coordinator.tick();
  assert.equal(f.calls.merge,0);assert.equal(f.store.get(f.task.id)?.status,'queued');f.store.close();
});
test('failed signing never reports uploaded or ready',async()=>{
  const request=(async(input:URL|string|Request)=>{
    const data=String(input).includes('/jobs') ? {jobs:[{name:'TestFlight (tapdemo)',status:'completed',conclusion:'failure',steps:[{name:'Configure signing',conclusion:'failure'}]}]} : {status:'completed',conclusion:'failure',html_url:'run'};
    return new Response(JSON.stringify(data),{status:200});
  }) as typeof fetch;
  const github=new GitHub({repository:'owner/repo',githubToken:'token'},request);assert.equal((await github.buildState(1,'tapdemo')).status,'failed');
});
test('workflow dispatch reconciliation finds a task even when main advanced before dispatch',async()=>{
  const request=(async(input:URL|string|Request)=>{
    const filtered=String(input).includes('head_sha=');
    return new Response(JSON.stringify({workflow_runs:filtered ? [] : [{id:7,head_sha:'new-main',display_title:'iOS [tapdemo] unique-task'}]}),{status:200});
  }) as typeof fetch;
  const github=new GitHub({repository:'owner/repo',githubToken:'token'},request);
  assert.equal((await github.runs('pinned-old-main'))[0]?.id,7);
});
test('known workflows reconcile directly after a long offline period',async()=>{
  const f=fixture();(f.github as any).runs=async()=>{throw new Error('Should not relist a known run');};
  f.store.update(f.task.id,{status:'uploaded',mergeSha:'merge',runId:7});await f.coordinator.tick();
  assert.equal(f.store.get(f.task.id)?.status,'processing');assert.equal(f.calls.dispatch,0);f.store.close();
});
