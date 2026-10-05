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
import { reply, answer, hearQuick } from '../src/conversation.js';
import { parsePlan } from '../src/providers.js';
import { managerOutlook } from '../src/manager-state.js';

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

test('two isolated workers fill available slots while another task remains in flight',async()=>{
  const f=fixture();const pending=new Map<string,(r:any)=>void>();const paths:string[]=[];
  (f.git as any).prepare=async(t:Task)=>{paths.push(t.id);return {worktree:t.id,branch:'codex/'+t.id,baseSha:'base'};};
  const providers={execute:async(_p:unknown,o:any)=>o.readonly ? {outcome:'completed',summary:'Reviewed',question:'',review:'approve'} : new Promise(r=>pending.set(o.cwd,r))} as unknown as Providers;
  const manager=new Coordinator(config,f.store,f.git,f.github,providers);
  const second=f.store.update(f.store.create(input('second')).id,{threadId:'second-thread'});
  const third=f.store.update(f.store.create(input('third')).id,{threadId:'third-thread'});
  const firstRun=manager.tick();await new Promise(r=>setImmediate(r));
  assert.equal(pending.size,2);assert.equal(new Set(paths).size,2);assert.equal(f.store.get(third.id)?.status,'queued');
  pending.get(f.task.id)!({outcome:'completed',summary:'First',question:'',review:'none'});await new Promise(r=>setImmediate(r));
  const fill=manager.tick();await new Promise(r=>setImmediate(r));assert(pending.has(third.id));assert.equal(f.store.get(second.id)?.status,'implementing');
  pending.get(second.id)!({outcome:'completed',summary:'Second',question:'',review:'none'});pending.get(third.id)!({outcome:'completed',summary:'Third',question:'',review:'none'});
  await Promise.all([firstRun,fill]);assert.equal(f.calls.validate,3);f.store.close();
});

test('pause finishes active work and keeps planning and quick chat available',async()=>{
  let finish!:(r:any)=>void;
  const f=fixture({agent:async()=>new Promise(r=>{finish=r;})});
  const operation=f.coordinator.tick();await new Promise(r=>setImmediate(r));await f.coordinator.setMode('paused');
  const queued=f.store.update(f.store.create(input('queued')).id,{threadId:'next'});
  const ask=f.store.update(f.store.create(input('ask','ask')).id,{source:'multica'});
  // Replace only the separate reader coordinator's adapter: the working invocation remains untouched.
  const reader=new Coordinator(config,f.store,f.git,f.github,{execute:async()=>({outcome:'completed',summary:'Answer',question:'',review:'none'})} as unknown as Providers);
  await reader.tick(true);await reader.tick();assert.equal(f.store.get(ask.id)?.status,'completed');assert.equal(f.store.get(queued.id)?.status,'queued');
  finish({outcome:'completed',summary:'Changed',question:'',review:'none'});await new Promise(r=>setImmediate(r));finish({outcome:'completed',summary:'Reviewed',question:'',review:'approve'});await operation;
  assert.equal(f.store.get(f.task.id)?.status,'awaiting_checks');assert.equal(f.store.get(queued.id)?.status,'queued');f.store.close();
});

test('stop aborts owned work, preserves sessions and resumes only stop-interrupted tasks',async()=>{
  const f=fixture();const old=f.store.update(f.store.create(input('old')).id,{status:'failed',error:'Needs login'});
  const manager=new Coordinator(config,f.store,f.git,f.github,{execute:async(_p:unknown,o:any)=>new Promise((_r,reject)=>{
    o.onEvent({type:'session',sessionId:'worker-session'});o.signal.addEventListener('abort',()=>reject(new Error('aborted')),{once:true});
  })} as unknown as Providers);
  f.store.update(f.task.id,{provider:'claude'});
  const operation=manager.tick();await new Promise(r=>setImmediate(r));await manager.setMode('stopped');await operation;
  assert.equal(f.store.get(f.task.id)?.status,'interrupted');assert.equal(f.store.get(f.task.id)?.worktree,'isolated');assert.equal(f.store.get(f.task.id)?.workerSessionId,'worker-session');
  await manager.setMode('running');assert.equal(f.store.get(f.task.id)?.status,'queued');assert.equal(f.store.get(old.id)?.status,'failed');f.store.close();
});

