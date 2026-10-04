import test from 'node:test';
import assert from 'node:assert/strict';
import { Store } from '../src/store.js';
import { BoardBridge, MulticaCLI, waitingOn, type BoardAPI, type BoardIssue, type BoardComment, type BoardConfig } from '../src/multica.js';
import type { Config } from '../src/config.js';
import type { Coordinator } from '../src/coordinator.js';
import { parsePlan } from '../src/providers.js';
import { run } from '../src/process.js';
import { projectContext } from '../src/manager-context.js';
import { mkdtempSync, mkdirSync, writeFileSync, symlinkSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';

const config={root:process.cwd(),guildId:'guild',defaultProvider:'codex',games:{tapdemo:{directory:'games/tapdemo'}}} as unknown as Config;
const board:BoardConfig={executable:'multica.exe',profile:'yyengine',projectId:'project',game:'tapdemo',owners:{owner:'developer'}};
const input=(eventId:string,kind:'change'|'plan'='change')=>({eventId,kind,game:'tapdemo',provider:'codex' as const,userId:'developer',channelId:'multica',prompt:'Scoring change'});
function fixture() {
  const store=new Store(':memory:');
  const issues:BoardIssue[]=[{id:'card',identifier:'YYEN-1',title:'Game request',description:'Why and what',status:'backlog',project_id:'project'}];
  const comments:Record<string,BoardComment[]>={card:[]};let creates=0,posts=0;
  const api:BoardAPI={issues:async()=>issues,comments:async id=>comments[id] || [],
    create:async(title,description)=>{creates++;const issue={id:`created-${creates}`,identifier:`YYEN-${creates+1}`,title,description,status:'backlog',project_id:'project'};issues.push(issue);comments[issue.id]=[];return issue;},
    update:async(id,status)=>{issues.find(i=>i.id===id)!.status=status;},metadata:async()=>{},
    comment:async(id,content)=>{posts++;(comments[id] ||= []).push({id:`post-${posts}`,author_id:'owner',author_type:'member',created_at:new Date().toISOString(),content});}};
  const coordinator={resume:async(id:string)=>store.update(id,{status:'queued'}),cancel:async(id:string)=>store.update(id,{status:'cancelled'})} as unknown as Coordinator;
  const bridge=new BoardBridge(config,board,store,coordinator,api);
  const command=(id:string,content:string,author='owner')=>comments.card!.push({id,content,author_id:author,author_type:'member',created_at:new Date().toISOString()});
  return {store,api,issues,comments,bridge,command,creates:()=>creates,posts:()=>posts};
}
test('only explicit mapped-owner commands enqueue board work, with durable deduplication',async()=>{
  const f=fixture();f.command('observer','/yy change Change scoring','outsider');f.command('chat','Please change it');
  await f.bridge.tick();assert.equal(f.store.list().length,0);
  f.command('owner-request','/yy change provider=claude Add two points');await f.bridge.tick();await f.bridge.tick();
  const tasks=f.store.list();assert.equal(tasks.length,1);assert.equal(tasks[0]?.provider,'claude');assert.equal(tasks[0]?.source,'multica');assert.equal(tasks[0]?.boardIssueId,'card');
  assert.equal(f.creates(),0);assert.equal(f.store.boardPending().length,0);assert.equal(f.posts(),1);f.store.close();
});
test('approved plans atomically become whole tasks once, with dependencies and preserved provider',()=>{
  const store=new Store(':memory:');const plan=store.create(input('plan','plan'));
  store.update(plan.id,{source:'multica',status:'waiting_input',proposalAt:1,proposal:[
    {title:'Award points',prompt:'Why: scoring. Approach: deterministic model. What to do: update tests and scoring.',depends:[]},
    {title:'Display score',prompt:'Update score display after the model change.',depends:[0]}]});
  const tasks=store.approvePlan(plan.id,'developer');assert.equal(tasks.length,2);assert.equal(store.approvePlan(plan.id,'developer').length,2);assert.equal(store.list().length,3);
  assert.equal(store.runnable(tasks[0]!),true);assert.equal(store.runnable(tasks[1]!),false);assert.match(waitingOn(tasks[1]!,store),/Dependencies/);
  store.update(tasks[0]!.id,{status:'failed'});assert.equal(store.runnable(tasks[1]!),false);
  store.update(tasks[0]!.id,{status:'ready'});assert.equal(store.runnable(tasks[1]!),true);store.close();
});
test('invalid dependency rolls back the entire approval instead of queuing a partial plan',()=>{
  const store=new Store(':memory:');const plan=store.create(input('plan','plan'));
  store.update(plan.id,{status:'waiting_input',proposal:[{title:'First',prompt:'Do it',depends:[]},{title:'Broken',prompt:'Do it',depends:[9]}]});
  assert.throws(()=>store.approvePlan(plan.id,'developer'),/dependencies/);assert.equal(store.list().length,1);assert.equal(store.get(plan.id)?.approvedBy,undefined);store.close();
});
test('lost create and comment responses recover without duplicate cards or notifications',async()=>{
  const f=fixture();const task=f.store.create(input('discord'));
  f.issues.push({id:'recovered',identifier:'YYEN-5',title:'Existing export',description:`YYEngine task: ${task.id}`,status:'backlog',project_id:'project'});
  f.store.notify(task.id,'Preparing');let once=true;const post=f.api.comment;
  f.api.comment=async(id,content)=>{await post(id,content);if(once){once=false;throw new Error('Response lost');}};
  await assert.rejects(()=>f.bridge.tick(),/lost/);await f.bridge.tick();
  assert.equal(f.creates(),0);assert.equal(f.posts(),1);assert.equal(f.store.get(task.id)?.boardIssueId,'recovered');assert.equal(f.store.boardPending().length,0);f.store.close();
});
test('native agent assignment cannot start a competing coordinator task',async()=>{
  const f=fixture();f.issues[0]!.assignee_type='agent';f.command('request','/yy change Do it');
  await f.bridge.tick();assert.equal(f.store.list().length,0);assert.match(f.comments.card![1]!.content,/assignment/);f.store.close();
});
test('read-only plan contract rejects forward dependencies and missing task bodies',()=>{
  const base={outcome:'completed',summary:'Proposed',question:'',review:'none'};
  assert.throws(()=>parsePlan({...base,tasks:[{title:'Bad',prompt:'Change',depends:[0]}]}),/dependency/);
  assert.throws(()=>parsePlan({...base,tasks:[{title:'Bad',prompt:'',depends:[]}]}),/task/);
  assert.throws(()=>parsePlan({...base,outcome:'needs_input',question:'Which target?',tasks:[{title:'Unsettled',prompt:'Change',depends:[]}]}),/Unresolved/);
  assert.equal(parsePlan({...base,tasks:[]}).tasks.length,0);
});
test('Multica adapter paginates and uses native argv/stdin without service credentials',async()=>{
  let pages=0;
  const api=new MulticaCLI(board,process.cwd(),async(executable,args,options)=>{
    assert.equal(executable,'multica.exe');assert.equal(options.env?.GITHUB_TOKEN,undefined);assert.equal(options.env?.DISCORD_TOKEN,undefined);
    if(args.includes('list')) {pages++;return JSON.stringify({issues:[{id:`card-${pages}`}],has_more:pages===1});}
    assert.equal(options.input,'A & B\nKeep $literal text');return '{}';
  });
  assert.equal((await api.issues()).length,2);await api.comment('card','A & B\nKeep $literal text');assert.equal(pages,2);
});
test('JSON command stdout is separated from native CLI notices on stderr',async()=>{
  const output=await run(process.execPath,['-e','process.stderr.write("Comment added\\n");process.stdout.write(JSON.stringify({ok:true}));'],{cwd:process.cwd(),stdoutOnly:true});
  assert.deepEqual(JSON.parse(output),{ok:true});
});
test('manager snapshot includes game evidence, excluding credentials, runtime state and escaping junctions',()=>{
  const root=mkdtempSync(resolve(tmpdir(),'yy-context-'));const outside=mkdtempSync(resolve(tmpdir(),'yy-outside-'));
  for(const name of ['docs','games/tapdemo','engine','tests','.yy']) mkdirSync(resolve(root,name),{recursive:true});
  writeFileSync(resolve(root,'README.md'),'Implemented game');writeFileSync(resolve(root,'docs/validation.md'),'Checks passed');
  writeFileSync(resolve(root,'games/tapdemo/model.cpp'),'Deterministic gameplay');writeFileSync(resolve(root,'.env'),'PRIVATE_ENV');
  writeFileSync(resolve(root,'.yy/private.cpp'),'PRIVATE_RUNTIME');writeFileSync(resolve(outside,'secret.cpp'),'PRIVATE_OUTSIDE');
  symlinkSync(outside,resolve(root,'engine/escape'),process.platform==='win32' ? 'junction' : 'dir');
  const text=projectContext(root,'games/tapdemo');assert.match(text,/Checks passed/);assert.match(text,/Deterministic gameplay/);assert(!text.includes('PRIVATE_'));
  assert.throws(()=>projectContext(root,'../.yy'),/Invalid/);
});
