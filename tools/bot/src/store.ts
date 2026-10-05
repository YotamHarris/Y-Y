import { DatabaseSync } from 'node:sqlite';
import { mkdirSync } from 'node:fs';
import { dirname } from 'node:path';
import { randomUUID } from 'node:crypto';
import { remote, terminal, type Task } from './types.js';

export class Store {
  private db:DatabaseSync;
  constructor(path:string) {
    mkdirSync(dirname(path),{recursive:true}); this.db=new DatabaseSync(path);
    this.db.exec(`PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;
      CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,event_id TEXT NOT NULL UNIQUE,status TEXT NOT NULL,created INTEGER NOT NULL,data TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,event_id TEXT UNIQUE,task_id TEXT NOT NULL,role TEXT NOT NULL,content TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY,task_id TEXT NOT NULL,content TEXT NOT NULL,sent INTEGER NOT NULL DEFAULT 0);`);
    if(!this.db.prepare('PRAGMA table_info(outbox)').all().some(r=>r.name==='board_sent')) this.db.exec('ALTER TABLE outbox ADD COLUMN board_sent INTEGER NOT NULL DEFAULT 0');
    if(!this.db.prepare('PRAGMA table_info(outbox)').all().some(r=>r.name==='discord_attempts')) this.db.exec('ALTER TABLE outbox ADD COLUMN discord_attempts INTEGER NOT NULL DEFAULT 0');
    this.db.exec('CREATE TABLE IF NOT EXISTS board_commands(id TEXT PRIMARY KEY); CREATE TABLE IF NOT EXISTS board_state(key TEXT PRIMARY KEY,value TEXT NOT NULL);');
  }
  create(input:Pick<Task,'eventId'|'kind'|'game'|'provider'|'model'|'userId'|'channelId'|'prompt'>): Task {
    const existing=this.byEvent(input.eventId); if(existing) return existing;
    const now=Date.now(); const task:Task={...input,id:randomUUID(),number:this.list().length+1,status:'queued',createdAt:now,updatedAt:now,attempt:0};
    this.db.prepare('INSERT INTO tasks VALUES(?,?,?,?,?)').run(task.id,task.eventId,task.status,now,JSON.stringify(task)); return task;
  }
  get(id:string):Task | undefined { const row=this.db.prepare('SELECT data FROM tasks WHERE id=?').get(id); return row ? JSON.parse(row.data as string) : undefined; }
  byEvent(id:string):Task | undefined { const row=this.db.prepare('SELECT data FROM tasks WHERE event_id=?').get(id); return row ? JSON.parse(row.data as string) : undefined; }
  byThread(id:string):Task | undefined { const row=this.db.prepare("SELECT data FROM tasks WHERE json_extract(data,'$.threadId')=? ORDER BY created DESC LIMIT 1").get(id); return row ? JSON.parse(row.data as string) : undefined; }
  conversationByThread(id:string):Task|undefined {
    const tasks=this.list().filter(t=>t.threadId===id && !t.conversationParentId);
    return tasks.filter(t=>!t.parentTaskId).at(-1) || tasks.at(-1);
  }
  conversationByBoard(id:string):Task|undefined {
    const tasks=this.list().filter(t=>t.boardIssueId===id && !t.conversationParentId);
    return tasks.filter(t=>!t.parentTaskId).at(-1) || tasks.at(-1);
  }
  list():Task[] { return this.db.prepare('SELECT data FROM tasks ORDER BY created').all().map(r=>JSON.parse(r.data as string)); }
  update(id:string,patch:Partial<Task>):Task {
    const current=this.get(id); if(!current) throw new Error(`Unknown task ${id}`);
    const task={...current,...patch,id:current.id,eventId:current.eventId,updatedAt:Date.now()};
    this.db.prepare('UPDATE tasks SET status=?,data=? WHERE id=?').run(task.status,JSON.stringify(task),id); return task;
  }
  message(taskId:string,role:string,content:string,eventId?:string):boolean {
    return this.db.prepare('INSERT OR IGNORE INTO messages(event_id,task_id,role,content) VALUES(?,?,?,?)').run(eventId || null,taskId,role,content).changes>0;
  }
  context(taskId:string):string {
    return this.db.prepare('SELECT role,content FROM (SELECT * FROM messages WHERE task_id=? ORDER BY id DESC LIMIT 30) ORDER BY id').all(taskId)
      .map(r=>`${r.role}: ${r.content}`).join('\n').slice(-50_000);
  }
  latestUser(taskId:string):string { return this.db.prepare("SELECT content FROM messages WHERE task_id=? AND role='user' ORDER BY id DESC LIMIT 1").get(taskId)?.content as string || this.get(taskId)?.prompt || ''; }
  notify(id:string,text:string) { this.db.prepare('INSERT INTO outbox(task_id,content) VALUES(?,?)').run(id,text); }
  pending():{id:number;taskId:string;content:string;attempts:number}[] { return this.db.prepare('SELECT id,task_id,content,discord_attempts FROM outbox WHERE sent=0 ORDER BY id').all().map(r=>({id:Number(r.id),taskId:r.task_id as string,content:r.content as string,attempts:Number(r.discord_attempts)})); }
  deliveryAttempt(id:number) { this.db.prepare('UPDATE outbox SET discord_attempts=discord_attempts+1 WHERE id=?').run(id); }
  sent(id:number) { this.db.prepare('UPDATE outbox SET sent=1 WHERE id=?').run(id); }
  boardPending() { return this.db.prepare('SELECT id,task_id,content FROM outbox WHERE board_sent=0 ORDER BY id').all().map(r=>({id:Number(r.id),taskId:r.task_id as string,content:r.content as string})); }
  boardSent(id:number) { this.db.prepare('UPDATE outbox SET board_sent=1 WHERE id=?').run(id); }
  command(id:string) { return this.db.prepare('INSERT OR IGNORE INTO board_commands VALUES(?)').run(id).changes>0; }
  state(key:string):string|undefined { return this.db.prepare('SELECT value FROM board_state WHERE key=?').get(key)?.value as string|undefined; }
  setState(key:string,value:string) { this.db.prepare('INSERT INTO board_state VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value').run(key,value); }
  runnable(task:Task) { return (task.dependencies || []).every(id=>{
    const dependency=this.get(id);if(!dependency || dependency.status==='cancelled') return false;
    if(dependency.reviewSha) return dependency.acceptedSha===dependency.reviewSha;
    return ['completed','ready'].includes(dependency.status);
  }); }
  accept(id:string,sha:string) {
    const task=this.get(id);
    if(!task || !task.mergeSha || task.reviewSha!==sha || task.mergeSha!==sha || task.status==='cancelled') throw new Error('This review is outdated or the task has not been published. Use the latest result.');
    if(task.acceptedSha===sha) return;
    this.update(id,{acceptedSha:sha,acceptance:'accepted'});
    this.notify(id,'Accepted. Dependent tasks can now start; TestFlight delivery is tracked separately.');
  }
  approvePlan(id:string,userId:string):Task[] {
    const plan=this.get(id);if(!plan || plan.kind!=='plan' || !plan.proposal?.length) throw new Error('No proposed implementation tasks to approve');
    if(plan.approvedBy) return this.list().filter(t=>t.parentTaskId===id && t.planRound===plan.proposalAt);
    if(plan.status!=='waiting_input') throw new Error('The plan is not waiting for approval');
    this.db.exec('BEGIN IMMEDIATE');
    try {
      const tasks:Task[]=[];
      for(const [i,item] of plan.proposal.entries()) {
        if(item.depends.some(n=>!Number.isInteger(n) || n<0 || n>=i)) throw new Error('Plan dependencies must refer to earlier tasks');
        const task=this.create({eventId:`approved-plan:${id}:${plan.proposalAt}:${i}`,kind:'change',game:plan.game,provider:plan.provider,model:plan.model,
          userId,channelId:plan.channelId,prompt:`${item.title}\n\n${item.prompt}`});
        tasks.push(this.update(task.id,{source:plan.source || 'discord',parentTaskId:id,planRound:plan.proposalAt,goalName:plan.goalName,dependencies:item.depends.map(n=>tasks[n]!.id)}));
        this.message(task.id,'context',this.context(id));this.message(task.id,'user',task.prompt);
        this.notify(task.id,`Approved plan task: ${item.title}`);
      }
      this.update(id,{status:'completed',approvedBy:userId,question:undefined});
      this.notify(id,`Plan approved. ${tasks.length} implementation task(s) queued with their dependencies; no second approval is needed.`);
      this.db.exec('COMMIT');return tasks;
    } catch(err) {this.db.exec('ROLLBACK');throw err;}
  }
  recover() {
    for(const task of this.list()) {
      if(!terminal.has(task.status) && !remote.has(task.status) && !['queued','waiting_input','interrupted'].includes(task.status)) {
        this.update(task.id,{status:'interrupted',error:'Service stopped during local work. Reply “continue” to validate and resume.'});
        this.notify(task.id,'Local work was interrupted. The branch is preserved; reply “continue” to resume.');
      }
    }
  }
  close() { this.db.close(); }
}
