import { Store } from './store.js';
import type { Coordinator } from './coordinator.js';
import type { Task } from './types.js';
import type { Config } from './config.js';

export const managerGuide=`Describe a goal here in your own words. I open a planning card, read the code and discuss material choices before proposing a few whole tasks with reasons and acceptance evidence. Refine the plan by replying. Approve the latest proposal once to start its tasks. This same planning conversation stays open for later goals; new words replace an unapproved proposal.

Approved tasks get their own cards and Discord threads. Up to two workers use separate worktrees; independent review and automated checks precede automatic publication. Commit/push does not wait for another approval. Published tasks then ask for Accept or Request changes. Dependencies wait for acceptance of the current published commit, independently of TestFlight delivery. Replying with changes reopens the same task, conversation and worktree.

Use the quick-questions card or Discord meatbag-talk for read-only conversation while workers are busy or paused. That conversation cannot start, stop or approve work. Claude resumes saved sessions; quick chat resets after three quiet hours. Codex receives the saved transcript. Questions offer a few choices, recommended first, and an Answer in my own words form. You can always reply normally.

On this manager card or in the project channel, say status, pause, resume or stop. Pause lets active workers finish and holds queued work; stop interrupts managed local processes while preserving their work. Resume releases stopped work. In task conversations, answer a question, say continue after fixing a prerequisite, or cancel that task. Say make a new TestFlight build here to explicitly build current main. Discord has five global commands: /status, /models, /pause, /resume, /stop. Routine updates and live progress have no buttons; only current decisions show controls.

Only mapped owners can request work. Card creation, dragging statuses and manager-generated replies do not start tasks. Leave cards unassigned to native Multica agents. Game work is limited to engine/, games/tapdemo/ and tests/. The coordinator owns checks, publication and distribution; provider processes cannot publish. TestFlight readiness requires verified Apple processing, tester assignment and internal testing state. iPhone performance requires a device measurement.

Task cards contain results, questions, review state, dependencies, PR and build links. Status separates active local workers from Waiting on services, and groups Needs you, Queued and reported Usage. A plan whose coding is published waits In review for your acceptance. The last_seen metadata confirms the board connection.`;

export const quickGuide=`Ask the manager questions here in ordinary comments: what is happening, how the game works, or what a change might involve. This is an ongoing read-only conversation with the current coordinator and board context, available while workers are busy or paused. It cannot edit files, approve a plan, start builds or stop work. Describe a goal on the manager card when you want implementation. Claude resumes the conversation and quick chat starts fresh after three quiet hours. The same lane is available in Discord's meatbag-talk thread.`;

