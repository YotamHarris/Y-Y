import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { randomUUID } from 'node:crypto';
import type { Config } from './config.js';
import { agentEnvironment } from './config.js';
import { run, ProcessFailure } from './process.js';
import type { Provider, AgentEvent, AgentResult, PlannedTask } from './types.js';

export const resultSchema={type:'object',additionalProperties:false,properties:{
  outcome:{type:'string',enum:['completed','needs_input']},summary:{type:'string'},question:{type:'string'},review:{type:'string',enum:['approve','request_changes','none']}
},required:['outcome','summary','question','review']};
export function parseResult(value:unknown):AgentResult {
  if(typeof value==='string') { try { value=JSON.parse(value); } catch { throw new Error('Agent did not return the required structured result'); } }
  const r=value as Partial<AgentResult> | null;
  if(!r || !['completed','needs_input'].includes(r.outcome || '') || typeof r.summary!=='string' || typeof r.question!=='string' || !['approve','request_changes','none'].includes(r.review || '')) throw new Error('Invalid structured agent result');
  return r as AgentResult;
}
export class AgentPaused extends Error {}
function progressText(text:string):string {
  try {const result=JSON.parse(text);if(typeof result.summary==='string') return result.summary+(result.question ? '\n'+result.question : '');}catch {}
  return text;
}
export const planSchema={type:'object',additionalProperties:false,properties:{
  ...resultSchema.properties,tasks:{type:'array',maxItems:8,items:{type:'object',additionalProperties:false,
    properties:{title:{type:'string'},prompt:{type:'string'},depends:{type:'array',items:{type:'integer'}}},required:['title','prompt','depends']}}
},required:[...resultSchema.required,'tasks']};
export function parsePlan(value:unknown):AgentResult & {tasks:PlannedTask[]} {
  if(typeof value==='string') value=JSON.parse(value);
  const result=parseResult(value);const tasks=(value as {tasks?:unknown}).tasks;
  if(!Array.isArray(tasks) || tasks.length>8) throw new Error('Invalid proposed tasks');
  if(result.outcome==='needs_input' && tasks.length) throw new Error('Unresolved owner choices cannot authorize proposed tasks');
  tasks.forEach((t,i)=>{if(!t || typeof t.title!=='string' || !t.title.trim() || typeof t.prompt!=='string' || !t.prompt.trim() || !Array.isArray(t.depends)
    || t.depends.some((n:unknown)=>!Number.isInteger(n) || Number(n)<0 || Number(n)>=i)) throw new Error('Invalid plan task or dependency');});
  return {...result,tasks};
}
export function normalizeEvent(provider:Provider,value:Record<string,any>):AgentEvent | undefined {
  if(provider==='codex') {
    if(value.type==='thread.started') return {type:'session',sessionId:value.thread_id};
    if(value.type==='error' || value.type==='turn.failed') return {type:'error',text:value.message || value.error?.message || 'Codex failed'};
    if(value.type==='item.completed' && value.item?.type==='agent_message') return {type:'progress',text:progressText(value.item.text || '')};
  } else {
    if(value.type==='system' && value.subtype==='init') return {type:'session',sessionId:value.session_id};
    if(value.type==='result' && value.is_error) return {type:'error',text:value.result || value.errors?.join('\n') || 'Claude failed'};
    if(value.type==='assistant') return {type:'progress',text:progressText(value.message?.content?.filter((b:any)=>b.type==='text').map((b:any)=>b.text).join('\n') || '')};
  }
  return undefined;
}
export class Providers {
  constructor(private config:Config,private executeProcess:typeof run=run) {}
  async execute(provider:Provider,options:{cwd:string;prompt:string;model?:string;readonly?:boolean;planning?:boolean;signal:AbortSignal;onEvent?:(event:AgentEvent)=>void}):Promise<AgentResult & {tasks?:PlannedTask[]}> {
    const env=agentEnvironment(provider,this.config.auth[provider]);
    const executable=this.config.executables[provider];
    try {
      const auth=await this.executeProcess(executable,provider==='codex' ? ['login','status'] : ['auth','status'],{cwd:options.cwd,env,signal:options.signal,timeout:30_000});
      if(provider==='codex') {
        const expected=this.config.auth.codex==='api' ? /API key/i : /ChatGPT/i;
        if(!expected.test(auth)) throw new AgentPaused(`Codex login does not match configured ${this.config.auth.codex} mode. Run codex login manually.`);
      } else {
        const status=JSON.parse(auth); if(status.loggedIn!==true) throw new AgentPaused('Claude is not logged in. Run claude auth login manually.');
        if(this.config.auth.claude==='subscription' && status.authMethod!=='claude.ai' && status.authMethod!=='oauth_token') throw new AgentPaused('Claude login does not match subscription mode.');
      }
    } catch(err) {
      if(err instanceof AgentPaused) throw err;
      throw new AgentPaused(`${provider} authentication preflight failed: ${err instanceof Error ? err.message : err}`);
    }
    const schemaDir=resolve(this.config.data,'schemas'); mkdirSync(schemaDir,{recursive:true});
    const contract=options.planning ? planSchema : resultSchema;
    const schema=resolve(schemaDir,`${randomUUID()}.json`); writeFileSync(schema,JSON.stringify(contract));
    const prompt=`${options.prompt}\nReturn ONLY JSON matching this schema: ${JSON.stringify(contract)}. If clarification or a tool permission is needed, return needs_input with the question; do not invent an answer. Never push, open PRs, merge, or access deployment credentials.`;
    const args=provider==='codex' ? ['exec','--json','--ignore-user-config','-c','approval_policy="never"','-c',`forced_login_method="${this.config.auth.codex==='api' ? 'api' : 'chatgpt'}"`,'--sandbox',options.readonly ? 'read-only' : 'workspace-write','--output-schema',schema,'-']
      : ['-p','--output-format','stream-json','--verbose','--permission-prompts','none','--permission-mode',options.readonly ? 'default' : 'acceptEdits','--allowedTools',options.readonly ? 'Read,Glob,Grep' : 'Read,Glob,Grep,Edit,Write,Bash(cmake *),Bash(ctest *),Bash(git diff *),Bash(git status *)','--json-schema',JSON.stringify(contract)];
    if(options.model) args.push('--model',options.model);
    let sessionId:string|undefined, final:unknown, error='';
    try {
      await this.executeProcess(executable,args,{cwd:options.cwd,env,input:prompt,signal:options.signal,timeout:this.config.timeout,onLine:line=>{
        let event:Record<string,any>; try { event=JSON.parse(line); } catch { return; }
        const normalized=normalizeEvent(provider,event);
        if(normalized?.sessionId) sessionId=normalized.sessionId;
        if(normalized?.type==='error') error=normalized.text || 'Agent failed';
        if(normalized) options.onEvent?.(normalized);
        if(provider==='codex' && event.type==='item.completed' && event.item?.type==='agent_message') final=event.item.text;
        if(provider==='claude' && event.type==='result') {
          final=event.structured_output || event.result;
          if(event.permission_denials?.length) error='Tool permission denied; refine the task or local provider configuration.';
        }
      }});
    } catch(err) {
      const text=err instanceof ProcessFailure ? err.output : String(err);
      if(/auth|login|rate.?limit|usage.?limit|quota|permission.denied/i.test(text)) throw new AgentPaused(`${provider}: authentication, usage limit, or tool permission needs attention.`);
      throw err;
    }
    if(error) throw new AgentPaused(error);
    return {...(options.planning ? parsePlan(final) : parseResult(final)),sessionId};
  }
}
