import { readFileSync, existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { createHash } from 'node:crypto';
import type { Config } from './config.js';
import { Store } from './store.js';
import type { Coordinator } from './coordinator.js';
import { redact, run, withoutSecrets } from './process.js';
import type { Kind, Task } from './types.js';
import { buildRequest, control, hearQuick, reply, statusText } from './conversation.js';
import { managerAction, managerOutlook, taskActivity, externalWork } from './manager-state.js';

export interface BoardConfig {
  executable:string; profile:string; projectId:string; game:string;
  owners:Record<string,string>; managerIssueId?:string; talkIssueId?:string;
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
export function boardStatus(task:Task,store?:Store):string {
  if(task.status==='cancelled') return 'cancelled';
  if(task.acceptance==='pending') return 'in_review';
  if(task.kind==='plan' && task.approvedBy && store) {
    const pending=store.list().filter(t=>t.parentTaskId===task.id && t.status!=='cancelled' && (t.reviewSha ? t.acceptedSha!==t.reviewSha : !['completed','ready'].includes(t.status)));
    if(pending.length) return pending.every(t=>!!t.reviewSha) ? 'in_review' : 'in_progress';
  }
  if(task.status==='ready' || task.status==='completed') return 'done';
  if(['failed','waiting_input','interrupted'].includes(task.status)) return 'blocked';
  if(task.status==='queued') return 'todo';
  if(['reviewing','awaiting_checks'].includes(task.status)) return 'in_review';
  return 'in_progress';
}
export function waitingOn(task:Task,store:Store):string {
  if(task.kind==='plan' && task.approvedBy) {
    const reviews=store.list().filter(t=>t.parentTaskId===task.id && t.status!=='cancelled' && t.reviewSha && t.acceptedSha!==t.reviewSha);
    if(reviews.length) return 'Owner acceptance of published tasks: '+reviews.map(t=>t.prompt.split('\n')[0]).join(', ');
  }
  if(task.status==='queued' && !store.runnable(task)) return 'Waiting for accepted dependencies: '+(task.dependencies || []).filter(id=>!store.runnable({...task,dependencies:[id]})).join(', ');
  if(task.kind==='plan' && task.status==='waiting_input' && task.proposal?.length) return 'Owner approval of proposed tasks';
  if(task.status==='waiting_input') return task.question || task.error || 'Owner clarification or local provider attention';
  if(task.status==='interrupted') return 'Owner resume after service interruption; preserved worktree';
  if(task.status==='failed') return task.error || 'Failed checks/build; see the workflow and status updates';
  if(task.status==='queued') return 'Coordinator slot';
  if(externalWork.has(task.status)) return taskActivity(task);
  return '';
}
function digest(value:unknown) { return createHash('sha256').update(JSON.stringify(value)).digest('hex'); }

export class BoardBridge {
  private busy=false;
  private stopping=false;
  constructor(private config:Config,private board:BoardConfig,private store:Store,private coordinator:Coordinator,private api:BoardAPI) {
    // Enabling conversation must never reinterpret the existing comment history.
    const key=`conversation-enabled:${board.projectId}`;
    if(!store.state(key)) store.setState(key,new Date().toISOString());
  }
  async tick() {
    if(this.busy || this.stopping) return;this.busy=true;
    try {
      const issues=await this.api.issues();
      this.store.setState(`manager-board-context:${this.board.game}`,redact(JSON.stringify(issues.map(i=>({
        card:i.identifier,title:i.title,status:i.status,description:i.description?.slice(0,2000)
      })))).slice(-20000));
      if(this.board.talkIssueId) for(const task of this.store.list().filter(t=>t.quickKey && t.game===this.board.game && !t.boardIssueId)) this.store.update(task.id,{boardIssueId:this.board.talkIssueId});
      for(const task of this.store.list().filter(t=>t.game===this.board.game)) {
        // Recover a create whose response was lost, using an immutable task marker.
        const marker=`YYEngine task: ${task.id}`;
        let issue=task.boardIssueId ? issues.find(i=>i.id===task.boardIssueId) : issues.find(i=>i.description?.includes(marker));
        if(task.boardIssueId && !issue) continue; // An owner may remove/archive a card; do not recreate it.
        if(!issue) {
          issue=await this.api.create(`${task.kind}: ${redact(task.prompt.split(/\r?\n/)[0] || '').slice(0,100)}`,
            `${redact(task.prompt).slice(0,20000)}\n\n${marker}\nProvider: ${task.provider}${task.model ? ' / '+task.model : ''}\n${task.threadId ? `Discord: https://discord.com/channels/${this.config.guildId}/${task.threadId}\n` : ''}Talk in comments. Reply approve to start a proposed plan, continue to retry paused work, or cancel to stop.`);
          issues.push(issue);
        }
        if(task.boardIssueId!==issue.id) this.store.update(task.id,{boardIssueId:issue.id});
        if(task.originBoardIssueId && !this.store.state(`origin-notified:${task.id}`)) {
          const receipt=`YY conversation ${task.id}`;
          const comments=await this.api.comments(task.originBoardIssueId);
          if(!comments.some(c=>c.content.includes(receipt))) await this.api.comment(task.originBoardIssueId,`YYEngine manager: I opened ${issue.identifier} for this ${task.kind==='build' ? 'build. Upload, processing and testing readiness will be reported there.' : 'goal. Refine the plan in its comments, then reply approve.'}\n\n${receipt}`);
          this.store.setState(`origin-notified:${task.id}`,'yes');
        }
      }
      // Fresh owner prose starts read-only conversation. Card creation, status
      // changes, generated replies and observer comments cannot authorize work.
      for(const issue of issues) {
        const comments=await this.api.comments(issue.id);
        for(const comment of [...comments].sort((a,b)=>a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id))) {
          if(comment.deleted_at || comment.author_type!=='member' || !this.board.owners[comment.author_id]) continue;
          const explicit=/^\/yy\s/i.test(comment.content.trim());
          if(!explicit && (comment.created_at<this.store.state(`conversation-enabled:${this.board.projectId}`)! ||
            /^YYEngine manager:|\bYY update [\w-]+\/\d+|\bYY conversation [\w-]+/.test(comment.content))) continue;
          if(!this.store.command(comment.id)) continue;
          try { if(explicit) await this.command(issue,comment,issues);else await this.conversation(issue,comment,issues); }
          catch(err) { await this.api.comment(issue.id,`YYEngine manager: ${redact(err instanceof Error ? err.message : String(err))}\nReply here with your correction.`); }
        }
      }
      for(const task of this.store.list().filter(t=>t.game===this.board.game && t.boardIssueId)) {
        if(task.kind==='ask' || task.conversationParentId || task.boardIssueId===this.board.managerIssueId) continue;
        const status=boardStatus(task,this.store),waiting=task.acceptance==='pending' ? 'Owner acceptance of the published commit; delivery tracked separately' : redact(waitingOn(task,this.store)).slice(0,1500);
        const progress=task.kind==='plan' && task.approvedBy && status==='in_review' ? waiting : taskActivity(task);
        const snapshot={status,waiting,progress,summary:task.summary,question:task.question,pr:task.prUrl,build:task.runUrl,review:task.reviewSha,accepted:task.acceptedSha};
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
          latest_progress:redact(progress).slice(-2000),question:redact(task.question || '').slice(-1500),pr_url:task.prUrl || '',build_url:task.runUrl || '',
          owner_review:task.acceptance || '',review_commit:task.reviewSha || '',accepted_commit:task.acceptedSha || ''})) {
          const metadataKey=`meta:${task.id}:${name}`;if(this.store.state(metadataKey)===value) continue;
          await this.api.metadata(issue.id,name,value);this.store.setState(metadataKey,value);
        }
        this.store.setState(key,hash);
      }
      for(const item of this.store.boardPending()) {
        const task=this.store.get(item.taskId);if(task?.game!==this.board.game || !task.boardIssueId || !issues.some(i=>i.id===task.boardIssueId)) continue;
        const receipt=`YY update ${task.id}/${item.id}`;
        const comments=await this.api.comments(task.boardIssueId);
        if(!comments.some(c=>c.content.includes(receipt))) await this.api.comment(task.boardIssueId,`YYEngine manager: ${redact(item.content)}\n\n${receipt}`);
        this.store.boardSent(item.id);
      }
      if(this.board.managerIssueId && Date.now()-Number(this.store.state('heartbeat') || 0)>60_000) {
        await this.api.metadata(this.board.managerIssueId,'last_seen',new Date().toISOString());
        await this.api.metadata(this.board.managerIssueId,'coordinator',`Connected: Discord + Multica, ${this.config.workerSlots || 2} isolated workers, serial publication`);
        await this.api.metadata(this.board.managerIssueId,'manager_mode',this.store.state('manager-mode') || 'running');
        this.store.setState('heartbeat',String(Date.now()));
      }
    } finally {this.busy=false;}
  }
  async shutdown() { this.stopping=true;while(this.busy) await new Promise(resolve=>setTimeout(resolve,25)); }
  private async conversation(issue:BoardIssue,comment:BoardComment,issues:BoardIssue[]) {
    if(issue.assignee_type==='agent' || issue.assignee_type==='squad') throw new Error('Remove the native agent assignment first; this card is managed by the YYEngine coordinator');
    const text=comment.content.trim();if(!text) return;
    const userId=this.board.owners[comment.author_id]!;
    const hub=issue.id===this.board.managerIssueId;
    const talk=issue.id===this.board.talkIssueId;
    if(talk) {hearQuick(this.store,this.config,{key:`board:${issue.id}`,text,eventId:comment.id,userId,game:this.board.game,channelId:'multica',boardIssueId:issue.id});return;}
    const current=hub ? undefined : this.store.conversationByBoard(issue.id);
    const global=hub ? managerAction(text) : undefined;
    if(global) {
      if(global!=='status') await this.coordinator.setMode(global==='pause' ? 'paused' : global==='stop' ? 'stopped' : 'running');
      await this.api.comment(issue.id,'YYEngine manager: '+managerOutlook(this.store));return;
    }
    const action=control(text);
    if(action==='status' && !current) {
      await this.api.comment(issue.id,'YYEngine manager: Current board:\n'+issues.filter(i=>i.id!==this.board.managerIssueId).map(i=>`${i.identifier} · ${i.title} · ${i.status}\n${redact(i.description || '').slice(0,1500)}`).join('\n\n')+'\n\nCoordinator:\n'+(this.store.list().filter(t=>t.game===this.board.game && !t.conversationParentId).slice(-10).map(t=>statusText(t,this.store)).join('\n\n') || 'No coordinator tasks yet.'));return;
    }
    if(action && !current) throw new Error('Open the relevant task card to approve, continue or cancel it. You can describe a new goal here.');
    if(current && !buildRequest(text)) {
      await reply(this.store,this.coordinator,current,text,comment.id,userId);return;
    }
    const kind=buildRequest(text) ? 'build' : 'plan';
    const task=this.store.create({eventId:`multica:${comment.id}`,kind,game:this.board.game,provider:current?.provider || this.config.defaultProvider,
      userId,channelId:'multica',prompt:text});
    this.store.update(task.id,{source:'multica',boardIssueId:hub || current ? undefined : issue.id,
      originBoardIssueId:hub || current ? issue.id : undefined,
      conversationParentId:undefined});
    if(current) this.store.message(task.id,'context',this.store.context(current.id));
    this.store.message(task.id,'context',`Current TapDemo board observations:\n${redact(JSON.stringify(issues.map(i=>({card:i.identifier,title:i.title,status:i.status,description:i.description?.slice(0,2000)})))).slice(-20000)}`);
    if(!hub) this.store.message(task.id,'context',`${issue.title}\n${issue.description || ''}`);
    this.store.message(task.id,'user',text,comment.id);
    this.store.notify(task.id,kind==='build' ? 'Queued a TestFlight build of current main. I’ll report upload, processing and testing readiness separately.' : 'Let’s plan this here. I’ll read the code, discuss the choices, and propose complete tasks; approval starts them. This conversation stays open for follow-up goals.');
  }
  private async command(issue:BoardIssue,comment:BoardComment,issues:BoardIssue[]) {
    if(issue.assignee_type==='agent' || issue.assignee_type==='squad') throw new Error('Remove the native agent assignment first; this card is managed by the YYEngine coordinator');
    const match=/^\/yy\s+(ask|plan|change|build|approve|resume|cancel)\b\s*([\s\S]*)$/i.exec(comment.content.trim());
    if(!match) throw new Error('Commands: /yy ask, /yy plan, /yy change, /yy build, /yy approve, /yy resume, /yy cancel');
    const action=match[1]!.toLowerCase();let body=match[2]!.trim();
    const userId=this.board.owners[comment.author_id]!;
    const current=this.store.conversationByBoard(issue.id);
    if(['approve','resume','cancel'].includes(action)) {
      if(!current) throw new Error('This card has no coordinator task');
      if(action==='approve') this.store.approvePlan(current.id,userId);
      else if(action==='cancel') await this.coordinator.cancel(current.id);
      else {
        await reply(this.store,this.coordinator,current,body || 'continue',comment.id,userId);
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
    this.store.notify(task.id,`YYEngine accepted ${action} using ${provider}. ${action==='ask' || action==='plan' ? 'Read-only; reply approve to a proposed plan to start implementation.' : 'Uses the existing checks, independent review, CI and distribution pipeline.'}`);
  }
}
