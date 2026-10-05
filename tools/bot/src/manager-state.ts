import type { Store } from './store.js';
import type { Task } from './types.js';

export const localWork=new Set(['preparing','implementing','checking','reviewing']);
export const externalWork=new Set(['awaiting_checks','merging','building','uploaded','processing']);
export function taskActivity(task:Task) {
  const remote:Record<string,string>={awaiting_checks:'Waiting for GitHub CI; no coding agent is running.',merging:'GitHub is merging the validated change; no coding agent is running.',
    building:'GitHub is building and signing the app; no coding agent is running.',uploaded:'Build uploaded; waiting for Apple processing and tester setup.',processing:'Waiting for Apple processing, tester assignment and testing readiness; no coding agent is running.',
    ready:'TestFlight readiness verified.',completed:'Finished.',cancelled:'Cancelled.',queued:'Queued; waiting for the coordinator.'};
  return remote[task.status] || (task.status==='waiting_input' ? task.question || task.error : undefined) || task.progress || task.question || task.error || task.summary || task.status.replaceAll('_',' ');
}

export type ManagerMode='running'|'paused'|'stopped';
export function managerMode(store:Store):ManagerMode { return store.state('manager-mode') as ManagerMode || 'running'; }
export function managerAction(text:string):'pause'|'resume'|'stop'|'status'|undefined {
  const words=text.trim().toLowerCase().replace(/[.!?]+$/,'');
  if(/^(?:pause|pause work|pause the manager)$/.test(words)) return 'pause';
  if(/^(?:resume|resume work|resume the manager|continue)$/.test(words)) return 'resume';
  if(/^(?:stop|stop work|stop the manager)$/.test(words)) return 'stop';
  if(/^(?:status|what(?:'s| is) happening(?: now)?|where are we|what is it doing(?: now)?)$/.test(words)) return 'status';
}
export function taskName(task:Task) { return task.prompt.split(/\r?\n/)[0]?.slice(0,110) || task.game; }
export function taskLink(task:Task) { return task.threadId && task.channelId!=='multica' ? `<#${task.threadId}>` : task.boardIssueId ? 'board task' : ''; }
export function managerOutlook(store:Store,now=Date.now()):string {
  const tasks=store.list();
  const working=tasks.filter(t=>localWork.has(t.status));
  const external=tasks.filter(t=>externalWork.has(t.status));
  const needs=tasks.filter(t=>t.status!=='cancelled' && (t.acceptance==='pending' || ['waiting_input','interrupted','failed'].includes(t.status)));
  const queued=tasks.filter(t=>t.status==='queued');
  const line=(t:Task)=>`${taskName(t)} ${taskLink(t)}`.trim();
  const doing=working.map(t=>`${line(t)} — ${t.kind==='plan' ? 'planning' : t.kind==='ask' ? 'answering (read-only)' : t.status.replaceAll('_',' ')} · ${t.provider}${t.model ? ' / '+t.model : ''}${t.runStartedAt ? ' · '+Math.floor((now-t.runStartedAt)/1000)+'s' : ''}\n${taskActivity(t).slice(-300)}`.trim());
  const services=external.map(t=>`${line(t)} — ${taskActivity(t)}\n${t.runUrl || t.prUrl || ''}`.trim());
  const decisions=needs.map(t=>`${line(t)} — ${t.acceptance==='pending' ? 'published; try it and Accept, or Request changes' : t.proposal?.length ? 'review the plan' : t.question || t.error?.split('\n')[0]?.slice(0,350) || 'reply to continue'}`);
  const waiting=queued.map(t=>`${line(t)} — ${!store.runnable(t) ? 'waiting for '+(t.dependencies || []).filter(id=>!store.runnable({...t,dependencies:[id]})).map(id=>store.get(id) ? taskName(store.get(id)!) : id).join(', ') : managerMode(store)==='running' ? 'waiting for a worker' : 'manager '+managerMode(store)}`);
  const usage=tasks.filter(t=>t.usage).map(t=>`${taskName(t)}: ${t.usage!.input} input / ${t.usage!.output} output tokens · ${t.usage!.cached} cached${t.totalRunMs ? ' · '+Math.round(t.totalRunMs/1000)+'s across '+(t.runCount || 1)+' runs' : ''}`);
  return `Manager: ${managerMode(store)}\n\n**Working on**\n${doing.join('\n\n') || 'No local worker running.'}${services.length ? '\n\n**Waiting on services**\n'+services.join('\n\n') : ''}\n\n**Needs you**\n${decisions.join('\n') || 'Nothing waiting on you.'}\n\n**Queued**\n${waiting.join('\n') || 'Nothing queued.'}\n\n**Usage**\n${usage.slice(-8).join('\n') || 'No usage readings yet.'}`;
}
