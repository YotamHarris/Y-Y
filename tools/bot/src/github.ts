import type { Config } from './config.js';

export class GitHub {
  constructor(private config:Pick<Config,'repository'|'githubToken'>,private request:typeof fetch=fetch) {}
  private async api<T>(path:string,method='GET',body?:unknown):Promise<T> {
    const response=await this.request(`https://api.github.com/repos/${this.config.repository}${path}`,{method,
      headers:{Authorization:`Bearer ${this.config.githubToken}`,Accept:'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/json'},
      body:body===undefined ? undefined : JSON.stringify(body),signal:AbortSignal.timeout(30_000)});
    if(!response.ok) throw new Error(`GitHub ${method} ${path}: ${response.status} ${response.statusText}`);
    if(response.status===204) return undefined as T;
    return response.json() as Promise<T>;
  }
  async main():Promise<string> { const branch=await this.api<{commit:{sha:string}}>('/branches/main'); return branch.commit.sha; }
  async protectedMain():Promise<boolean> {
    const rules=await this.api<{strict:boolean;contexts:string[];checks?:{context:string}[]}>('/branches/main/protection/required_status_checks');
    const contexts=new Set([...rules.contexts,...(rules.checks || []).map(c=>c.context)]);
    return rules.strict && ['automation','windows','ios'].every(name=>contexts.has(name));
  }
  async pull(branch:string,title:string,body:string) {
    const owner=this.config.repository.split('/')[0];
    const existing=await this.api<any[]>(`/pulls?state=all&head=${encodeURIComponent(`${owner}:${branch}`)}`);
    if(existing[0]) return existing[0] as {number:number;html_url:string;merged_at?:string;merge_commit_sha?:string};
    return this.api<{number:number;html_url:string}>('/pulls','POST',{title,body,head:branch,base:'main'});
  }
  pullState(number:number) { return this.api<{merged:boolean;merge_commit_sha:string|null;state:string;head:{sha:string}}>(`/pulls/${number}`); }
  async checks(sha:string):Promise<'pending'|'passed'|'failed'> {
    const result=await this.api<{check_runs:{name:string;status:string;conclusion:string|null}[]}>(`/commits/${sha}/check-runs?per_page=100&filter=latest`);
    const required=['automation','windows','ios'];
    for(const name of required) {
      const checks=result.check_runs.filter(c=>c.name===name);
      if(checks.some(c=>c.status==='completed' && c.conclusion!=='success')) return 'failed';
    }
    return required.every(name=>result.check_runs.some(c=>c.name===name && c.status==='completed' && c.conclusion==='success')) ? 'passed' : 'pending';
  }
  async merge(number:number,sha:string) {
    const result=await this.api<{merged:boolean;sha:string;message:string}>(`/pulls/${number}/merge`,'PUT',{sha,merge_method:'squash'});
    if(!result.merged) throw new Error(`Merge blocked: ${result.message}`); return result.sha;
  }
  async runs(sha:string) {
    type Runs={workflow_runs:{id:number;html_url:string;status:string;conclusion:string|null;head_sha:string;display_title:string}[]};
    // Dispatch metadata describes the workflow ref, which can differ from the pinned checkout SHA.
    const [pinned,recent]=await Promise.all([
      this.api<Runs>(`/actions/workflows/ios-testflight.yml/runs?head_sha=${encodeURIComponent(sha)}&per_page=100`),
      this.api<Runs>('/actions/workflows/ios-testflight.yml/runs?per_page=100')
    ]);
    return [...new Map([...recent.workflow_runs,...pinned.workflow_runs].map(r=>[r.id,r])).values()];
  }
  async dispatch(sha:string,game:string,taskId:string) {
    // The workflow definition comes from main; its checkout is pinned to this merged SHA.
    await this.api('/actions/workflows/ios-testflight.yml/dispatches','POST',{ref:'main',inputs:{sha,game,task_id:taskId}});
  }
  cancelRun(id:number) { return this.api(`/actions/runs/${id}/cancel`,'POST'); }
  rerun(id:number) { return this.api(`/actions/runs/${id}/rerun-failed-jobs`,'POST'); }
  async buildState(runId:number,game:string):Promise<{status:'building'|'uploaded'|'processing'|'ready'|'failed';url:string;detail?:string}> {
    const run=await this.api<{status:string;conclusion:string|null;html_url:string;jobs_url:string}>(`/actions/runs/${runId}`);
    const jobs=await this.api<{jobs:{name:string;status:string;conclusion:string|null;steps:{name:string;conclusion:string|null}[]}[]}>(`/actions/runs/${runId}/jobs?per_page=100`);
    const job=jobs.jobs.find(j=>j.name===`TestFlight (${game})`);
    const steps=job?.steps || [];
    if(job?.status==='completed' && job.conclusion!=='success') return {status:'failed',url:run.html_url,detail:`Build job ${job.conclusion}`};
    if(steps.some(s=>s.name==='Verify TestFlight readiness' && s.conclusion==='success') && job?.conclusion==='success') return {status:'ready',url:run.html_url};
    if(run.status==='completed' && !job) return {status:'failed',url:run.html_url,detail:'Workflow completed without this game build'};
    if(steps.some(s=>s.name==='Upload build' && s.conclusion==='success')) return {status:steps.some(s=>s.name==='Distribute to internal testers') ? 'processing' : 'uploaded',url:run.html_url};
    return {status:'building',url:run.html_url};
  }
}