test('publication is automatic, acceptance unlocks dependencies independently of delivery, and revisions retain identity',async()=>{
  const f=fixture();f.store.update(f.task.id,{status:'awaiting_checks',pr:1,headSha:'head',baseSha:'base',workerSessionId:'saved',worktree:'preserved'});
  const dependent=f.store.update(f.store.create(input('dependent')).id,{dependencies:[f.task.id],threadId:'dependent'});
  await f.coordinator.tickRemote();const published=f.store.get(f.task.id)!;
  assert.equal(f.calls.merge,1);assert.equal(published.acceptance,'pending');assert.equal(f.store.runnable(dependent),false);
  assert.throws(()=>f.store.accept(published.id,'stale'),/outdated/);
  f.store.accept(published.id,'merge');assert.equal(f.store.get(published.id)?.status,'building');assert.equal(f.store.runnable(dependent),true);
  const count=f.store.pending().length;f.store.accept(published.id,'merge');assert.equal(f.store.pending().length,count);
  await f.coordinator.requestChanges(published.id,'Targets still overlap','merge');const revised=f.store.get(published.id)!;
  assert.equal(revised.id,published.id);assert.equal(revised.worktree,'preserved');assert.equal(revised.workerSessionId,'saved');assert.equal(revised.revision,1);
  assert.equal(revised.reviewSha,undefined);assert.equal(revised.pr,undefined);assert.equal(revised.deliveries?.[0]?.sha,'merge');assert.equal(f.store.runnable(dependent),false);
  await assert.rejects(()=>f.coordinator.requestChanges(published.id,'Another change','merge'),/outdated/);f.store.close();
});

test('an immediate resume during stop unwinding does not lose the interrupted task',async()=>{
  const f=fixture();let rejectWorker!:(e:Error)=>void;
  const manager=new Coordinator(config,f.store,f.git,f.github,{execute:async()=>new Promise((_r,reject)=>{rejectWorker=reject;})} as unknown as Providers);
  const operation=manager.tick();await new Promise(r=>setImmediate(r));await manager.setMode('stopped');await manager.setMode('running');rejectWorker(new Error('aborted'));await operation;
  assert.equal(f.store.get(f.task.id)?.status,'queued');assert.equal(f.store.get(f.task.id)?.worktree,'isolated');f.store.close();
});

test('owner steering during a merge becomes a revision rather than disappearing',async()=>{
  const f=fixture();let merged!:(sha:string)=>void;
  f.store.update(f.task.id,{status:'awaiting_checks',pr:1,headSha:'head',baseSha:'base'});
  (f.github as any).merge=()=>new Promise(r=>{merged=r;});
  const operation=f.coordinator.tickRemote();await new Promise(r=>setImmediate(r));
  await reply(f.store,f.coordinator,f.store.get(f.task.id)!,'Also make the hit area larger','steering','developer');merged('published');await operation;
  assert.equal(f.store.get(f.task.id)?.status,'queued');assert.equal(f.store.get(f.task.id)?.revision,1);assert.equal(f.store.get(f.task.id)?.deliveries?.[0]?.sha,'published');
  assert.match(f.store.context(f.task.id),/hit area larger/);assert.equal(f.calls.dispatch,0);f.store.close();
});

test('approved planning conversations keep later rounds separate and questions collect all answers',async()=>{
  const f=fixture();const proposal=[{title:'One',prompt:'Why: easier hits. Verify model tests.',depends:[]}];
  const plan=f.store.update(f.task.id,{kind:'plan',status:'waiting_input',proposal,proposalAt:10});
  const first=f.store.approvePlan(plan.id,'developer');assert.equal(f.store.approvePlan(plan.id,'developer')[0]?.id,first[0]?.id);
  await reply(f.store,f.coordinator,f.store.get(plan.id)!,'Next, improve colors','round-two','developer');
  f.store.update(plan.id,{status:'waiting_input',proposal,proposalAt:20});const second=f.store.approvePlan(plan.id,'developer');assert.notEqual(second[0]?.id,first[0]?.id);
  assert.equal(first[0]?.threadId,undefined,'children must receive their own conversation');
  const questions=f.store.update(plan.id,{status:'waiting_input',proposal:undefined,questionVersion:30,questions:[{question:'Color?',options:['Blue','Red']},{question:'Size?',options:['Large','Small']}]});
  assert.equal(await answer(f.store,f.coordinator,questions,30,{0:'Blue'},'color','developer'),false);assert.equal(f.store.get(plan.id)?.status,'waiting_input');
  assert.equal(await answer(f.store,f.coordinator,f.store.get(plan.id)!,30,{1:'Large'},'size','developer'),true);assert.equal(f.store.get(plan.id)?.status,'queued');assert.match(f.store.latestUser(plan.id),/Blue[\s\S]*Large/);
  await assert.rejects(()=>answer(f.store,f.coordinator,f.store.get(plan.id)!,30,{1:'Small'},'old','developer'),/replaced/);f.store.close();
});

