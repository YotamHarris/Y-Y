import { readFileSync, existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { createHash } from 'node:crypto';
import type { Config } from './config.js';
import { Store } from './store.js';
import type { Coordinator } from './coordinator.js';
import { redact, run, withoutSecrets } from './process.js';
import type { Kind, Task } from './types.js';

export interface BoardConfig {
  executable:string; profile:string; projectId:string; game:string;
  owners:Record<string,string>; managerIssueId?:string;
}
export interface BoardIssue { id:string; identifier:string; title:string; description:string|null; status:string; assignee_type?:string|null; project_id:string|null }
export interface BoardComment { id:string; author_id:string; author_type:string; content:string; created_at:string; deleted_at?:string|null }
export interface BoardAPI {
  issues():Promise<BoardIssue[]>; comments(id:string):Promise<BoardComment[]>;
  create(title:string,description:string):Promise<BoardIssue>;
  update(id:string,status:string):Promise<unknown>; metadata(id:string,key:string,value:string):Promise<unknown>;
  comment(id:string,content:string):Promise<unknown>;
}
// Supported CLI authentication uses its own named profile; service secrets are not
// passed to that child process. No browser cookies or provider tokens are read.
export class MulticaCLI implements BoardAPI {
  constructor(private config:BoardConfig,private root:string,private execute:typeof run=run) {}
  private async call(args:string[],input?:string):Promise<any> {
    const output=await this.execute(this.config.executable,['--profile',this.config.profile,...args,'--output','json'],
      {cwd:this.root,env:withoutSecrets(),input,timeout:30_000,stdoutOnly:true});
    return JSON.parse(output);
  }
  async issues() {
    const issues:BoardIssue[]=[];
    for(let offset=0;;offset+=100) {
      const page=await this.call(['issue','list','--project',this.config.projectId,'--limit','100','--offset',String(offset)]);
      issues.push(...page.issues);if(!page.has_more) return issues;
      if(!page.issues.length) throw new Error('Multica returned an empty continuation page');
    }
  }
  comments(id:string):Promise<BoardComment[]> { return this.call(['issue','comment','list',id,'--full']); }
  create(title:string,description:string):Promise<BoardIssue> { return this.call(['issue','create','--project',this.config.projectId,'--title',title,'--status','backlog','--description-stdin'],description); }
  update(id:string,status:string) { return this.call(['issue','update',id,'--status',status,'--no-start']); }
  metadata(id:string,key:string,value:string) { return this.call(['issue','metadata','set',id,'--key',key,'--value',value,'--type','string']); }
  comment(id:string,content:string) { return this.call(['issue','comment','add',id,'--content-stdin'],content); }
}
export function loadBoardConfig(config:Config):BoardConfig|undefined {
  const path=resolve(config.data,'multica','board.json');if(!existsSync(path)) return;
  const board=JSON.parse(readFileSync(path,'utf8')) as BoardConfig;
  if(!board.executable || !board.profile || !board.projectId || !config.games[board.game] || !Object.keys(board.owners || {}).length
    || Object.values(board.owners).some(id=>!config.users.has(id))) throw new Error('Invalid Multica board configuration or owner mapping');
  return board;
}
export function boardStatus(task:Task):string {
  if(task.status==='ready' || task.status==='completed') return 'done';
  if(task.status==='cancelled') return 'cancelled';
  if(['failed','waiting_input','interrupted'].includes(task.status)) return 'blocked';
  if(task.status==='queued') return 'todo';
  if(['reviewing','awaiting_checks'].includes(task.status)) return 'in_review';
  return 'in_progress';
}
export function waitingOn(task:Task,store:Store):string {
  if(task.status==='queued' && !store.runnable(task)) return 'Dependencies: '+(task.dependencies || []).filter(id=>!['ready','completed'].includes(store.get(id)?.status || '')).join(', ');
  if(task.kind==='plan' && task.status==='waiting_input' && task.proposal?.length) return 'Owner approval of proposed tasks';
  if(task.status==='waiting_input') return task.question || task.error || 'Owner clarification or local provider attention';
  if(task.status==='interrupted') return 'Owner resume after service interruption; preserved worktree';
  if(task.status==='failed') return task.error || 'Failed checks/build; see the workflow and status updates';
  if(task.status==='queued') return 'Coordinator slot';
  if(task.status==='awaiting_checks') return 'GitHub CI at the candidate commit';
  if(['building','uploaded','processing'].includes(task.status)) return 'Hosted build / Apple processing and tester assignment';
  return '';
}
function digest(value:unknown) { return createHash('sha256').update(JSON.stringify(value)).digest('hex'); }

export class BoardBridge {
  private busy=false;
  private stopping=false;
  constructor(private config:Config,private board:BoardConfig,private store:Store,private coordinator:Coordinator,private api:BoardAPI) {}
  async tick() {
    if(this.busy || this.stopping) return;this.busy=true;
    try {
      const issues=await this.api.issues();
      for(const task of this.store.list().filter(t=>t.game===this.board.game)) {
        // Recover a create whose response was lost, using an immutable task marker.
        const marker=`YYEngine task: ${task.id}`;
        let issue=task.boardIssueId ? issues.find(i=>i.id===task.boardIssueId) : issues.find(i=>i.description?.includes(marker));
        if(task.boardIssueId && !issue) continue; // An owner may remove/archive a card; do not recreate it.
        if(!issue) {
          issue=await this.api.create(`${task.kind}: ${redact(task.prompt.split(/\r?\n/)[0] || '').slice(0,100)}`,
            `${redact(task.prompt).slice(0,20000)}\n\n${marker}\nProvider: ${task.provider}${task.model ? ' / '+task.model : ''}\n${task.threadId ? `Discord: https://discord.com/channels/${this.config.guildId}/${task.threadId}\n` : ''}Updates appear in comments. Owner commands: /yy resume, /yy cancel, /yy approve (plans).`);
          issues.push(issue);
        }
        if(task.boardIssueId!==issue.id) this.store.update(task.id,{boardIssueId:issue.id});
      }
      // Only explicit owner commands can start work. Card creation, status changes,
      // provider prose and comments by invited observers never authorize execution.
      for(const issue of issues) {
        const comments=await this.api.comments(issue.id);
        for(const comment of [...comments].sort((a,b)=>a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id))) {
          if(comment.deleted_at || comment.author_type!=='member' || !this.board.owners[comment.author_id] || !/^\/yy\s/i.test(comment.content.trim())) continue;
          if(!this.store.command(comment.id)) continue;
          try { await this.command(issue,comment,issues); }
          catch(err) { await this.api.comment(issue.id,`YYEngine manager: ${redact(err instanceof Error ? err.message : String(err))}\nCorrect the request and post a new command.`); }
        }
      }
      for(const task of this.store.list().filter(t=>t.game===this.board.game && t.boardIssueId)) {
        const status=boardStatus(task),waiting=redact(waitingOn(task,this.store)).slice(0,1500);
        const snapshot={status,waiting,progress:task.progress,summary:task.summary,question:task.question,pr:task.prUrl,build:task.runUrl};
        const key=`snapshot:${task.id}`,hash=digest(snapshot);
        if(this.store.state(key)===hash) continue;
        const issue=issues.find(i=>i.id===task.boardIssueId);if(!issue) continue;
        // A card assigned to a native Multica agent could start a second executor.
        // Do not change its lifecycle; ask the owner to restore coordinator ownership.
        if(issue.assignee_type==='agent' || issue.assignee_type==='squad') {
          throw new Error(`${issue.identifier} is assigned to a Multica agent. Remove that assignment before using YYEngine commands.`);
        }
        if(issue.status!==status) {await this.api.update(issue.id,status);issue.status=status;}
        for(const [name,value] of Object.entries({yy_task_id:task.id,pipeline_status:task.status,waiting_on:waiting,provider:task.provider,
          latest_progress:redact(task.progress || '').slice(-2000),question:redact(task.question || '').slice(-1500),pr_url:task.prUrl || '',build_url:task.runUrl || ''})) {
          const metadataKey=`meta:${task.id}:${name}`;if(this.store.state(metadataKey)===value) continue;
          await this.api.metadata(issue.id,name,value);this.store.setState(metadataKey,value);
        }
        this.store.setState(key,hash);
      }
      for(const item of this.store.boardPending()) {
        const task=this.store.get(item.taskId);if(task?.game!==this.board.game || !task.boardIssueId || !issues.some(i=>i.id===task.boardIssueId)) continue;
        const receipt=`YY update ${task.id}/${item.id}`;
        const comments=await this.api.comments(task.boardIssueId);
        if(!comments.some(c=>c.content.includes(receipt))) await this.api.comment(task.boardIssueId,`${redact(item.content)}\n\n${receipt}`);
        this.store.boardSent(item.id);
      }
      if(this.board.managerIssueId && Date.now()-Number(this.store.state('heartbeat') || 0)>60_000) {
        await this.api.metadata(this.board.managerIssueId,'last_seen',new Date().toISOString());
        await this.api.metadata(this.board.managerIssueId,'coordinator','Connected: Discord + Multica, one serial work queue');
        this.store.setState('heartbeat',String(Date.now()));
      }
    } finally {this.busy=false;}
  }
  async shutdown() { this.stopping=true;while(this.busy) await new Promise(resolve=>setTimeout(resolve,25)); }
  private async command(issue:BoardIssue,comment:BoardComment,issues:BoardIssue[]) {
    if(issue.assignee_type==='agent' || issue.assignee_type==='squad') throw new Error('Remove the native agent assignment first; this card is managed by the YYEngine coordinator');
    const match=/^\/yy\s+(ask|plan|change|build|approve|resume|cancel)\b\s*([\s\S]*)$/i.exec(comment.content.trim());
    if(!match) throw new Error('Commands: /yy ask, /yy plan, /yy change, /yy build, /yy approve, /yy resume, /yy cancel');
    const action=match[1]!.toLowerCase();let body=match[2]!.trim();
    const userId=this.board.owners[comment.author_id]!;
    const current=this.store.list().filter(t=>t.boardIssueId===issue.id).at(-1);
    if(['approve','resume','cancel'].includes(action)) {
      if(!current) throw new Error('This card has no coordinator task');
      if(action==='approve') this.store.approvePlan(current.id,userId);
      else if(action==='cancel') await this.coordinator.cancel(current.id);
      else {
        if(body) this.store.message(current.id,'user',body,comment.id);
        if(current.kind==='plan' && current.approvedBy) throw new Error('This plan is already approved; create another card for a new plan');
        if(current.kind==='plan' && !['waiting_input','interrupted','failed'].includes(current.status)) throw new Error('The planner is still working');
        if(current.kind==='plan') {this.store.update(current.id,{proposal:undefined,question:undefined});await this.coordinator.resume(current.id);}
        else if(current.kind==='ask' && current.status==='completed') this.store.update(current.id,{status:'queued',question:undefined});
        else await this.coordinator.resume(current.id);
      }
      return;
    }
    if(current) throw new Error('This card already has a task. Use /yy resume or create a new card');
    const providerMatch=/^provider=(codex|claude)(?:\s+|$)/i.exec(body);
    const provider=providerMatch ? providerMatch[1]!.toLowerCase() as Task['provider'] : this.config.defaultProvider;
    if(providerMatch) body=body.slice(providerMatch[0].length).trim();
    if(/^provider=/i.test(body)) throw new Error('provider must be codex or claude');
    const dependsMatch=/^depends=([^\s]+)(?:\s+|$)/i.exec(body);const dependencies:string[]=[];
    if(dependsMatch) {
      for(const ref of dependsMatch[1]!.split(',')) {
        const dependencyIssue=issues.find(i=>i.identifier.toLowerCase()===ref.toLowerCase());
        const dependency=this.store.list().find(t=>t.boardIssueId===dependencyIssue?.id);
        if(!dependency || dependencyIssue?.id===issue.id) throw new Error(`Unknown coordinator dependency: ${ref}`);
        dependencies.push(dependency.id);
      }
      body=body.slice(dependsMatch[0].length).trim();
    }
    const prompt=body || (action==='build' ? 'Build latest main' : `${issue.title}\n\n${issue.description || ''}`);
    const task=this.store.create({eventId:`multica:${comment.id}`,kind:action as Kind,game:this.board.game,provider,userId,channelId:'multica',prompt});
    this.store.update(task.id,{source:'multica',boardIssueId:issue.id,dependencies});
    this.store.message(task.id,'user',prompt,comment.id);
    this.store.notify(task.id,`YYEngine accepted ${action} using ${provider}. ${action==='ask' || action==='plan' ? 'Read-only; a plan requires /yy approve before implementation.' : 'Uses the existing checks, independent review, CI and distribution pipeline.'}`);
  }
}
