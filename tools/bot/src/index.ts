import { Client, Events, GatewayIntentBits, ChannelType, MessageFlags, type TextChannel } from 'discord.js';
import { resolve } from 'node:path';
import { mkdirSync, openSync, closeSync, unlinkSync, readFileSync, writeFileSync } from 'node:fs';
import { loadConfig, authorized } from './config.js';
import { Store } from './store.js';
import { Providers } from './providers.js';
import { Git } from './git.js';
import { GitHub } from './github.js';
import { Coordinator } from './coordinator.js';
import { redact } from './process.js';
import type { Kind,Provider } from './types.js';
import { BoardBridge, MulticaCLI, loadBoardConfig } from './multica.js';

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
  for(let i=0;i<text.length;i+=1900) await channel.send({content:redact(text.slice(i,i+1900)),allowedMentions:{parse:[]}});
};
client.on(Events.InteractionCreate,async interaction=>{
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
  if(message.author.bot || !authorized(config,message.guildId,message.author.id) || !message.channel.isThread()) return;
  const task=store.byThread(message.channelId);if(!task) return;
  if(!store.message(task.id,'user',message.content,message.id)) return;
  if(task.kind==='ask' && ['completed','waiting_input'].includes(task.status)) store.update(task.id,{status:'queued',prompt:message.content});
  else if(task.status==='waiting_input') {
    await coordinator.resume(task.id);store.notify(task.id,'Answer received; resuming the explicitly requested task.');
  }
});
client.on(Events.Error,err=>console.error(redact(String(err))));
let draining=false;
const flush=async()=>{
  if(draining || !client.isReady()) return;draining=true;
  try {for(const item of store.pending()) {const task=store.get(item.taskId);if(!task?.threadId) {if(task?.source==='multica') store.sent(item.id);continue;}await safeSend(task.threadId,item.content);store.sent(item.id);}}
  catch(err){console.error(`Notification pending: ${redact(String(err))}`);}finally{draining=false;}
};
client.once(Events.ClientReady,()=>console.log('YYEngine bot connected.'));
const queueTimer=setInterval(()=>{if(client.isReady() || board) void coordinator.tick().catch(err=>console.error(redact(String(err))));},5000);
const outboxTimer=setInterval(()=>void flush(),1500);
const boardTimer=board ? setInterval(()=>void board.tick().catch(err=>console.error(`Board sync pending: ${redact(String(err))}`)),10_000) : undefined;
if(board) void board.tick().catch(err=>console.error(`Board sync pending: ${redact(String(err))}`));
// Leave in-flight tasks persisted for restart recovery; do not turn a service stop into user cancellation.
let shuttingDown=false;
for(const signal of ['SIGINT','SIGTERM'] as const) process.once(signal,()=>void (async()=>{
  if(shuttingDown) return;shuttingDown=true;
  clearInterval(queueTimer);clearInterval(outboxTimer);clearInterval(boardTimer);await coordinator.shutdown();await board?.shutdown();client.destroy();store.close();unlinkSync(lock);process.exit(0);
})());
await client.login(config.discordToken);