test('quick-chat commands are only conversation, reuse an active session and reset after quiet time',()=>{
  const f=fixture();const quickConfig={...config,defaultProvider:'codex',quickProvider:'claude',quickModel:'sonnet'} as Config;
  const request={key:'talk',text:'stop and build the app',eventId:'quick1',userId:'developer',game:'tapdemo',channelId:'talk',threadId:'talk'};
  const first=hearQuick(f.store,quickConfig,request);f.store.update(first.id,{status:'implementing',sessionId:'session'});
  const second=hearQuick(f.store,quickConfig,{...request,text:'approve',eventId:'quick2'});assert.equal(second.id,first.id);assert.equal(second.status,'implementing');assert.equal(second.provider,'claude');
  assert.equal(f.store.get(f.task.id)?.status,'queued');assert.equal(f.store.state('manager-mode'),undefined);
  const future=Date.now()+4*3600_000;const original=Date.now;Date.now=()=>future;
  try {assert.notEqual(hearQuick(f.store,quickConfig,{...request,eventId:'quick3'}).id,first.id);}finally{Date.now=original;f.store.close();}
});

test('Claude resumes with latest words, retries a missing session once, and never retries a usage limit',async()=>{
  const adapterConfig={...config,auth:{codex:'subscription',claude:'subscription'},executables:{codex:'codex',claude:'claude'},timeout:1000} as Config;
  let attempts=0;
  const runner:typeof run=async(_exe,args,o)=>{
    if(args[0]==='auth') return '{"loggedIn":true,"authMethod":"claude.ai"}';attempts++;
    if(attempts===1) {assert(args.includes('--resume'));assert.match(o.input!,/latest words/);throw new Error('session could not resume');}
    assert(!args.includes('--resume'));assert.match(o.input!,/saved transcript/);
    o.onLine?.(JSON.stringify({type:'result',structured_output:{outcome:'completed',summary:'Recovered',question:'',review:'none',asks:[]}}));return '';
  };
  const options={cwd:config.root,prompt:'saved transcript',resumePrompt:'latest words',sessionId:'old-session',readonly:true,signal:new AbortController().signal};
  assert.equal((await new Providers(adapterConfig,runner).execute('claude',options)).summary,'Recovered');assert.equal(attempts,2);
  attempts=0;await assert.rejects(()=>new Providers(adapterConfig,async(_exe,args)=>{if(args[0]==='auth') return '{"loggedIn":true,"authMethod":"claude.ai"}';attempts++;throw new AgentPaused('usage limit');}).execute('claude',options),/limit/);assert.equal(attempts,1);
  assert.deepEqual(normalizeEvent('codex',{type:'turn.completed',usage:{input_tokens:12,output_tokens:3,cached_input_tokens:8}})?.usage,{input:12,output:3,cached:8});
  assert.deepEqual(normalizeEvent('claude',{type:'result',usage:{input_tokens:4,output_tokens:3,cache_creation_input_tokens:10,cache_read_input_tokens:100}})?.usage,{input:114,output:3,cached:100});
  assert.throws(()=>parsePlan({outcome:'completed',summary:'Choose',question:'Color?',review:'none',asks:[{question:'Color?',options:['Blue','Red']}],tasks:[{title:'X',prompt:'X',depends:[]}]}),/Unresolved/);
});

