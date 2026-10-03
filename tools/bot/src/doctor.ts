import { existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { loadConfig, agentEnvironment } from './config.js';
import { run,redact } from './process.js';
const root=resolve(process.env.YY_REPO_ROOT || fileURLToPath(new URL('../../../../',import.meta.url)));
let failed=false;
async function probe(label:string,fn:()=>Promise<unknown>) {try{await fn();console.log(`OK ${label}`);}catch(err){failed=true;console.log(`MISSING ${label}: ${redact(err instanceof Error ? err.message : String(err))}`);}}
await probe('Node 24.13+',async()=>{const [major,minor]=process.versions.node.split('.').map(Number);if(major!<24 || (major===24 && minor!<13)) throw new Error('Upgrade Node');});
await probe('Git',()=>run('git',['--version'],{cwd:root,timeout:10_000}));
await probe('Initial repository commit',()=>run('git',['rev-parse','HEAD'],{cwd:root,timeout:10_000}));
await probe('Local CMake',async()=>{if(!existsSync(resolve(root,'.tools','Scripts','cmake.exe')) && !existsSync(resolve(root,'.tools','bin','cmake'))) await run('cmake',['--version'],{cwd:root,timeout:10_000});});
for(const provider of ['codex','claude'] as const) await probe(`${provider} executable`,()=>run(process.env[`YY_${provider.toUpperCase()}_PATH`] || provider,['--version'],{cwd:root,timeout:10_000}));
await probe('Discord/GitHub configuration',async()=>{loadConfig();});
try {const config=loadConfig();for(const provider of ['codex','claude'] as const) await probe(`${provider} login (${config.auth[provider]})`,()=>run(config.executables[provider],provider==='codex' ? ['login','status'] : ['auth','status'],{cwd:root,env:agentEnvironment(provider,config.auth[provider]),timeout:30_000}));}catch{/* Configuration error already reported. */}
process.exitCode=failed ? 1 : 0;
