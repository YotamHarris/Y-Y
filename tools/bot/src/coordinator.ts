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

export class Coordinator {
  private busy=false;
  private stopping=false;
  private active?:{id:string;abort:AbortController};
  constructor(private config:Config,public store:Store,private git:Git,private github:GitHub,private providers:Providers) {}
  private stage(id:string,status:Task['status'],detail?:string) {
    if(this.store.get(id)?.cancelRequested) throw new Error('Cancelled');
    const task=this.store.update(id,{status}); this.store.notify(id,`${status}${detail ? ` — ${detail}` : ''}`); return task;
  }
  async tick() {
    if(this.busy || this.stopping) return; this.busy=true;
    try {
      // Recover remote work before accepting another change; main evolves serially.
      const task=this.store.list().find(t=>t.cancelPending || remote.has(t.status)) || this.store.list().find(t=>t.status==='queued' && (t.threadId || t.source==='multica') && this.store.runnable(t));
      if(!task) return;
      const abort=new AbortController();this.active={id:task.id,abort};
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
        this.store.notify(task.id,`${status}: ${error.slice(-1500)}${current.mergeSha ? `\nAlready merged: ${current.mergeSha}` : ''}\nTask: ${task.id}`);
      }
    } finally {this.active=undefined;this.busy=false;}
  }
  private async agent(task:Task,prompt:string,signal:AbortSignal,readonly=false,planning=false) {
    return this.providers.execute(task.provider,{cwd:task.worktree || this.config.root,prompt,model:task.model,readonly,planning,signal,onEvent:event=>{
      if(event.sessionId) this.store.update(task.id,{sessionId:event.sessionId});
      if(event.type==='progress' && event.text) this.store.update(task.id,{progress:redact(event.text).slice(-4000)});
    }});
  }
  private async local(task:Task,signal:AbortSignal) {
    const context=task.kind==='ask' || task.kind==='plan' ? projectContext(this.config.root,this.config.games[task.game]!.directory) : '';
    if(task.kind==='plan') {
      this.stage(task.id,'implementing','Read-only planning');
      const result=await this.agent(task,`Plan the requested change to ${task.game}, reading README.md, docs/setup.md, AGENTS.md and relevant code from the supplied snapshot. You may use read-only file inspection (rg, Get-Content, git diff/status) if available, but no commands are needed to read the supplied files. Do not edit files, run builds, install tools or change repository state. Propose few whole implementation tasks, at most eight, each with a title, prompt explaining Why, Approach and What to do, and depends containing zero-based indices of earlier tasks. Implementation may only touch engine/, games/${task.game}/ and tests/. Ask about unresolved choices and return tasks=[] while choices remain. Never propose infrastructure edits. Approval belongs to the owner.\n${context}\n${this.store.context(task.id)}\n${task.prompt}`,signal,true,true);
      const proposed=!!result.tasks?.length;
      const question=result.question || (proposed ? 'Review the proposed tasks and comment /yy approve to start implementation.' : undefined);
      this.store.message(task.id,'assistant',result.summary+'\n'+result.question);
      this.store.update(task.id,{status:proposed || result.outcome==='needs_input' ? 'waiting_input' : 'completed',summary:result.summary,question,proposal:result.tasks || [],proposalAt:Date.now()});
      this.store.notify(task.id,result.summary+'\n'+(result.tasks || []).map((t,i)=>`${i+1}. ${t.title}\n${t.prompt}\nDepends on: ${t.depends.map(n=>n+1).join(', ') || 'none'}`).join('\n\n')+(question ? '\n'+question : ''));return;
    }
    if(task.kind==='ask') {
      this.stage(task.id,'implementing','Read-only discussion');
      const result=await this.agent(task,`Discuss ${task.game}. Use the supplied file snapshot; no commands are needed to read it. Read-only inspection commands (rg, Get-Content, git diff/status) are allowed if available. Do not edit files, run builds, install tools or change repository state.\n${context}\n${this.store.context(task.id)}\n${task.prompt}`,signal,true);
      this.store.message(task.id,'assistant',result.summary+'\n'+result.question);
      this.store.update(task.id,{status:result.outcome==='needs_input' ? 'waiting_input' : 'completed',summary:result.summary,question:result.question || undefined});
      this.store.notify(task.id,result.summary+(result.question ? '\n'+result.question : ''));return;
    }
    this.stage(task.id,'preparing');
    task=this.store.update(task.id,await this.git.prepare(task,signal));
    // A stale candidate has already been implemented. Rebase then validate/review again.
    let feedback='';
    for(let attempt=task.attempt;attempt<=2;++attempt) {
      this.store.update(task.id,{attempt});
      if(!task.headSha || feedback) {
        this.stage(task.id,'implementing',attempt ? `Repair ${attempt}/2` : undefined);
        const result=await this.agent(task,`Implement this game change: ${task.prompt}\nConversation:\n${this.store.context(task.id)}\nOnly edit engine/, ${this.config.games[task.game]!.directory}/, and tests/. Do not change automation, workflows, config, dependencies, or git metadata.\n${feedback}`,signal);
        if(result.outcome==='needs_input') {this.store.message(task.id,'assistant',result.summary+'\n'+result.question);this.store.update(task.id,{status:'waiting_input',summary:result.summary,question:result.question || result.summary});this.store.notify(task.id,result.question || result.summary);return;}
        this.store.update(task.id,{summary:result.summary});
      }
      try {
        task=this.store.update(task.id,{headSha:await this.git.commit(task,signal)});
        if(task.headSha===task.baseSha) {this.stage(task.id,'completed','No code changes were needed');return;}
        this.stage(task.id,'checking');await this.git.validate(task,signal);
        this.stage(task.id,'reviewing');
        const diff=await this.git.diff(task,signal);
        const result=await this.agent(task,`Independently review the candidate against request: ${task.prompt}\nDo not edit files. Inspect correctness, tests, mobile lifecycle, and unintended changes. Return review=approve only if acceptable, otherwise request_changes.\nDiff:\n${diff.slice(0,120_000)}`,signal,true);
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
    // Infrastructure failures must not consume coding repair attempts or rewrite a reviewed candidate.
    await this.git.push(task,signal);
    const pr=await this.github.pull(task.branch!,`Update ${task.game}: ${task.prompt.replace(/[\r\n]/g,' ').slice(0,100)}`,
      `${this.store.get(task.id)?.summary || task.prompt}\n\nValidation: local engine/game tests and Release compilation; independent agent review. CI checks must pass before automatic merge.\n\nTask: ${task.id}`);
    this.store.update(task.id,{pr:pr.number,prUrl:pr.html_url});this.stage(task.id,'awaiting_checks',pr.html_url);
  }
  private async reconcile(task:Task,signal:AbortSignal) {
    if(task.status==='awaiting_checks' || task.status==='merging') {
      if(!task.pr || !task.headSha) throw new Error('Missing persisted PR reference');
      const pull=await this.github.pullState(task.pr);
      if(pull.merged) {task=this.store.update(task.id,{mergeSha:pull.merge_commit_sha!});await this.startBuild(task);return;}
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
      this.stage(task.id,'merging');
      task=this.store.update(task.id,{mergeSha:await this.github.merge(task.pr,task.headSha)});
      if(task.cancelRequested) {
        this.store.update(task.id,{status:'cancelled',cancelPending:true});
        this.store.notify(task.id,`The in-flight merge completed before cancellation: ${task.mergeSha}. Waiting to cancel its remote build.`);return;
      }
      this.store.notify(task.id,`Merged ${task.mergeSha}.`);await this.startBuild(task);return;
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
    if(this.active?.id===id) this.active.abort.abort();
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
    this.stopping=true;this.active?.abort.abort();
    while(this.busy) await new Promise(resolve=>setTimeout(resolve,25));
  }
}