test('independent review never inherits a worker session',async()=>{
  const f=fixture();f.store.update(f.task.id,{provider:'claude',workerSessionId:'worker'});const seen:any[]=[];
  const manager=new Coordinator(config,f.store,f.git,f.github,{execute:async(_p:unknown,o:any)=>{seen.push(o.sessionId);o.onEvent({type:'session',sessionId:o.readonly ? 'reviewer' : 'resumed-worker'});return {outcome:'completed',summary:'Done',question:'',review:o.readonly ? 'approve' : 'none'};}} as unknown as Providers);
  await manager.tick();assert.deepEqual(seen,['worker',undefined]);assert.equal(f.store.get(f.task.id)?.workerSessionId,'resumed-worker');f.store.close();
});
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
test('board plans are read-only and dependencies wait for approved successful work',async()=>{
  const f=fixture();f.store.update(f.task.id,{kind:'plan',source:'multica',threadId:undefined});
  let plans=0,changes=0;
  const providers={execute:async(_provider:unknown,options:any)=>{
    if(options.planning) {assert.equal(options.readonly,true);plans++;return {outcome:'completed',summary:'Proposed',question:'',review:'none',tasks:[
      {title:'Model',prompt:'Change scoring and model tests',depends:[]},{title:'Display',prompt:'Update the game display',depends:[0]}]};}
    if(!options.readonly) changes++;
    return {outcome:'completed',summary:'Done',question:'',review:options.readonly ? 'approve' : 'none'};
  }} as unknown as Providers;
  const manager=new Coordinator(config,f.store,f.git,f.github,providers);
  await manager.tick();assert.equal(plans,1);assert.equal(changes,0);assert.equal(f.calls.validate,0);assert.equal(f.store.get(f.task.id)?.status,'waiting_input');
  await manager.tick();assert.equal(plans,1,'awaiting approval must not re-run the planner');
  const tasks=f.store.approvePlan(f.task.id,'developer');
  await manager.tick();assert.equal(changes,1);assert.equal(f.store.get(tasks[1]!.id)?.status,'queued');assert.equal(f.calls.merge,0);
  f.store.update(tasks[0]!.id,{status:'failed'});await manager.tick();assert.equal(changes,1,'failed prerequisite must not unblock its dependent');
  f.store.update(tasks[0]!.id,{status:'ready'});await manager.tick();assert.equal(changes,2);f.store.close();
});

test('read-only conversation can answer during an in-flight worker, without taking another mutation slot',async()=>{
  let finish!:(result:any)=>void;let writes=0,reads=0;
  const f=fixture({agent:undefined});
  const providers={execute:async(_provider:unknown,options:any)=>{
    if(options.readonly) {reads++;return {outcome:'completed',summary:'The round lasts 30 seconds.',question:'',review:'approve'};}
    writes++;return new Promise(resolve=>{finish=resolve;});
  }} as unknown as Providers;
  const manager=new Coordinator(config,f.store,f.git,f.github,providers);
  const working=manager.tick();await new Promise(resolve=>setImmediate(resolve));
  const ask=f.store.create({...input('question','ask'),prompt:'How long is a round?'});f.store.update(ask.id,{source:'multica'});f.store.message(ask.id,'user',ask.prompt);
  await manager.tick(true);assert.equal(f.store.get(ask.id)?.status,'completed');assert.equal(f.store.get(f.task.id)?.status,'implementing');
  await manager.tick();assert.equal(writes,1);assert.equal(reads,1);
  finish({outcome:'completed',summary:'Changed',question:'',review:'none'});await working;assert.equal(writes,1);f.store.close();
});

test('new words arriving during planning invalidate the old proposal and are read next',async()=>{
  const f=fixture();const plan=f.store.update(f.task.id,{kind:'plan'});f.store.message(plan.id,'user','Bigger targets');
  let finish!:(result:any)=>void;let prompt='';
  const providers={execute:async(_provider:unknown,options:any)=>{prompt=options.prompt;return new Promise(resolve=>{finish=resolve;});}} as unknown as Providers;
  const manager=new Coordinator(config,f.store,f.git,f.github,providers);
  const first=manager.tick(true);await new Promise(resolve=>setImmediate(resolve));
  await reply(f.store,manager,f.store.get(plan.id)!,'Actually keep targets small; change the colors','reply','developer');
  finish({outcome:'completed',summary:'Bigger targets',question:'',review:'none',tasks:[{title:'Bigger',prompt:'Increase target size',depends:[]}]});await first;
  assert.equal(f.store.get(plan.id)?.status,'queued');assert.equal(f.store.get(plan.id)?.proposal,undefined);
  assert.throws(()=>f.store.approvePlan(plan.id,'developer'),/No proposed/);
  const next=manager.tick(true);await new Promise(resolve=>setImmediate(resolve));assert.match(prompt,/keep targets small; change the colors/);
  finish({outcome:'completed',summary:'Change colors',question:'',review:'none',tasks:[{title:'Colors',prompt:'Update colors only',depends:[]}]});await next;
  assert.equal(f.store.get(plan.id)?.proposal?.[0]?.title,'Colors');f.store.close();
});

