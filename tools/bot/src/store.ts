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
  }
  create(input:Pick<Task,'eventId'|'kind'|'game'|'provider'|'model'|'userId'|'channelId'|'prompt'>): Task {
    const existing=this.byEvent(input.eventId); if(existing) return existing;
    const now=Date.now(); const task:Task={...input,id:randomUUID(),status:'queued',createdAt:now,updatedAt:now,attempt:0};
    this.db.prepare('INSERT INTO tasks VALUES(?,?,?,?,?)').run(task.id,task.eventId,task.status,now,JSON.stringify(task)); return task;
  }
  get(id:string):Task | undefined { const row=this.db.prepare('SELECT data FROM tasks WHERE id=?').get(id); return row ? JSON.parse(row.data as string) : undefined; }
  byEvent(id:string):Task | undefined { const row=this.db.prepare('SELECT data FROM tasks WHERE event_id=?').get(id); return row ? JSON.parse(row.data as string) : undefined; }
  byThread(id:string):Task | undefined { const row=this.db.prepare("SELECT data FROM tasks WHERE json_extract(data,'$.threadId')=? ORDER BY created DESC LIMIT 1").get(id); return row ? JSON.parse(row.data as string) : undefined; }
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
  notify(id:string,text:string) { this.db.prepare('INSERT INTO outbox(task_id,content) VALUES(?,?)').run(id,text); }
  pending():{id:number;taskId:string;content:string}[] { return this.db.prepare('SELECT id,task_id,content FROM outbox WHERE sent=0 ORDER BY id').all().map(r=>({id:Number(r.id),taskId:r.task_id as string,content:r.content as string})); }
  sent(id:number) { this.db.prepare('UPDATE outbox SET sent=1 WHERE id=?').run(id); }
  recover() {
    for(const task of this.list()) {
      if(!terminal.has(task.status) && !remote.has(task.status) && !['queued','waiting_input','interrupted'].includes(task.status)) {
        this.update(task.id,{status:'interrupted',error:'Service stopped during local work. Use /status task:<id> resume:true to validate and resume.'});
        this.notify(task.id,'Local work was interrupted. The branch is preserved; use /status with resume:true.');
      }
    }
  }
  close() { this.db.close(); }
}
