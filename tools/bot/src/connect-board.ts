import { resolve } from 'node:path';
import { mkdirSync, writeFileSync, existsSync, readFileSync } from 'node:fs';
import { loadConfig } from './config.js';
import { MulticaCLI, type BoardConfig } from './multica.js';
import { run, withoutSecrets } from './process.js';
import { managerGuide } from './conversation.js';

const config=loadConfig();
const executable=resolve(config.data,'multica/bin/multica.exe');
async function cli(args:string[]):Promise<any> {
  return JSON.parse(await run(executable,['--profile','yyengine',...args,'--output','json'],{cwd:config.root,env:withoutSecrets(),timeout:30_000,stdoutOnly:true}));
}
const projects=await cli(['project','list']);
let project=projects.find((p:any)=>p.title==='TapDemo');
if(!project) project=await cli(['project','create','--title','TapDemo','--icon','🎯','--repo',`https://github.com/${config.repository}`]);
await cli(['project','update',project.id,'--status','in_progress','--description',
  project.description || 'TapDemo is our C++20/SDL3 tap game: moving targets, scoring, 30-second rounds, sounds and tap-to-restart on Windows/iOS. The YYEngine coordinator mirrors Discord tasks here. Describe goals or ask questions in ordinary comments on the manager guide card; plans are approved once before implementation. Task cards hold the latest validation and delivery evidence.']);
const members=await cli(['workspace','member','list']);
const email=process.env.YY_MULTICA_OWNER_EMAIL || 'yotam.harris@gmail.com';
const member=members.find((m:any)=>m.email===email);
if(!member) throw new Error(`Invite/sign in ${email} before connecting the board`);
const userId=process.env.YY_MULTICA_OWNER_DISCORD_ID || [...config.users][0]!;
if(!config.users.has(userId)) throw new Error('The board owner must map to an existing authorized Discord user');
const path=resolve(config.data,'multica/board.json');
const previous=existsSync(path) ? JSON.parse(readFileSync(path,'utf8')) as BoardConfig : undefined;
const board:BoardConfig={executable,profile:'yyengine',projectId:project.id,game:'tapdemo',owners:{...previous?.owners,[member.user_id || member.id]:userId}};
const api=new MulticaCLI(board,config.root);
const issues=await api.issues();
const cards=[
  {title:'YYEngine manager — how to request work',status:'done',body:
    managerGuide},
  {title:'Repair signing and verify the first TestFlight build',status:'blocked',body:
    'Latest signed build failed at Configure signing before upload: https://github.com/YotamHarris/Y-Y/actions/runs/37199715524 . Check the failed step and repository docs/setup.md; correct certificate/profile/Apple environment prerequisites. Resume the existing mirrored build card with /yy resume after the fix so the bot reruns failed jobs in the same workflow. This acceptance item itself is tracking information and does not start a build. Done requires upload, Apple processing, internal tester assignment and readiness all verified.'},
  {title:'Verify a live game change through Discord and the board',status:'todo',body:
    'A live Discord build request reached GitHub, but the complete game-change path remains unverified. Once signing works, request a small scoring change through /change in Discord, or /yy change on a new TapDemo card. Verify isolated worktree, local checks, independent review, PR CI, merge and board updates, then TestFlight delivery. Do not start until explicitly requested.'},
  {title:'Install TapDemo on an iPhone and record device acceptance',status:'backlog',body:
    'Depends on the first verified signed TestFlight build. Install with the internal tester invitation. Record touch alignment, safe areas, pause/resume, audio, restart, 60 FPS behavior, memory, battery/thermal behavior and app size on an actual iPhone. Desktop and simulator evidence cannot establish device performance. Human/device acceptance; no automatic task is started by this card.'}
];
for(const card of cards) {
  let issue=issues.find(i=>i.title===card.title);
  if(!issue) {issue=await api.create(card.title,card.body);await api.update(issue.id,card.status);issues.push(issue);}
  if(card.title.startsWith('YYEngine manager')) {
    board.managerIssueId=issue.id;
    await run(executable,['--profile',board.profile,'issue','update',issue.id,'--description-stdin','--no-start','--output','json'],
      {cwd:config.root,env:withoutSecrets(),input:managerGuide,timeout:30_000,stdoutOnly:true});
  }
}
mkdirSync(resolve(config.data,'multica'),{recursive:true});
writeFileSync(path,JSON.stringify(board,null,2));
console.log(`TapDemo project connected: http://localhost:3072/yyengine/projects/${project.id}`);
console.log(`Board owner: ${email}. Restart the bot once to load this connection.`);
