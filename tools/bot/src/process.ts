import { spawn, type ChildProcess } from 'node:child_process';

export class ProcessFailure extends Error { constructor(message:string,public output:string,public code:number|null) { super(message); } }
export function withoutSecrets(source:NodeJS.ProcessEnv=process.env):NodeJS.ProcessEnv {
  return Object.fromEntries(Object.entries(source).filter(([key])=>!/TOKEN|SECRET|PASSWORD|API_KEY/i.test(key)));
}
export function redact(text:string,env:NodeJS.ProcessEnv=process.env) {
  for(const [key,value] of Object.entries(env)) if(value && value.length>=8 && /TOKEN|SECRET|PASSWORD|API_KEY/.test(key)) text=text.split(value).join('[REDACTED]');
  return text.replace(/(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{16,})/g,'[REDACTED]');
}
function terminate(child:ChildProcess) {
  if(!child.pid) return;
  if(process.platform==='win32') spawn('taskkill',['/PID',String(child.pid),'/T','/F'],{windowsHide:true,stdio:'ignore'}).on('error',()=>child.kill());
  else { try { process.kill(-child.pid,'SIGTERM'); } catch { child.kill('SIGTERM'); } }
}
export interface RunOptions {cwd:string;env?:NodeJS.ProcessEnv;input?:string;signal?:AbortSignal;timeout?:number;onLine?:(line:string)=>void}
export function run(executable:string,args:string[],options:RunOptions):Promise<string> {
  if(/\.(cmd|bat)$/i.test(executable)) throw new Error('Configure a native .exe CLI path, not a shell wrapper');
  return new Promise((resolve,reject)=>{
    if(options.signal?.aborted) return reject(new Error('Cancelled'));
    const child=spawn(executable,args,{cwd:options.cwd,env:options.env || process.env,windowsHide:true,shell:false,detached:process.platform!=='win32',stdio:['pipe','pipe','pipe']});
    let output='',pending='',reason='';
    const stop=(why:string)=>{reason=why;terminate(child);};
    const timer=setTimeout(()=>stop('Process timed out'),options.timeout || 1_800_000);
    const abort=()=>stop('Cancelled'); options.signal?.addEventListener('abort',abort,{once:true});
    const consume=(chunk:Buffer)=>{
      const text=chunk.toString(); output=(output+text).slice(-2_000_000); pending+=text;
      if(pending.length>2_000_000) {stop('Process output line exceeded limit');pending='';return;}
      const lines=pending.split(/\r?\n/); pending=lines.pop() || '';
      for(const line of lines) { try { options.onLine?.(redact(line)); } catch { stop('Invalid process output'); } }
    };
    child.stdout.on('data',consume); child.stderr.on('data',(chunk:Buffer)=>{output=(output+chunk.toString()).slice(-2_000_000);});
    const cleanup=()=>{clearTimeout(timer);options.signal?.removeEventListener('abort',abort);};
    child.on('error',err=>{cleanup();reject(err);});
    child.on('close',code=>{
      cleanup(); if(pending) {try{options.onLine?.(redact(pending));}catch{reason='Invalid process output';}}
      if(reason || code!==0) reject(new ProcessFailure(reason || `${executable} exited with code ${code}`,redact(output),code)); else resolve(redact(output));
    });
    child.stdin.on('error',()=>{}); child.stdin.end(options.input || '');
  });
}
