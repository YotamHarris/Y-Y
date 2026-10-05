import { Client, Events, GatewayIntentBits, ChannelType, MessageFlags, ActionRowBuilder, ModalBuilder, TextInputBuilder, TextInputStyle, type TextChannel, type Message } from 'discord.js';
import { resolve } from 'node:path';
import { mkdirSync, openSync, closeSync, unlinkSync, readFileSync, writeFileSync } from 'node:fs';
import { loadConfig, authorized } from './config.js';
import { Store } from './store.js';
import { Providers } from './providers.js';
import { Git } from './git.js';
import { GitHub } from './github.js';
import { Coordinator } from './coordinator.js';
import { redact } from './process.js';
import type { Task } from './types.js';
import { BoardBridge, MulticaCLI, loadBoardConfig } from './multica.js';
import { answer, buildRequest, hearQuick, reply } from './conversation.js';
import { managerAction, managerOutlook, taskName } from './manager-state.js';
import { decisions, deliverUpdate, type DeliveryChannel } from './discord-view.js';

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
const safeSend=async(channelId:string,text:string)=>{
  const channel=await client.channels.fetch(channelId);
  if(!channel?.isSendable()) throw new Error('Task channel is unavailable');
  if(channel.isThread() && channel.archived) await channel.setArchived(false);
  for(let i=0;i<text.length;i+=1900) await channel.send({content:redact(text.slice(i,i+1900)),allowedMentions:{parse:[]}});
};
client.on(Events.InteractionCreate,async interaction=>{
  if(interaction.isModalSubmit() && interaction.customId.startsWith('yy:')) {
    if(!authorized(config,interaction.guildId,interaction.user.id)) {await interaction.reply({content:'This control is restricted to the configured developers.',flags:MessageFlags.Ephemeral});return;}
    await interaction.deferReply({flags:MessageFlags.Ephemeral});
    try {
      const [,action,id,version]=interaction.customId.split(':');const task=store.get(id!);
      if(!task || task.threadId!==interaction.channelId) throw new Error('This form belongs to another task.');
      if(!store.command(interaction.id)) {await interaction.editReply('Already received.');return;}
      if(action==='revise') await coordinator.requestChanges(task.id,interaction.fields.getTextInputValue('feedback'),version);
      else if(action==='answers') {
        const values=Object.fromEntries((task.questions || []).map((_q,i)=>[i,interaction.fields.getTextInputValue(String(i))]));
        await answer(store,coordinator,task,Number(version),values,interaction.id,interaction.user.id);
      } else throw new Error('Unknown form');
      if(interaction.isFromMessage()) await interaction.message.edit({components:decisions(store.get(task.id))});
      await interaction.editReply('Got it. I’ll continue in this thread.');
    } catch(err) {await interaction.editReply(redact(String(err)).slice(0,1900));}return;
  }
  if(interaction.isButton() && interaction.customId.startsWith('yy:')) {
    if(!authorized(config,interaction.guildId,interaction.user.id)) {await interaction.reply({content:'This bot is restricted to the configured two developers.',flags:MessageFlags.Ephemeral});return;}
    try {
      const [,action,id,version,qIndex,optionIndex]=interaction.customId.split(':');const task=store.get(id!);
      if(!task || task.threadId!==interaction.channelId) throw new Error('This button belongs to another task thread.');
      if(action==='own' || action==='changes') {
        if(action==='changes' && (!task.reviewSha || task.reviewSha!==version)) throw new Error('This review was replaced. Use the latest result.');
        if(action==='own' && (task.status!=='waiting_input' || task.questionVersion!==Number(version) || !task.questions?.length)) throw new Error('These questions were answered or replaced.');
        const modal=new ModalBuilder().setCustomId(`yy:${action==='own' ? 'answers' : 'revise'}:${task.id}:${version}`).setTitle(action==='own' ? 'Answer in your own words' : 'Request changes');
        if(action==='changes') modal.addComponents(new ActionRowBuilder<TextInputBuilder>().addComponents(new TextInputBuilder().setCustomId('feedback').setLabel('What should change?').setStyle(TextInputStyle.Paragraph).setRequired(true).setMaxLength(4000)));
        else for(const [i,q] of task.questions!.entries()) modal.addComponents(new ActionRowBuilder<TextInputBuilder>().addComponents(new TextInputBuilder().setCustomId(String(i)).setLabel(`${i+1}. ${q.question}`.slice(0,45)).setStyle(TextInputStyle.Paragraph).setRequired(false).setMaxLength(1500).setValue(task.answers?.[i] || '')));
        await interaction.showModal(modal);return;
      }
      await interaction.deferReply({flags:MessageFlags.Ephemeral});
      if(!store.command(interaction.id)) {await interaction.editReply('Already received.');return;}
      if(action==='accept') store.accept(task.id,version!);
      else if(action==='answer') {
        const option=task.questions?.[Number(qIndex)]?.options[Number(optionIndex)];if(!option) throw new Error('Unknown answer');
        await answer(store,coordinator,task,Number(version),{[Number(qIndex)]:option},interaction.id,interaction.user.id);
      } else if(['approve','resume','status','cancel'].includes(action!)) await reply(store,coordinator,task,action!,interaction.id,interaction.user.id,version ? Number(version) : undefined);
      else throw new Error('Unknown control');
      await interaction.message.edit({components:decisions(store.get(task.id))});
      await interaction.editReply(action==='approve' ? 'Approved. The tasks are queued.' : 'Got it. See the thread for updates.');
    } catch(err) {
      const error={content:redact(String(err)).slice(0,1900),flags:MessageFlags.Ephemeral};
      if(interaction.deferred || interaction.replied) await interaction.editReply(error.content);else await interaction.reply({content:error.content,flags:MessageFlags.Ephemeral});
    }return;
  }
  if(!interaction.isChatInputCommand()) return;
  if(!authorized(config,interaction.guildId,interaction.user.id)) {await interaction.reply({content:'This bot is restricted to the configured two developers.',flags:MessageFlags.Ephemeral});return;}
  await interaction.deferReply({flags:MessageFlags.Ephemeral});
  try {
    const name=interaction.commandName;
    if(['status','pause','resume','stop','models'].includes(name)) {
      if(name==='models') {await interaction.editReply(`Workers: ${config.defaultProvider} · ${config.workerSlots || 2} isolated workspaces\nQuick questions: ${config.quickProvider || config.defaultProvider} / ${config.quickModel || 'CLI default'}\nClaude conversations resume their saved session; Codex receives the saved transcript. Publication and independent review stay with the coordinator.`);return;}
      if(name!=='status') await coordinator.setMode(name==='pause' ? 'paused' : name==='stop' ? 'stopped' : 'running');
      await interaction.editReply(managerOutlook(store).slice(0,1900));return;
    }
    throw new Error('Describe a goal in the project channel, or ask in meatbag-talk.');
  } catch(err) {await interaction.editReply(redact(err instanceof Error ? err.message : String(err)).slice(0,1900));}
});
const handleMessage=async(message:Message)=>{
  if(message.author.bot || !authorized(config,message.guildId,message.author.id) || !message.content.trim()) return;
  const current=message.channel.isThread() ? store.conversationByThread(message.channelId) : undefined;
  const knownChannels=new Set(store.list().filter(t=>t.channelId!=='multica').map(t=>t.channelId));
  const projectChannel=config.conversationChannelId ? message.channelId===config.conversationChannelId : knownChannels.size===1 && knownChannels.has(message.channelId);
  const talkChannel=message.channelId===(config.talkChannelId || store.state('discord-talk-channel')) || message.channel.type===ChannelType.GuildText && /^meatbag[\s_-]*talk$/i.test(message.channel.name);
  if(!current && !projectChannel && !talkChannel) return;
  if(!store.command(`discord:${message.id}`)) return;
  const cursorKey=`discord-cursor:${message.channelId}`,cursor=store.state(cursorKey);
  if(!cursor || BigInt(message.id)>BigInt(cursor)) store.setState(cursorKey,message.id);
  try {
    const game=current?.game || boardConfig?.game || (Object.keys(config.games).length===1 ? Object.keys(config.games)[0] : undefined);
    if(!game) throw new Error('This channel is not connected to a game project. Configure its game before requesting work.');
    if(talkChannel) {
      hearQuick(store,config,{key:`discord:${message.channelId}`,text:message.content,eventId:message.id,userId:message.author.id,game,
        channelId:message.channelId,threadId:message.channelId});return;
    }
    const global=!current ? managerAction(message.content) : undefined;
    if(global) {if(global!=='status') await coordinator.setMode(global==='pause' ? 'paused' : global==='stop' ? 'stopped' : 'running');await safeSend(message.channelId,managerOutlook(store));return;}
    if(current) {
      store.setState(`discord-archived:${current.id}`,'');
      await reply(store,coordinator,current,message.content,message.id,message.author.id);return;
    }
    const kind=buildRequest(message.content) ? 'build' : 'plan';
    const task=store.create({eventId:message.id,kind,game,provider:config.defaultProvider,userId:message.author.id,
      channelId:message.channelId,prompt:message.content});
    store.update(task.id,{source:'discord'});
    store.message(task.id,'user',message.content,message.id);
    store.notify(task.id,kind==='build' ? 'Queued a TestFlight build of current main. Upload, Apple processing and testing readiness will be reported separately.' : 'Let’s plan this here. I’ll read the code, talk through the choices, and propose whole tasks. Approve plan starts them; this conversation stays open afterwards.');
    if(message.channel.type===ChannelType.GuildText) {
      const thread=await message.startThread({name:`G${task.number} | ${message.content.replace(/\s+/g,' ').slice(0,80)}`,autoArchiveDuration:1440});
      store.update(task.id,{source:'discord',threadId:thread.id});
    }
  } catch(err) {await safeSend(message.channelId,redact(String(err)).slice(0,1900));}
};
client.on(Events.MessageCreate,message=>void handleMessage(message).catch(err=>console.error(redact(String(err)))));
client.on(Events.Error,err=>console.error(redact(String(err))));
let draining=false;
async function ensureThread(task:Task):Promise<Task> {
  if(task.source==='multica' || task.quickKey) return task;
  if(task.threadId && (!task.parentTaskId || task.threadId!==store.get(task.parentTaskId)?.threadId)) return task;
  const channel=await client.channels.fetch(task.channelId);if(channel?.type!==ChannelType.GuildText) throw new Error('Project channel unavailable');
  const prefix=task.parentTaskId ? `T${task.number || task.id.slice(0,8)} | ` : `G${task.number || task.id.slice(0,8)} | `;
  const name=(prefix+(task.goalName ? task.goalName+': ' : '')+taskName(task)).slice(0,100);
  const active=await (channel as TextChannel).threads.fetchActive();
  let thread=active.threads.find(t=>t.name.startsWith(prefix));
  if(!thread && !task.parentTaskId && /^\d+$/.test(task.eventId)) {
    const original=await channel.messages.fetch(task.eventId).catch(()=>undefined);
    thread=original?.thread || (original ? await original.startThread({name,autoArchiveDuration:1440}) : undefined);
  }
  thread ||= await (channel as TextChannel).threads.create({name,autoArchiveDuration:1440});
  const updated=store.update(task.id,{threadId:thread.id});
  const parent=task.parentTaskId ? store.get(task.parentTaskId) : undefined;
  if(parent?.threadId) await safeSend(parent.threadId,`Task ${task.number || ''}: <#${thread.id}>`);
  return updated;
}
const flush=async()=>{
  if(draining || !client.isReady()) return;draining=true;
  try {const pending=store.pending();for(const item of pending) {
    let task=store.get(item.taskId);
    if(!task) continue;
    if(task.source!=='multica') task=await ensureThread(task);
    if(!task.threadId) {if(task.source==='multica') store.sent(item.id);continue;}
    const channel=await client.channels.fetch(task.threadId);if(!channel?.isSendable()) throw new Error('Task channel unavailable');
    if(channel.isThread() && channel.archived) await channel.setArchived(false);
    if(task.kind==='plan' && task.goalName && channel.isThread()) {const name=`G${task.number || task.id.slice(0,8)} | ${task.goalName}`.slice(0,100);if(channel.name!==name) await channel.setName(name);}
    const adapter:DeliveryChannel={
      history:async(before)=>[...(await channel.messages.fetch({limit:100,before})).values()].map(m=>({id:m.id,authorId:m.author.id,createdAt:m.createdTimestamp,footers:m.embeds.map(e=>e.footer?.text || '')})),
      send:payload=>channel.send(payload),clear:async id=>{const message=await channel.messages.fetch(id).catch(()=>undefined);if(message) await message.edit({components:[]});}
    };
    store.deliveryAttempt(item.id);
    const actionable=pending.filter(p=>p.taskId===task!.id).at(-1)?.id===item.id;
    const result=await deliverUpdate({channel:adapter,task,text:item.content,receipt:`YY event ${item.id}`,retry:item.attempts>0,botId:client.user!.id,actionable,
      previousDecision:actionable ? store.state(`discord-decision:${task.id}`) : undefined});
    if(actionable) store.setState(`discord-decision:${task.id}`,result.decisionId || '');
    store.sent(item.id);
  }
  for(const task of store.list().filter(t=>t.threadId && t.kind==='change' && (t.status==='cancelled' || t.acceptance==='accepted' && ['ready','completed'].includes(t.status)))) {
    if(store.state(`discord-archived:${task.id}`)) continue;
    const channel=await client.channels.fetch(task.threadId!);if(channel?.isThread() && !channel.archived) await channel.setArchived(true);
    store.setState(`discord-archived:${task.id}`,'yes');
  }}
  catch(err){console.error(`Notification pending: ${redact(String(err))}`);}finally{draining=false;}
};
async function reconcileMessages(channelId:string) {
  const channel=await client.channels.fetch(channelId);if(!channel?.isSendable()) return;
  const enabled=Number(store.state('discord-conversation-enabled'));
  const after=BigInt(store.state(`discord-cursor:${channelId}`) || ((BigInt(enabled)-1420070400000n)<<22n).toString());
  const messages:Message[]=[];let before:string|undefined;
  for(;;) {
    const page=[...(await channel.messages.fetch({limit:100,before})).values()];if(!page.length) break;
    messages.push(...page.filter(m=>BigInt(m.id)>after));
    if(page.length<100 || BigInt(page.at(-1)!.id)<=after) break;before=page.at(-1)!.id;
  }
  for(const message of messages.sort((a,b)=>a.createdTimestamp-b.createdTimestamp)) await handleMessage(message);
}
if(!store.state('discord-conversation-enabled')) store.setState('discord-conversation-enabled',String(Date.now()));
client.once(Events.ClientReady,()=>void (async()=>{
  console.log('YYEngine bot connected.');
  const guild=await client.guilds.fetch(config.guildId),channels=await guild.channels.fetch();
  const talk=channels.find(c=>c?.type===ChannelType.GuildText && /^meatbag[\s_-]*talk$/i.test(c.name));
  if(talk && !config.talkChannelId) store.setState('discord-talk-channel',talk.id);
  const ids=new Set(store.list().flatMap(t=>[t.threadId,t.channelId]).filter((id):id is string=>!!id && id!=='multica'));
  if(config.conversationChannelId) ids.add(config.conversationChannelId);
  if(config.talkChannelId || talk) ids.add(config.talkChannelId || talk!.id);
  for(const id of ids) await reconcileMessages(id).catch(err=>console.error(`History pending: ${redact(String(err))}`));
})().catch(err=>console.error(redact(String(err)))));
const queueTimer=setInterval(()=>{if(client.isReady() || board) void coordinator.tick().catch(err=>console.error(redact(String(err))));},5000);
const conversationTimer=setInterval(()=>{if(client.isReady() || board) void coordinator.tick(true).catch(err=>console.error(redact(String(err))));},1500);
const remoteTimer=setInterval(()=>{if(client.isReady() || board) void coordinator.tickRemote().catch(err=>console.error(redact(String(err))));},5000);
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
      const elapsed=Math.max(0,Math.floor((Date.now()-(task.runStartedAt || task.updatedAt))/1000));
      const content=redact(`${label} · ${task.provider}${task.model ? ' / '+task.model : ''} · ${elapsed}s${task.runCount ? ' · run '+task.runCount : ''}\n${task.progress?.slice(-750) || task.runUrl || 'I’ll post the result here.'}\n${task.lastStep || ''}`).slice(0,1900);
      if(line) await line.edit({content,allowedMentions:{parse:[]},components:[]});
      else {line=await channel.send({content,allowedMentions:{parse:[]},components:[]});store.setState(key,line.id);}
    }
  }catch(err){console.error(`Live progress pending: ${redact(String(err))}`);}finally{progressing=false;}
};
const progressTimer=setInterval(()=>void liveProgress(),6000);
const boardTimer=board ? setInterval(()=>void board.tick().catch(err=>console.error(`Board sync pending: ${redact(String(err))}`)),10_000) : undefined;
if(board) void board.tick().catch(err=>console.error(`Board sync pending: ${redact(String(err))}`));
// Leave in-flight tasks persisted for restart recovery; do not turn a service stop into user cancellation.
let shuttingDown=false;
for(const signal of ['SIGINT','SIGTERM'] as const) process.once(signal,()=>void (async()=>{
  if(shuttingDown) return;shuttingDown=true;
  clearInterval(queueTimer);clearInterval(conversationTimer);clearInterval(remoteTimer);clearInterval(outboxTimer);clearInterval(progressTimer);clearInterval(boardTimer);await coordinator.shutdown();await board?.shutdown();
  while(draining || progressing) await new Promise(resolve=>setTimeout(resolve,25));client.destroy();store.close();unlinkSync(lock);process.exit(0);
})());
await client.login(config.discordToken);