test('stale approval buttons and qualified approval prose cannot start an obsolete plan',async()=>{
  const f=fixture();const proposal=[{title:'Colors',prompt:'Update colors',depends:[]}];
  const plan=f.store.update(f.task.id,{kind:'plan',status:'waiting_input',proposalAt:2,proposal});
  await assert.rejects(()=>reply(f.store,f.coordinator,plan,'approve','stale','developer',1),/replaced/);
  assert.equal(f.store.list().length,1);
  await reply(f.store,f.coordinator,plan,'yes, but keep the current colors','qualified','developer');
  assert.equal(f.store.get(plan.id)?.proposal,undefined);assert.equal(f.store.list().length,1);assert.equal(f.store.get(plan.id)?.status,'queued');
  const updated=f.store.update(plan.id,{status:'waiting_input',proposalAt:3,proposal});
  await reply(f.store,f.coordinator,updated,'go ahead','approved','developer',3);assert.equal(f.store.list().length,2);f.store.close();
});

test('ask follow-ups stay read-only and task-thread selection preserves the planning conversation',async()=>{
  const f=fixture();const plan=f.store.update(f.task.id,{kind:'plan'});
  const child=f.store.create(input('child'));f.store.update(child.id,{threadId:'thread',parentTaskId:plan.id});
  const ask=f.store.create(input('ask','ask'));f.store.update(ask.id,{threadId:'thread',conversationParentId:plan.id,status:'completed'});
  assert.equal(f.store.conversationByThread('thread')?.id,plan.id);
  await reply(f.store,f.coordinator,f.store.get(ask.id)!,'Make the targets blue','follow-up','developer');
  assert.equal(f.store.get(ask.id)?.kind,'ask');assert.equal(f.store.get(ask.id)?.status,'queued');assert.equal(f.store.list().length,3);f.store.close();
});
test('stale base returns to queue without merging',async()=>{
  const f=fixture({main:'new-base'});f.store.update(f.task.id,{status:'awaiting_checks',pr:1,headSha:'head',baseSha:'base'});await f.coordinator.tickRemote();assert.equal(f.store.get(f.task.id)?.status,'queued');assert.equal(f.calls.merge,0);f.store.close();
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
  const f=fixture();(f.github as any).runs=async()=>[{id:7,display_title:`iOS [tapdemo] ${f.task.id}`,html_url:'run'}];f.store.update(f.task.id,{status:'building',mergeSha:'merge',progress:'Waiting for required CI',lastStep:'Read source'});await f.coordinator.tick();
  const task=f.store.get(f.task.id)!;assert.equal(task.status,'processing');assert.match(task.progress!,/Apple/);assert.equal(task.lastStep,undefined);
  const start=task.phaseStartedAt;await f.coordinator.tickRemote();assert.equal(f.store.get(task.id)?.phaseStartedAt,start);
  const status=managerOutlook(f.store);assert.match(status,/No local worker running/);assert.match(status,/Waiting on services/);assert(!status.includes('Waiting for required CI'));f.store.close();
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
        assert(args.includes('workspace-write'));assert(args.includes('--approve-for-me'));assert(args.includes('approval_policy="on-request"'));
        if(process.platform==='win32') assert(args.includes('windows.sandbox="unelevated"'));
        options.onLine?.('{"type":"thread.started","thread_id":"session"}');
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
  f.store.update(f.task.id,{status:'awaiting_checks',pr:1,headSha:'head',baseSha:'base'});await f.coordinator.tickRemote();
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
