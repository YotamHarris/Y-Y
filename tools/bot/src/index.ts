import { Client, Events, GatewayIntentBits, ChannelType, MessageFlags, ActionRowBuilder, ButtonBuilder, ButtonStyle, type TextChannel } from 'discord.js';
import { resolve } from 'node:path';
import { mkdirSync, openSync, closeSync, unlinkSync, readFileSync, writeFileSync } from 'node:fs';
import { loadConfig, authorized } from './config.js';
import { Store } from './store.js';
import { Providers } from './providers.js';
import { Git } from './git.js';
import { GitHub } from './github.js';
import { Coordinator } from './coordinator.js';
import { redact } from './process.js';
import type { Kind,Provider,Task } from './types.js';
import { BoardBridge, MulticaCLI, loadBoardConfig } from './multica.js';
import { buildRequest, control, question, reply } from './conversation.js';

const config=loadConfig();mkdirSync(config.data,{recursive:true});
const lock=resolve(config.data,'service.lock');
try {const fd=openSync(lock,'wx');writeFileSync(fd,String(process.pid));closeSync(fd);} catch {
  const pid=Number(readFileSync(lock,'utf8'));
  let alive=true;try{process.kill(pid,0);}catch(err){alive=(err as NodeJS.ErrnoException).code!=='ESRCH';}
  if(alive) throw new Error(`Another bot process owns ${lock}. Do not run two coordinators.`);
  unlinkSync(lock);writeFileSync(lock,String(process.pid),{flag:'wx'});
}
const store=new Store(resolve(config.data,'tasks.sqlite'));store.recover();
const coordinator=new Coordinator(config,store,new Git(config),new GitHub(config),new Providers(config));
const boardConfig=loadBoardConfig(config);
const board=boardConfig ? new BoardBridge(config,boardConfig,store,coordinator,new MulticaCLI(boardConfig,config.root)) : undefined;
const client=new Client({intents:[GatewayIntentBits.Guilds,GatewayIntentBits.GuildMessages,GatewayIntentBits.MessageContent]});
function buttons(task?:Task) {
  if(!task) return [];
  const row=new ActionRowBuilder<ButtonBuilder>();
  if(task.kind==='plan' && task.status==='waiting_input' && task.proposal?.length && !task.approvedBy) row.addComponents(new ButtonBuilder()
    .setCustomId(`yy:approve:${task.id}:${task.proposalAt}`).setLabel('Approve plan').setStyle(ButtonStyle.Success));
  if(['failed','interrupted','waiting_input'].includes(task.status) && !(task.kind==='plan' && task.proposal?.length)) row.addComponents(new ButtonBuilder()
    .setCustomId(`yy:resume:${task.id}`).setLabel('Continue').setStyle(ButtonStyle.Primary));
  row.addComponents(new ButtonBuilder().setCustomId(`yy:status:${task.id}`).setLabel('Status').setStyle(ButtonStyle.Secondary));
  if(!['completed','ready','cancelled'].includes(task.status)) row.addComponents(new ButtonBuilder().setCustomId(`yy:cancel:${task.id}`).setLabel('Cancel').setStyle(ButtonStyle.Danger));
  return [row];
}
const safeSend=async(channelId:string,text:string,task?:Task)=>{
  const channel=await client.channels.fetch(channelId);
  if(!channel?.isSendable()) throw new Error('Task channel is unavailable');
  for(let i=0;i<text.length;i+=1900) await channel.send({content:redact(text.slice(i,i+1900)),allowedMentions:{parse:[]},components:i+1900>=text.length ? buttons(task) : []});
};
client.on(Events.InteractionCreate,async interaction=>{
  if(interaction.isButton() && interaction.customId.startsWith('yy:')) {
    if(!authorized(config,interaction.guildId,interaction.user.id)) {await interaction.reply({content:'This bot is restricted to the configured two developers.',flags:MessageFlags.Ephemeral});return;}
    await interaction.deferReply({flags:MessageFlags.Ephemeral});
    try {
      const [,action,id,version]=interaction.customId.split(':');const task=store.get(id!);
      if(!task || task.threadId!==interaction.channelId) throw new Error('This button belongs to another task thread.');
      if(!store.command(interaction.id)) {await interaction.editReply('Already received.');return;}
      await reply(store,coordinator,task,action!,interaction.id,interaction.user.id,version ? Number(version) : undefined);
      await interaction.editReply(action==='approve' ? 'Approved. The tasks are queued.' : 'Got it. See the thread for updates.');
    } catch(err) {await interaction.editReply(redact(String(err)).slice(0,1900));}return;
  }
  if(!interaction.isChatInputCommand()) return;
  if(!authorized(config,interaction.guildId,interaction.user.id)) {await interaction.reply({content:'This bot is restricted to the configured two developers.',flags:MessageFlags.Ephemeral});return;}
  await interaction.deferReply({flags:MessageFlags.Ephemeral});
  try {
    const name=interaction.commandName;
    if(name==='status' || name==='cancel') {
      const id=interaction.options.getString('task') || store.byThread(interaction.channelId)?.id;
      if(!id) {await interaction.editReply(store.list().slice(-10).map(t=>`${t.id} · ${t.game} · ${t.status}`).join('\n') || 'No tasks yet.');return;}
      let task=store.get(id);if(!task) throw new Error('Unknown task');
      if(name==='cancel') await coordinator.cancel(id);
      else if(interaction.options.getBoolean('resume')) await coordinator.resume(id);
      task=store.get(id)!;
      await interaction.editReply(`${task.id} · ${task.game} · ${task.provider} · ${task.status}\n${task.summary || ''}\n${task.prUrl || ''}\n${task.runUrl || ''}\n${task.error?.slice(-900) || ''}`.slice(0,1900));return;
    }
    if(!['ask','change','build'].includes(name)) throw new Error('Unknown command');
    const existing=store.byEvent(interaction.id);if(existing?.threadId){await interaction.editReply(`Task ${existing.id}: <#${existing.threadId}>`);return;}
    const game=interaction.options.getString('game',true);if(!config.games[game]) throw new Error('Unknown game');
    const provider=(interaction.options.getString('provider') || config.defaultProvider) as Provider;
    if(!['codex','claude'].includes(provider)) throw new Error('Unknown provider');
    const parent=await client.channels.fetch(interaction.channelId);
    const channel=parent?.isThread() ? await client.channels.fetch(parent.parentId!) : parent;
    if(channel?.type!==ChannelType.GuildText) throw new Error('Start game tasks in a server text channel');
    const task=store.create({eventId:interaction.id,kind:name as Kind,game,provider,model:interaction.options.getString('model') || undefined,userId:interaction.user.id,
      channelId:channel.id,prompt:interaction.options.getString('request') || 'Build latest main'});
    if(parent?.isThread()) {
      const source=store.byThread(parent.id);if(source) store.message(task.id,'context',store.context(source.id));
    }
    const thread=await (channel as TextChannel).threads.create({name:`${game} / ${name} / ${task.id.slice(0,8)}`,autoArchiveDuration:1440});
    store.update(task.id,{threadId:thread.id});store.message(task.id,'user',task.prompt,interaction.id);
    store.notify(task.id,`Task ${task.id}\n${name==='ask' ? 'Read-only discussion' : name==='change' ? 'Automatic checks, merge, and TestFlight upload' : 'TestFlight build'} · ${provider}\n${task.prompt}`);
    await interaction.editReply(`Queued task ${task.id}: <#${thread.id}>`);
  } catch(err) {await interaction.editReply(redact(err instanceof Error ? err.message : String(err)).slice(0,1900));}
});
client.on(Events.MessageCreate,async message=>{
  if(message.author.bot || !authorized(config,message.guildId,message.author.id) || !message.content.trim()) return;
  const current=message.channel.isThread() ? store.conversationByThread(message.channelId) : undefined;
  const knownChannels=new Set(store.list().filter(t=>t.channelId!=='multica').map(t=>t.channelId));
  const projectChannel=config.conversationChannelId ? message.channelId===config.conversationChannelId : knownChannels.size===1 && knownChannels.has(message.channelId);
  const talkChannel=message.channelId===config.talkChannelId || message.channel.type===ChannelType.GuildText && /^meatbag[\s_-]*talk$/i.test(message.channel.name);
  if(!current && !projectChannel && !talkChannel) return;
  if(!store.command(`discord:${message.id}`)) return;
  try {
    if(current && (control(message.content) || current.kind==='ask' || !buildRequest(message.content) && (current.kind==='plan' && !current.approvedBy || current.kind!=='plan' && !['completed','ready','cancelled'].includes(current.status) && !question(message.content)))) {
      await reply(store,coordinator,current,message.content,message.id,message.author.id);return;
    }
    const game=current?.game || boardConfig?.game || (Object.keys(config.games).length===1 ? Object.keys(config.games)[0] : undefined);
    if(!game) throw new Error('Choose a game with /ask or /change first; I cannot infer which project this channel is for.');
    const kind=talkChannel || question(message.content) ? 'ask' : buildRequest(message.content) ? 'build' : 'plan';
    const task=store.create({eventId:message.id,kind,game,provider:current?.provider || config.defaultProvider,userId:message.author.id,
      channelId:current?.channelId || message.channelId,prompt:message.content});
    if(current && kind==='ask' || talkChannel) store.update(task.id,{source:'discord',threadId:message.channelId,conversationParentId:current?.id || 'talk'});
    else {
      const parent=current ? await client.channels.fetch(current.channelId) : message.channel;
      if(parent?.type!==ChannelType.GuildText) throw new Error('The project channel is unavailable.');
      const thread=await (parent as TextChannel).threads.create({name:`${game} / ${kind} / ${task.id.slice(0,8)}`,autoArchiveDuration:1440});
      store.update(task.id,{source:'discord',threadId:thread.id});
      await safeSend(message.channelId,`I opened <#${thread.id}>. ${kind==='plan' ? 'We’ll refine the plan there, then approve it to start.' : 'I’ll answer there.'}`);
    }
    if(current) store.message(task.id,'context',store.context(current.id));
    if(kind==='ask') for(const previous of store.list().filter(t=>t.id!==task.id && t.kind==='ask' && t.threadId===message.channelId).slice(-3)) store.message(task.id,'context',store.context(previous.id));
    store.message(task.id,'user',message.content,message.id);
    store.notify(task.id,kind==='ask' ? 'I’m checking. This conversation is read-only.' : kind==='build' ? 'Queued a TestFlight build of current main. I’ll report upload, processing and testing readiness separately.' : 'I’m reading the code and preparing a plan. Reply naturally to refine it; Approve plan starts the tasks.');
  } catch(err) {await safeSend(message.channelId,redact(String(err)).slice(0,1900));}
});
client.on(Events.Error,err=>console.error(redact(String(err))));
let draining=false;
const flush=async()=>{
  if(draining || !client.isReady()) return;draining=true;
  try {for(const item of store.pending()) {
    let task=store.get(item.taskId);
    if(task?.parentTaskId && task.source!=='multica' && task.threadId===store.get(task.parentTaskId)?.threadId) {
      const channel=await client.channels.fetch(task.channelId);
      if(channel?.type!==ChannelType.GuildText) throw new Error('Task project channel unavailable');
      const name=`${task.game} / change / ${task.id.slice(0,8)}`;
      const active=await (channel as TextChannel).threads.fetchActive();
      const thread=active.threads.find(t=>t.name===name) || await (channel as TextChannel).threads.create({name,autoArchiveDuration:1440});
      const plan=store.get(task.parentTaskId)!;
      task=store.update(task.id,{threadId:thread.id});
      if(plan.threadId) await safeSend(plan.threadId,`Implementation task: <#${thread.id}>`);
    }
    if(!task?.threadId) {if(task?.source==='multica') store.sent(item.id);continue;}await safeSend(task.threadId,item.content,task);store.sent(item.id);
  }}
  catch(err){console.error(`Notification pending: ${redact(String(err))}`);}finally{draining=false;}
};
client.once(Events.ClientReady,()=>console.log('YYEngine bot connected.'));
const queueTimer=setInterval(()=>{if(client.isReady() || board) void coordinator.tick().catch(err=>console.error(redact(String(err))));},5000);
const conversationTimer=setInterval(()=>{if(client.isReady() || board) void coordinator.tick(true).catch(err=>console.error(redact(String(err))));},1500);
const outboxTimer=setInterval(()=>void flush(),1500);
let progressing=false;
const liveProgress=async()=>{
  if(progressing || !client.isReady()) return;progressing=true;
  try {
    for(const task of store.list().filter(t=>t.threadId)) {
      const key=`discord-live:${task.id}`,id=store.state(key);
      const running=['preparing','implementing','checking','reviewing','awaiting_checks','merging','building','uploaded','processing'].includes(task.status);
      if(!running && !id) continue;
      const channel=await client.channels.fetch(task.threadId!);if(!channel?.isSendable()) continue;
      let line=id ? await channel.messages.fetch(id).catch(()=>undefined) : undefined;
      if(!running) {if(line) await line.delete();store.setState(key,'');continue;}
      const label=task.kind==='ask' ? 'Answering' : task.kind==='plan' ? 'Planning' : task.status.replaceAll('_',' ');
      const elapsed=Math.max(0,Math.floor((Date.now()-task.updatedAt)/1000));
      const content=redact(`${label} · ${task.provider}${task.model ? ' / '+task.model : ''}\n${task.progress?.slice(-900) || task.runUrl || 'I’ll post the result here.'}\nLast update ${elapsed}s ago.`).slice(0,1900);
      if(line) await line.edit({content,allowedMentions:{parse:[]},components:buttons(task)});
      else {line=await channel.send({content,allowedMentions:{parse:[]},components:buttons(task)});store.setState(key,line.id);}
    }
  }catch(err){console.error(`Live progress pending: ${redact(String(err))}`);}finally{progressing=false;}
};
const progressTimer=setInterval(()=>void liveProgress(),15_000);
const boardTimer=board ? setInterval(()=>void board.tick().catch(err=>console.error(`Board sync pending: ${redact(String(err))}`)),10_000) : undefined;
if(board) void board.tick().catch(err=>console.error(`Board sync pending: ${redact(String(err))}`));
// Leave in-flight tasks persisted for restart recovery; do not turn a service stop into user cancellation.
let shuttingDown=false;
for(const signal of ['SIGINT','SIGTERM'] as const) process.once(signal,()=>void (async()=>{
  if(shuttingDown) return;shuttingDown=true;
  clearInterval(queueTimer);clearInterval(conversationTimer);clearInterval(outboxTimer);clearInterval(progressTimer);clearInterval(boardTimer);await coordinator.shutdown();await board?.shutdown();
  while(draining || progressing) await new Promise(resolve=>setTimeout(resolve,25));client.destroy();store.close();unlinkSync(lock);process.exit(0);
})());
await client.login(config.discordToken);
