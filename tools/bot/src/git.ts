import { existsSync, mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import type { Config } from './config.js';
import type { Task } from './types.js';
import { run, withoutSecrets } from './process.js';

export function assertChangeScope(paths:string[],gameDirectory:string) {
  for(const path of paths) if(!path.startsWith('engine/') && !path.startsWith(`${gameDirectory}/`) && !path.startsWith('tests/')) {
    throw new Error(`Automated game task changed ${path}. Game tasks may edit the engine, selected game, and tests only.`);
  }
}
export function statusPaths(output:string):string[] {
  const entries=output.split('\0');const paths:string[]=[];
  for(let i=0;i<entries.length;++i) {
    const entry=entries[i];if(!entry) continue;
    paths.push(entry.slice(3));
    if(/[RC]/.test(entry.slice(0,2))) {const original=entries[++i];if(original) paths.push(original);}
  }
  return paths;
}
export class Git {
  private preparing:Promise<void>=Promise.resolve();
  constructor(private config:Pick<Config,'root'|'data'|'games'|'githubToken'>) {}
  private execute(cwd:string,args:string[],signal?:AbortSignal,auth=false) {
    const env=withoutSecrets();
    // Git receives an ephemeral HTTP header via environment, never in argv or a saved remote URL.
    if(auth) {
      env.GIT_CONFIG_COUNT='1';env.GIT_CONFIG_KEY_0='http.https://github.com/.extraheader';
      env.GIT_CONFIG_VALUE_0=`AUTHORIZATION: basic ${Buffer.from(`x-access-token:${this.config.githubToken}`).toString('base64')}`;
    }
    env.GIT_TERMINAL_PROMPT='0';
    return run('git',args,{cwd,signal,env,timeout:120_000});
  }
  async prepare(task:Task,signal:AbortSignal):Promise<{worktree:string;branch:string;baseSha:string}> {
    // Fetch and worktree registration touch the shared Git directory. Coding,
    // validation and review remain independent once each checkout is prepared.
    const previous=this.preparing;let release!:()=>void;
    this.preparing=new Promise<void>(resolve=>{release=resolve;});
    await previous;
    try {return await this.prepareWorktree(task,signal);}finally{release();}
  }
  private async prepareWorktree(task:Task,signal:AbortSignal):Promise<{worktree:string;branch:string;baseSha:string}> {
    const baseSha=(await this.execute(this.config.root,['fetch','origin','main'],signal,true),await this.execute(this.config.root,['rev-parse','origin/main'],signal)).trim();
    const branch=task.branch || `codex/${task.game}-${task.id}`;
    const worktree=task.worktree || resolve(this.config.data,'worktrees',task.id);
    mkdirSync(resolve(this.config.data,'worktrees'),{recursive:true});
    if(!existsSync(resolve(worktree,'.git'))) await this.execute(this.config.root,['worktree','add','-b',branch,worktree,baseSha],signal);
    else {
      const status=await this.execute(worktree,['status','--porcelain'],signal);
      if(!status.trim()) await this.execute(worktree,['rebase','origin/main'],signal);
      else if(task.baseSha && task.baseSha!==baseSha) throw new Error('Main changed while interrupted edits were uncommitted. Resolve the preserved worktree before resuming.');
    }
    return {worktree,branch,baseSha};
  }
  async commit(task:Task,signal:AbortSignal):Promise<string> {
    const cwd=task.worktree!;
    const files=statusPaths(await this.execute(cwd,['status','--porcelain','-z','--untracked-files=all'],signal));
    assertChangeScope(files,this.config.games[task.game]!.directory);
    await this.execute(cwd,['add','--','engine',this.config.games[task.game]!.directory,'tests'],signal);
    const changed=(await this.execute(cwd,['diff','--cached','--name-only'],signal)).trim();
    if(changed) await this.execute(cwd,['-c','user.name=YY Agent','-c','user.email=yy-agent@users.noreply.github.com','commit','-m',`Update ${task.game}: ${task.prompt.replace(/[\r\n]/g,' ').slice(0,100)}`],signal);
    // Inspect committed changes too: a provider must not bypass scope by making its own commit.
    const candidateFiles=(await this.execute(cwd,['diff','--name-only','-z',task.baseSha!,'HEAD'],signal)).split('\0').filter(Boolean);
    assertChangeScope(candidateFiles,this.config.games[task.game]!.directory);
    return (await this.execute(cwd,['rev-parse','HEAD'],signal)).trim();
  }
  async diff(task:Task,signal:AbortSignal) { return this.execute(task.worktree!,['diff',`${task.baseSha}...HEAD`],signal); }
  async push(task:Task,signal:AbortSignal) { await this.execute(task.worktree!,['push','--force-with-lease','origin',`HEAD:refs/heads/${task.branch}`],signal,true); }
  async assertClean(task:Task,signal:AbortSignal) {
    if((await this.execute(task.worktree!,['status','--porcelain'],signal)).trim()) throw new Error('Reviewer changed the worktree; refusing to merge unvalidated work.');
    if((await this.execute(task.worktree!,['rev-parse','HEAD'],signal)).trim()!==task.headSha) throw new Error('Candidate commit changed after validation');
  }
  async validate(task:Task,signal:AbortSignal) {
    const powershell=process.platform==='win32' ? 'powershell.exe' : 'pwsh';
    await run(powershell,['-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',resolve(this.config.root,'scripts/check.ps1'),'-RepoRoot',task.worktree!, '-ToolsRoot',this.config.root,'-Smoke'],{cwd:task.worktree!,signal,env:withoutSecrets(),timeout:600_000});
  }
}
