import type { Config } from './config.js';
import { Store } from './store.js';
import { Git } from './git.js';
import { GitHub } from './github.js';
import { Providers, AgentPaused } from './providers.js';
import { redact, ProcessFailure } from './process.js';
import { remote, terminal, type Task } from './types.js';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { projectContext } from './manager-context.js';
import { managerMode, managerOutlook, type ManagerMode } from './manager-state.js';

export class Coordinator {
  private stopping=false;
  private workers=new Map<string,AbortController>();
  private readers=new Map<'ask'|'plan',{id:string;abort:AbortController}>();
  private remoting=false;
  private remoteActive?:{id:string;abort:AbortController};
  constructor(private config:Config,public store:Store,private git:Git,private github:GitHub,private providers:Providers) {}
  private stage(id:string,status:Task['status'],detail?:string) {
    if(this.store.get(id)?.cancelRequested) throw new Error('Cancelled');
    const progress=status==='checking' ? 'Running coordinator tests, Release compilation and rendering smoke.' : status==='reviewing' ? 'Coordinator validations passed; obtaining independent review.' : status==='awaiting_checks' ? 'Local validations and review passed; waiting for required CI.' : undefined;
    const task=this.store.update(id,{status,...(progress ? {progress} : {})});
    if(status==='ready') this.store.notify(id,`TestFlight upload, Apple processing, tester assignment and internal testing readiness verified.\n${task.runUrl || detail || ''}`);
    if(status==='completed') this.store.notify(id,detail || 'No changes were needed.');
    return task;
  }
  async tick(readonly=false) {
    if(readonly) {await Promise.all([this.tickConversation('plan'),this.tickConversation('ask')]);return;}
    if(this.stopping || managerMode(this.store)==='stopped') return;
    await this.tickRemote();
    if(managerMode(this.store)!=='running') return;
    const available=(this.config.workerSlots || 2)-this.workers.size;
    const tasks=this.store.list().filter(t=>!['ask','plan'].includes(t.kind) && t.status==='queued' && !this.workers.has(t.id) && (t.threadId || t.source==='multica') && this.store.runnable(t)).slice(0,Math.max(0,available));
    const runs=tasks.map(task=>{const abort=new AbortController();this.workers.set(task.id,abort);return this.executeTask(task,abort,'worker').finally(()=>this.workers.delete(task.id));});
    if(runs.length) await Promise.all(runs);else await this.tick(true);
  }
  async tickConversation(kind:'ask'|'plan') {
    if(this.stopping || this.readers.has(kind) || managerMode(this.store)==='stopped') return;
    const task=this.store.list().find(t=>t.kind===kind && t.status==='queued' && (t.threadId || t.source==='multica'));if(!task) return;
    const abort=new AbortController();this.readers.set(kind,{id:task.id,abort});
    try {await this.executeTask(task,abort,'reader');}finally{this.readers.delete(kind);}
  }
  async tickRemote() {
    if(this.stopping || this.remoting || managerMode(this.store)==='stopped') return;
    const task=this.store.list().filter(t=>t.cancelPending || remote.has(t.status)).sort((a,b)=>(a.remotePollAt || 0)-(b.remotePollAt || 0))[0];if(!task) return;
    this.store.update(task.id,{remotePollAt:Date.now()});
    this.remoting=true;const abort=new AbortController();this.remoteActive={id:task.id,abort};
    try {await this.executeTask(task,abort,'remote');}finally{this.remoting=false;this.remoteActive=undefined;}
  }
  async setMode(mode:ManagerMode) {
    this.store.setState('manager-mode',mode);
    if(mode==='stopped') {
      for(const abort of this.workers.values()) abort.abort('manager-stop');
      for(const reader of this.readers.values()) reader.abort.abort('manager-stop');
      this.remoteActive?.abort.abort('manager-stop');
    }
    if(mode==='running') for(const task of this.store.list().filter(t=>t.status==='interrupted' && t.error==='Manager stopped; work and sessions preserved.')) await this.resume(task.id);
  }
  private async executeTask(task:Task,abort:AbortController,lane:'worker'|'reader'|'remote') {
    const started=Date.now();
    if(lane!=='remote') this.store.update(task.id,{runStartedAt:started,runCount:(task.runCount || 0)+1,progress:undefined,lastStep:undefined});
    try {
        if(task.cancelRequested) { await this.cancel(task.id); return; }
        if(remote.has(task.status)) await this.reconcile(task,abort.signal);
        else if(task.kind==='build') {
          const sha=task.mergeSha || await this.github.main();
          const next=this.store.update(task.id,{mergeSha:sha});
          await this.startBuild(next);
        } else await this.local(task,abort.signal);
      } catch(err) {
        const current=this.store.get(task.id)!;
        if(this.stopping) return; // Startup recovery marks unfinished local work interrupted.
        if(current.status==='cancelled') return;
        if(abort.signal.reason==='manager-stop' || managerMode(this.store)==='stopped') {
          if(lane!=='remote') this.store.update(task.id,{status:managerMode(this.store)==='running' ? 'queued' : 'interrupted',error:managerMode(this.store)==='running' ? undefined : 'Manager stopped; work and sessions preserved.',pausedFrom:current.status});
          return;
        }
        if(remote.has(current.status) && !(err instanceof AgentPaused) && !current.cancelRequested && /GitHub|fetch failed|network|ECONN|timeout/i.test(String(err))) {
          if(!current.remoteErrorAt || Date.now()-current.remoteErrorAt>300_000) {
            this.store.update(task.id,{remoteErrorAt:Date.now()});this.store.notify(task.id,'GitHub is temporarily unavailable; keeping the task for reconciliation.');
          }
          return;
        }
        const error=redact(err instanceof ProcessFailure ? `${err.message}\n${err.output}` : err instanceof Error ? err.message : String(err));
        mkdirSync(resolve(this.config.data,'logs'),{recursive:true});writeFileSync(resolve(this.config.data,'logs',`${task.id}.log`),error);
        const status=current.cancelRequested ? 'cancelled' : err instanceof AgentPaused ? 'waiting_input' : 'failed';
        this.store.update(task.id,{status,error:error.slice(-10_000),pausedFrom:status==='waiting_input' ? current.status : undefined});
        this.store.notify(task.id,`${status==='waiting_input' ? 'Needs your attention' : 'Stopped after a failure'}: ${error.split('\n')[0]?.slice(0,400)}${current.mergeSha ? `\nAlready published: ${current.mergeSha.slice(0,12)}` : ''}\nReply with your answer, or continue after fixing the prerequisite.`);
    } finally {
      if(lane!=='remote') {const latest=this.store.get(task.id)!;this.store.update(task.id,{runStartedAt:undefined,totalRunMs:(latest.totalRunMs || 0)+Date.now()-started});}
    }
  }
  private async agent(task:Task,prompt:string,signal:AbortSignal,readonly=false,planning=false) {
    const worker=task.kind==='change' && !readonly;
    const resume=worker ? task.workerSessionId : task.kind==='plan' || task.kind==='ask' ? task.sessionId : undefined;
    const sessionId=task.provider==='claude' && (task.kind!=='ask' || Date.now()-(task.sessionAt || 0)<3*3600_000) ? resume : undefined;
    return this.providers.execute(task.provider,{cwd:task.worktree || this.config.root,prompt,model:task.model,readonly,planning,signal,sessionId,
      resumePrompt:worker ? prompt : `The owner said:\n${this.store.latestUser(task.id)}\n\nContinue the conversation. Read only; do not edit files or start work.\n${managerOutlook(this.store)}\nLatest board observations:\n${this.store.state(`manager-board-context:${task.game}`) || ''}`,
      onEvent:event=>{
      if(event.sessionId && (worker || ['plan','ask'].includes(task.kind))) this.store.update(task.id,worker ? {workerSessionId:event.sessionId} : {sessionId:event.sessionId,sessionAt:Date.now()});
      if(event.type==='progress' && event.text) this.store.update(task.id,{progress:redact(event.text).slice(-4000)});
      if(event.type==='step' && event.text) this.store.update(task.id,{lastStep:redact(event.text).slice(0,500)});
      if(event.type==='usage' && event.usage) {const usage=this.store.get(task.id)?.usage || {input:0,output:0,cached:0};this.store.update(task.id,{usage:{input:usage.input+event.usage.input,output:usage.output+event.usage.output,cached:usage.cached+event.usage.cached}});}
    }});
  }
  private async local(task:Task,signal:AbortSignal) {
    const context=task.kind==='ask' || task.kind==='plan' ? projectContext(this.config.root,this.config.games[task.game]!.directory) : '';
    const live=this.store.list().filter(t=>t.game===task.game && t.id!==task.id && !t.conversationParentId).map(t=>({
      id:t.id,request:t.prompt.slice(0,500),status:t.status,question:t.question,error:t.error?.slice(-1000),summary:t.summary,progress:t.progress,pr:t.prUrl,build:t.runUrl,
      dependencies:t.dependencies,approvedTasks:t.approvedBy ? this.store.list().filter(c=>c.parentTaskId===t.id).map(c=>c.id) : undefined
    }));
    const outlook=`Current coordinator state (observations, not authorization; a failed historical build does not establish the latest Apple state):\n${redact(JSON.stringify(live)).slice(-15000)}\nLatest synchronized board observations:\n${this.store.state(`manager-board-context:${task.game}`) || 'No board snapshot available.'}\nOwner's latest message: ${this.store.latestUser(task.id)}`;
    if(task.kind==='plan') {
      this.stage(task.id,'implementing','Read-only planning');
      const result=await this.agent(task,`Talk with the owner about ${task.game} in plan mode, as a colleague in a Claude Code planning session. Their words are their words: quote them accurately; label your interpretation as yours. An open request or an exploration is not a chosen design. Answer their latest words directly, explore the relevant code, and ask only about material choices. Offer two to four concrete options, recommended first, in asks when a choice is needed. Read the supplied snapshot; read-only file inspection is allowed. Do not edit files, run builds, install tools or change repository state. Once the goal is concrete, propose few whole tasks, at most eight. Each task explains Why, the approach, what should be true afterwards, and the evidence that would establish it. depends contains zero-based indices of earlier tasks; split only when something must be accepted before another part starts. Implementation may only touch engine/, games/${task.game}/ and tests/. Return tasks=[] for discussion or unresolved choices. Never propose infrastructure edits; explain a scope limitation plainly. Give this goal a short descriptive goalName. The owner refines the plan by replying, then approves it once. This same planning conversation stays open afterwards. Do not tell them to use commands.\n${context}\n${outlook}\n${this.store.context(task.id)}`,signal,true,true);
      if(this.store.get(task.id)?.cancelRequested) return;
      if((this.store.get(task.id)?.replyVersion || 0)!==(task.replyVersion || 0)) {
        this.store.update(task.id,{status:'queued',proposal:undefined,proposalAt:undefined});
        this.store.notify(task.id,'I’ve read your follow-up. Updating the plan before asking for approval.');return;
      }
      const proposed=!!result.tasks?.length;
      const question=result.question || (proposed ? 'Reply “approve” to start these tasks, or tell me what to change.' : undefined);
      this.store.message(task.id,'assistant',result.summary+'\n'+result.question);
      this.store.update(task.id,{status:proposed || result.outcome==='needs_input' ? 'waiting_input' : 'completed',summary:result.summary,question,proposal:result.tasks || [],proposalAt:Date.now(),
        goalName:result.goalName || task.goalName,questions:result.asks?.length ? result.asks : result.outcome==='needs_input' && result.question ? [{question:result.question,options:[]}] : undefined,questionVersion:Date.now(),answers:undefined});
      this.store.notify(task.id,result.summary+'\n'+(result.tasks || []).map((t,i)=>`${i+1}. ${t.title}\n${t.prompt.replace(/\s+/g,' ').slice(0,350)}${t.depends.length ? '\nAfter task '+t.depends.map(n=>n+1).join(', ') : ''}`).join('\n\n')+(question ? '\n'+question : ''));return;
    }
    if(task.kind==='ask') {
      this.stage(task.id,'implementing','Read-only discussion');
      const result=await this.agent(task,`Answer the owner's latest question about ${task.game} concisely. Use the supplied file snapshot and current coordinator/board observations, preferring live evidence over old README acceptance statements. Do not invent progress or claim TestFlight readiness without verified evidence. Read-only inspection commands (rg, Get-Content, git diff/status) are allowed if available. Do not edit files, run builds, install tools, start tasks or change repository state. A change described in this conversation remains a discussion; direct the owner to describe a goal on the manager card or in the project channel for a plan.\n${context}\n${outlook}\n${this.store.context(task.id)}`,signal,true);
      if(this.store.get(task.id)?.cancelRequested) return;
      this.store.message(task.id,'assistant',result.summary+'\n'+result.question);
      this.store.update(task.id,{status:(this.store.get(task.id)?.replyVersion || 0)!==(task.replyVersion || 0) ? 'queued' : result.outcome==='needs_input' ? 'waiting_input' : 'completed',summary:result.summary,question:result.question || undefined,
        questions:result.asks?.length ? result.asks : result.outcome==='needs_input' && result.question ? [{question:result.question,options:[]}] : undefined,questionVersion:Date.now(),answers:undefined});
      this.store.notify(task.id,result.summary+(result.question ? '\n'+result.question : ''));return;
    }
    this.stage(task.id,'preparing');
    task=this.store.update(task.id,await this.git.prepare(task,signal));
    const turnVersion=task.replyVersion || 0;
    // A stale candidate has already been implemented. Rebase then validate/review again.
    let feedback='';
    for(let attempt=task.attempt;attempt<=2;++attempt) {
      this.store.update(task.id,{attempt});
      if(!task.headSha || feedback) {
        this.stage(task.id,'implementing',attempt ? `Repair ${attempt}/2` : undefined);
        const result=await this.agent(task,`Implement this game change: ${task.prompt}\nConversation:\n${this.store.context(task.id)}\nOnly edit engine/, ${this.config.games[task.game]!.directory}/, and tests/. Do not change automation, workflows, config, dependencies, or git metadata. The coordinator runs required validation, including scripts/build.ps1 -Smoke, outside your provider sandbox after you finish; it then obtains independent review before publication. You do not need to run builds yourself. If your sandbox cannot access a compiler or validation tool, report that limitation in summary and return completed once the requested edits are ready for the coordinator's checks. Completed means candidate implementation finished, not validated or published. Use needs_input only for an unresolved owner choice or a permission needed to make the actual edits, not to ask the owner to run the coordinator's validations.\n${feedback}`,signal);
        if((this.store.get(task.id)?.replyVersion || 0)!==turnVersion) {this.store.update(task.id,{status:'queued',headSha:undefined});return;}
        if(result.outcome==='needs_input') {this.store.message(task.id,'assistant',result.summary+'\n'+result.question);this.store.update(task.id,{status:'waiting_input',summary:result.summary,question:result.question || result.summary,
          questions:result.asks?.length ? result.asks : [{question:result.question || result.summary,options:[]}],questionVersion:Date.now(),answers:undefined});this.store.notify(task.id,result.summary+(result.question ? '\n'+result.question : ''));return;}
        this.store.update(task.id,{summary:result.summary});
      }
      try {
        task=this.store.update(task.id,{headSha:await this.git.commit(task,signal)});
        if(task.headSha===task.baseSha) {this.stage(task.id,'completed','No code changes were needed');return;}
        this.stage(task.id,'checking');await this.git.validate(task,signal);
        this.stage(task.id,'reviewing');
        const diff=await this.git.diff(task,signal);
        const result=await this.agent(task,`Independently review the candidate against request: ${task.prompt}\nOwner conversation and revisions:\n${this.store.context(task.id)}\nThe coordinator's local tests, Release compilation and rendering smoke passed for candidate ${task.headSha}. Do not rerun builds or edit files. Inspect correctness, tests, mobile lifecycle, and unintended changes; read the generated smoke metrics/image if relevant. Return review=approve only if acceptable, otherwise request_changes.\nDiff:\n${diff.slice(0,120_000)}`,signal,true);
        if(result.outcome==='needs_input') throw new AgentPaused(result.question || result.summary);
        if(result.review!=='approve') throw new Error(`Review requested changes: ${result.summary}`);
        await this.git.assertClean(task,signal);
        break;
      } catch(err) {
        if(signal.aborted || err instanceof AgentPaused) throw err;
        if(attempt===2) throw err;
        feedback=`Repair the following validation failure, preserving the original request:\n${err instanceof ProcessFailure ? err.output : String(err)}`;
      }
    }
    if((this.store.get(task.id)?.replyVersion || 0)!==turnVersion) {this.store.update(task.id,{status:'queued',headSha:undefined});return;}
    // Infrastructure failures must not consume coding repair attempts or rewrite a reviewed candidate.
    await this.git.push(task,signal);
    const pr=await this.github.pull(task.branch!,`Update ${task.game}: ${task.prompt.replace(/[\r\n]/g,' ').slice(0,100)}`,
      `${this.store.get(task.id)?.summary || task.prompt}\n\nValidation: local engine/game tests and Release compilation; independent agent review. CI checks must pass before automatic merge.\n\nTask: ${task.id}`);
    this.store.update(task.id,{pr:pr.number,prUrl:pr.html_url});
    if((this.store.get(task.id)?.replyVersion || 0)!==turnVersion) {this.store.update(task.id,{status:'queued',headSha:undefined});return;}
    this.stage(task.id,'awaiting_checks',pr.html_url);
  }
  private async reconcile(task:Task,signal:AbortSignal) {
    if(task.status==='awaiting_checks' || task.status==='merging') {
      if(!task.pr || !task.headSha) throw new Error('Missing persisted PR reference');
      const pull=await this.github.pullState(task.pr);
      if(pull.merged) {task=this.published(task,pull.merge_commit_sha!);await this.startBuild(task);return;}
      if(pull.state==='closed') throw new Error('PR was closed without merging');
      if(pull.head.sha!==task.headSha) throw new Error('PR head changed outside this task; refusing automatic merge');
      const base=await this.github.main();
      if(base!==task.baseSha) {
        this.store.update(task.id,{status:'queued'});this.store.notify(task.id,'Main changed; rebasing and repeating checks and review.');return;
      }
      const checks=await this.github.checks(task.headSha);
      if(checks==='pending') return;
      if(checks==='failed') {
        if(task.attempt>=2) throw new Error('CI checks failed after two repairs');
        this.store.message(task.id,'system',`CI failed. Inspect and fix the failures at ${task.prUrl}.`);
        this.store.update(task.id,{status:'queued',attempt:task.attempt+1,headSha:undefined});return;
      }
      await this.git.assertClean(task,signal);
      // Refresh the base immediately before merging the validated head SHA.
      if(await this.github.main()!==task.baseSha) {this.store.update(task.id,{status:'queued'});return;}
      if(this.store.get(task.id)?.headSha!==task.headSha || !['awaiting_checks','merging'].includes(this.store.get(task.id)!.status)) return;
      this.stage(task.id,'merging');
      const mergingVersion=task.replyVersion || 0;
      task=this.store.update(task.id,{mergeSha:await this.github.merge(task.pr,task.headSha)});
      if(task.cancelRequested) {
        this.store.update(task.id,{status:'cancelled',cancelPending:true});
        this.store.notify(task.id,`The in-flight merge completed before cancellation: ${task.mergeSha}. Waiting to cancel its remote build.`);return;
      }
      task=this.published(task,task.mergeSha!);
      if((task.replyVersion || 0)!==mergingVersion) {await this.requestChanges(task.id,this.store.latestUser(task.id),task.mergeSha);return;}
      await this.startBuild(task);return;
    }
    if(!task.mergeSha) throw new Error('Missing build commit');
    if(!task.runId) {
      const runs=await this.github.runs(task.mergeSha);
      const selected=runs.find(r=>r.display_title.includes(task.id) || (task.kind==='change' && r.head_sha===task.mergeSha && r.display_title.includes('[all]')));
      if(!selected) {
        // Dispatch completion is ambiguous across crashes; reconcile before retrying.
        if(Date.now()-(task.dispatchAt || task.updatedAt)>120_000) await this.startBuild(task,true);
        return;
      }
      task=this.store.update(task.id,{runId:selected.id,runUrl:selected.html_url});
    }
    const state=await this.github.buildState(task.runId!,task.game);
    if(task.rerunAt && Date.now()-task.rerunAt<60_000 && state.status==='failed') return;
    if(state.status!==task.status) this.stage(task.id,state.status,`${state.detail || ''} ${state.url}`.trim());
  }
  private published(task:Task,sha:string) {
    const latest=this.store.get(task.id)!;
    task=this.store.update(task.id,{mergeSha:sha,reviewSha:sha,acceptance:latest.acceptedSha===sha ? 'accepted' : 'pending'});
    if(latest.reviewSha!==sha) this.store.notify(task.id,`Published and ready for your review.\n${task.summary || task.prompt}\nCoordinator tests, Release compilation, rendering smoke, independent review and required CI passed.\nCommit: ${sha}\nTry the result, then Accept or Request changes. Dependent tasks wait for your acceptance; the build continues separately.\n${task.prUrl || ''}`);
    return task;
  }
  async requestChanges(id:string,feedback:string,sha?:string) {
    const task=this.store.get(id);
    if(!task || task.kind!=='change' || !task.mergeSha || !task.reviewSha || sha && task.reviewSha!==sha) throw new Error('This review is outdated or has not been published. Use the latest result.');
    if(!feedback.trim()) throw new Error('Describe what should change.');
    this.store.message(id,'user',feedback);
    this.store.update(id,{status:'queued',revision:(task.revision || 0)+1,replyVersion:(task.replyVersion || 0)+1,
      deliveries:[...(task.deliveries || []),{sha:task.mergeSha,url:task.runUrl,runId:task.runId,status:task.status}],
      reviewSha:undefined,acceptedSha:undefined,acceptance:undefined,mergeSha:undefined,runId:undefined,runUrl:undefined,dispatchAt:undefined,
      headSha:undefined,pr:undefined,prUrl:undefined,attempt:0,error:undefined,question:undefined,cancelRequested:false});
    this.store.notify(id,'Got your changes. Reopening this task with its conversation and worktree; I’ll validate and publish the revision.');
  }
  private async startBuild(task:Task,retry=false) {
    const runs=await this.github.runs(task.mergeSha!);
    const existing=runs.find(r=>r.display_title.includes(task.id) || (task.kind==='change' && r.head_sha===task.mergeSha && r.display_title.includes('[all]')));
    if(existing) {this.store.update(task.id,{status:'building',runId:existing.id,runUrl:existing.html_url});return;}
    // Persist intent before the external request. Never rebuild merely because the PC restarted.
    this.stage(task.id,'building',`Commit ${task.mergeSha}`);
    // Main pushes already start distribution. Give GitHub time to expose that run.
    if(task.kind==='change' && !retry) return;
    this.store.update(task.id,{dispatchAt:Date.now()});
    await this.github.dispatch(task.mergeSha!,task.game,task.id);
  }
  async cancel(id:string) {
    const task=this.store.get(id);if(!task) throw new Error('Unknown task');
    if(terminal.has(task.status) && !task.cancelPending) return;
    this.store.update(id,{cancelRequested:true,status:'cancelled',cancelPending:!!task.mergeSha && (task.kind==='change' || !!task.dispatchAt || !!task.runId)});
    this.workers.get(id)?.abort();
    for(const reader of this.readers.values()) if(reader.id===id) reader.abort.abort();
    if(this.remoteActive?.id===id) this.remoteActive.abort.abort();
    let runId=task.runId;
    if(!runId && task.mergeSha) {
      const runs=await this.github.runs(task.mergeSha);
      const candidate=runs.find(r=>r.display_title.includes(task.id) || (task.kind==='change' && r.head_sha===task.mergeSha && r.display_title.includes('[all]')));
      if(candidate) {runId=candidate.id;this.store.update(id,{runId,runUrl:candidate.html_url});}
    }
    let cancelPending=!!task.mergeSha && !runId && (task.kind==='change' || !!task.dispatchAt);
    if(runId) {
      try {await this.github.cancelRun(runId);} catch(err) {
        const state=await this.github.buildState(runId,task.game);
        cancelPending=state.status!=='failed' && state.status!=='ready';
        if(!task.cancelPending) this.store.notify(id,`Could not confirm remote cancellation: ${String(err)}. Check ${task.runUrl}.`);
      }
    }
    this.store.update(id,{status:'cancelled',cancelPending});
    if(task.status!=='cancelled') this.store.notify(id,`Cancelled. ${task.mergeSha ? `Already merged: ${task.mergeSha}. Uploaded builds are not removed from TestFlight.${cancelPending ? ' Waiting to cancel the remote build.' : ''}` : 'Worktree and branch preserved.'}`);
  }
  async resume(id:string) {
    const task=this.store.get(id);if(!task) throw new Error('Unknown task');
    if(!['waiting_input','interrupted','failed'].includes(task.status)) throw new Error('Only paused, interrupted, or failed tasks can resume');
    if(task.mergeSha && task.runId && task.status==='failed') {
      await this.github.rerun(task.runId);this.store.update(id,{rerunAt:Date.now()});
    }
    this.store.update(id,{status:task.mergeSha ? 'building' : task.pausedFrom && remote.has(task.pausedFrom) ? task.pausedFrom : 'queued',cancelRequested:false,error:undefined,question:undefined});
  }
  async shutdown() {
    this.stopping=true;
    for(const abort of this.workers.values()) abort.abort();for(const reader of this.readers.values()) reader.abort.abort();this.remoteActive?.abort.abort();
    while(this.workers.size || this.readers.size || this.remoting) await new Promise(resolve=>setTimeout(resolve,25));
  }
}