// Context, not an LLM's guessed intent, authorizes work. New prose plans read-only;
// only an unambiguous approval of a current proposal creates implementation tasks.
export function control(text:string):'approve'|'resume'|'cancel'|'status'|'accept'|undefined {
  const value=text.trim().replace(/[.!]+$/,'').toLowerCase();
  if(/^(?:approve(?: (?:the )?plan)?|approved|go ahead|looks good(?:,? (?:go ahead|do it))?|yes(?:,? (?:go ahead|do it))?|do it|ship it)$/.test(value)) return 'approve';
  if(/^(?:accept|accepted|accept (?:this|the) result|it works)$/.test(value)) return 'accept';
  if(/^(?:resume|continue|try again|retry|it's fixed|it is fixed|done)$/.test(value)) return 'resume';
  if(/^(?:cancel(?: (?:this|the) task)?|stop(?: (?:this|the) task)?)$/.test(value)) return 'cancel';
  if(/^(?:status|what(?:'s| is) happening(?: now)?\??|what is it doing(?: now)?\??|where are we\??)$/.test(value)) return 'status';
}
export function buildRequest(text:string):boolean {
  return /^(?:please )?(?:build(?: (?:latest main|the app|for testflight|tapdemo))?|(?:make|upload|create) (?:a |the )?(?:new |latest )?(?:testflight )?build)[.!]*$/i.test(text.trim());
}
export function statusText(task:Task,store:Store):string {
  const children=store.list().filter(t=>t.parentTaskId===task.id);
  return `${task.game}: ${task.status}\n${task.question || task.error || task.progress || task.summary || ''}\n${task.prUrl || ''}\n${task.runUrl || ''}`.trim()
    +(children.length ? '\n\nApproved tasks:\n'+children.map(t=>`${t.prompt.split('\n')[0]}: ${t.status}${t.runUrl ? ' · '+t.runUrl : ''}`).join('\n') : '');
}
export async function reply(store:Store,coordinator:Coordinator,task:Task,text:string,eventId:string,userId:string,approvalVersion?:number) {
  let action=control(text);
  if(action==='approve') {
    if(task.kind==='change' && task.reviewSha) {store.accept(task.id,task.reviewSha);return;}
    if(task.kind!=='plan' || !task.proposal?.length) action=undefined;
  }
  if(action==='approve') {
    if(approvalVersion!==undefined && task.proposalAt!==approvalVersion) throw new Error('That plan was replaced. Approve the latest proposal.');
    store.approvePlan(task.id,userId);return;
  }
  if(action==='status') {store.notify(task.id,statusText(task,store));return;}
  if(action==='accept') {store.accept(task.id,task.reviewSha || '');return;}
  if(action==='cancel') {
    if(task.kind==='plan' && task.approvedBy) for(const child of store.list().filter(t=>t.parentTaskId===task.id)) await coordinator.cancel(child.id);
    await coordinator.cancel(task.id);return;
  }
  if(task.kind==='change' && task.mergeSha && !action) {await coordinator.requestChanges(task.id,text);return;}
  if(!store.message(task.id,'user',text,eventId)) return;
  if(task.kind==='ask' || task.kind==='plan') {
    const active=task.status==='implementing';
    store.update(task.id,{replyVersion:(task.replyVersion || 0)+1,proposal:undefined,proposalAt:undefined,approvedBy:undefined,question:undefined,
      questions:undefined,answers:undefined,questionVersion:undefined,status:active ? task.status : 'queued',error:undefined});
    if(active) store.notify(task.id,'Got it. I’ll read this as soon as the current turn finishes.');
  } else if(['waiting_input','interrupted','failed'].includes(task.status)) {
    store.update(task.id,{questions:undefined,questionVersion:undefined,answers:undefined});
    await coordinator.resume(task.id);store.notify(task.id,'Got it. Resuming the requested task.');
  } else if(action==='resume') throw new Error('This task is already running or finished. Ask a question here, or describe the next goal in the project channel.');
  else if(task.kind==='change') {
    store.update(task.id,{replyVersion:(task.replyVersion || 0)+1,...(task.status==='awaiting_checks' ? {status:'queued' as const,headSha:undefined} : {})});
    store.notify(task.id,'Got it. Your words go into this task’s next turn.');
  }
}

export async function answer(store:Store,coordinator:Coordinator,task:Task,version:number,values:Record<number,string>,eventId:string,userId:string) {
  if(task.status!=='waiting_input' || task.questionVersion!==version || !task.questions?.length) throw new Error('These questions were answered or replaced. Use the latest question, or reply in the thread.');
  const answers={...task.answers};
  for(const [key,value] of Object.entries(values)) {const i=Number(key);if(!Number.isInteger(i) || !task.questions[i]) throw new Error('Unknown question');if(value.trim()) answers[i]=value.trim();}
  store.update(task.id,{answers});
  if(task.questions.some((_q,i)=>!answers[i])) return false;
  const text=task.questions.map((q,i)=>`${q.question}\nOwner's answer: ${answers[i]}`).join('\n\n');
  await reply(store,coordinator,store.get(task.id)!,text,eventId,userId);return true;
}

export function hearQuick(store:Store,config:Config,input:{key:string;text:string;eventId:string;userId:string;game:string;channelId:string;threadId?:string;boardIssueId?:string}) {
  const prior=store.list().filter(t=>t.quickKey===input.key).at(-1);
  const reuse=prior && Date.now()-prior.updatedAt<3*3600_000;
  const task=reuse ? prior : store.create({eventId:input.eventId,kind:'ask',game:input.game,provider:config.quickProvider || config.defaultProvider,
    model:config.quickModel,userId:input.userId,channelId:input.channelId,prompt:input.text});
  if(!store.message(task.id,'user',input.text,input.eventId)) return task;
  return store.update(task.id,{source:input.boardIssueId ? 'multica' : 'discord',quickKey:input.key,threadId:input.threadId,boardIssueId:input.boardIssueId || task.boardIssueId,
    conversationParentId:'talk',replyVersion:(task.replyVersion || 0)+1,status:task.status==='implementing' ? task.status : 'queued',error:undefined,question:undefined});
}
