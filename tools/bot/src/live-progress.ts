import type { Task } from './types.js';
import { externalWork, localWork, taskActivity } from './manager-state.js';
import { redact } from './process.js';

export interface ProgressLine {id:string;edit(payload:any):Promise<unknown>;delete():Promise<unknown>}
export interface ProgressTarget {
  archived?:boolean;setArchived?(value:boolean):Promise<unknown>;
  fetch(id:string):Promise<ProgressLine|undefined>;send(payload:any):Promise<ProgressLine>;
}
export async function refreshLiveProgress(tasks:Task[],ports:{
  readId(task:Task):string|undefined;writeId(task:Task,id:string):void;
  target(task:Task):Promise<ProgressTarget|undefined>;onError(task:Task,error:unknown):void;
},now=Date.now()) {
  for(const task of tasks) {
    try {
      const id=ports.readId(task),running=localWork.has(task.status) || externalWork.has(task.status);
      if(!running && !id) continue;
      const channel=await ports.target(task);if(!channel) continue;
      const line=id ? await channel.fetch(id) : undefined;
      if(!running) {
        if(line) {
          const archived=channel.archived;
          if(archived) await channel.setArchived?.(false);
          try {await line.delete();}finally{if(archived) await channel.setArchived?.(true);}
        }
        ports.writeId(task,'');continue;
      }
      if(channel.archived) await channel.setArchived?.(false);
      const external=externalWork.has(task.status);
      const label=external ? 'Waiting on services' : task.kind==='ask' ? 'Answering' : task.kind==='plan' ? 'Planning' : task.status.replaceAll('_',' ');
      const start=external ? task.phaseStartedAt : task.runStartedAt;
      const elapsed=start ? ` · ${Math.max(0,Math.floor((now-start)/1000))}s` : '';
      const identity=external ? '' : ` · ${task.provider}${task.model ? ' / '+task.model : ''}${task.runCount ? ' · run '+task.runCount : ''}`;
      const content=redact(`${label}${identity}${elapsed}\n${taskActivity(task).slice(-750)}\n${external ? task.runUrl || task.prUrl || '' : task.lastStep || ''}`).slice(0,1900);
      const payload={content,allowedMentions:{parse:[]},components:[]};
      if(line) await line.edit(payload);else ports.writeId(task,(await channel.send(payload)).id);
    }catch(error){ports.onError(task,error);}
  }
}
