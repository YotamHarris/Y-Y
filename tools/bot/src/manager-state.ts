import type { Store } from './store.js';
import type { Task } from './types.js';

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
  const working=tasks.filter(t=>['preparing','implementing','checking','reviewing','awaiting_checks','merging','building','uploaded','processing'].includes(t.status));
  const needs=tasks.filter(t=>t.status!=='cancelled' && (t.acceptance==='pending' || ['waiting_input','interrupted','failed'].includes(t.status)));
  const queued=tasks.filter(t=>t.status==='queued');
  const line=(t:Task)=>`${taskName(t)} ${taskLink(t)}`.trim();
  const doing=working.map(t=>`${line(t)} — ${t.kind==='plan' ? 'planning' : t.kind==='ask' ? 'answering (read-only)' : t.status.replaceAll('_',' ')} · ${t.provider}${t.model ? ' / '+t.model : ''}${t.runStartedAt ? ' · '+Math.floor((now-t.runStartedAt)/1000)+'s' : ''}\n${t.progress?.slice(-300) || t.lastStep || t.runUrl || ''}`.trim());
  const decisions=needs.map(t=>`${line(t)} — ${t.acceptance==='pending' ? 'published; try it and Accept, or Request changes' : t.proposal?.length ? 'review the plan' : t.question || t.error?.split('\n')[0]?.slice(0,350) || 'reply to continue'}`);
  const waiting=queued.map(t=>`${line(t)} — ${!store.runnable(t) ? 'waiting for '+(t.dependencies || []).filter(id=>!store.runnable({...t,dependencies:[id]})).map(id=>store.get(id) ? taskName(store.get(id)!) : id).join(', ') : managerMode(store)==='running' ? 'waiting for a worker' : 'manager '+managerMode(store)}`);
  const usage=tasks.filter(t=>t.usage).map(t=>`${taskName(t)}: ${t.usage!.input} input / ${t.usage!.output} output tokens · ${t.usage!.cached} cached${t.totalRunMs ? ' · '+Math.round(t.totalRunMs/1000)+'s across '+(t.runCount || 1)+' runs' : ''}`);
  return `Manager: ${managerMode(store)}\n\n**Working on**\n${doing.join('\n\n') || 'Nothing running.'}\n\n**Needs you**\n${decisions.join('\n') || 'Nothing waiting on you.'}\n\n**Queued**\n${waiting.join('\n') || 'Nothing queued.'}\n\n**Usage**\n${usage.slice(-8).join('\n') || 'No usage readings yet.'}`;
}
