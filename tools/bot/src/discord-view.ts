import { ActionRowBuilder, ButtonBuilder, ButtonStyle, EmbedBuilder } from 'discord.js';
import type { Task } from './types.js';
import { taskName } from './manager-state.js';
import { redact } from './process.js';

export function decisions(task?:Task) {
  if(!task) return [];
  const rows:ActionRowBuilder<ButtonBuilder>[]=[];
  if(task.status==='waiting_input' && task.questions?.length) {
    task.questions.forEach((q,i)=>{
      if(task.answers?.[i] || !q.options.length) return;
      rows.push(new ActionRowBuilder<ButtonBuilder>().addComponents(q.options.map((option,j)=>new ButtonBuilder()
        .setCustomId(`yy:answer:${task.id}:${task.questionVersion}:${i}:${j}`).setLabel(option.slice(0,80))
        .setStyle(j===0 ? ButtonStyle.Success : ButtonStyle.Secondary))));
    });
    rows.push(new ActionRowBuilder<ButtonBuilder>().addComponents(new ButtonBuilder().setCustomId(`yy:own:${task.id}:${task.questionVersion}`)
      .setLabel('Answer in my own words').setStyle(ButtonStyle.Primary)));return rows;
  }
  if(task.kind==='plan' && task.status==='waiting_input' && task.proposal?.length && !task.approvedBy) {
    rows.push(new ActionRowBuilder<ButtonBuilder>().addComponents(new ButtonBuilder().setCustomId(`yy:approve:${task.id}:${task.proposalAt}`)
      .setLabel('Approve plan').setStyle(ButtonStyle.Success)));
  } else if(task.acceptance==='pending' && task.reviewSha && task.status!=='cancelled') {
    rows.push(new ActionRowBuilder<ButtonBuilder>().addComponents(new ButtonBuilder().setCustomId(`yy:accept:${task.id}:${task.reviewSha}`)
      .setLabel('Accept').setStyle(ButtonStyle.Success),new ButtonBuilder().setCustomId(`yy:changes:${task.id}:${task.reviewSha}`)
      .setLabel('Request changes').setStyle(ButtonStyle.Primary)));
  } else if(['failed','interrupted'].includes(task.status) || task.status==='waiting_input' && !task.question) {
    rows.push(new ActionRowBuilder<ButtonBuilder>().addComponents(new ButtonBuilder().setCustomId(`yy:resume:${task.id}`)
      .setLabel('Continue').setStyle(ButtonStyle.Primary)));
  }
  return rows;
}
export function taskReport(task:Task,text:string) {
  return redact(`What we are fixing\n${task.prompt}\n\nWhere it stands\n${task.status}${task.acceptance ? ' · owner review '+task.acceptance : ''}\n\n${text}\n\n${task.error || ''}\n\nProvider: ${task.provider}${task.model ? ' / '+task.model : ''}\nRuns: ${task.runCount || 0}\nTime: ${Math.round((task.totalRunMs || 0)/1000)}s\n${task.usage ? JSON.stringify(task.usage) : 'Usage not reported.'}\nPublished commit: ${task.mergeSha || 'not published'}\n${task.prUrl || ''}\n${task.runUrl || ''}`);
}
export interface DeliveryMessage {id:string;authorId:string;createdAt:number;footers:string[]}
export interface DeliveryChannel {
  history(before?:string):Promise<DeliveryMessage[]>;
  send(payload:any):Promise<{id:string}>;
  clear(id:string):Promise<void>;
}
export async function deliverUpdate(options:{channel:DeliveryChannel;task:Task;text:string;receipt:string;retry:boolean;botId:string;actionable:boolean;previousDecision?:string}) {
  const {channel,task,text,receipt}=options;
  const chunks=task.kind==='change' || task.kind==='build' ? [text.slice(0,2800)] : text.match(/[\s\S]{1,3500}/g) || [''];
  const found=new Map<string,string>();
  if(options.retry) {
    let before:string|undefined;
    for(;;) {
      const messages=await channel.history(before);if(!messages.length) break;
      for(const message of messages) if(message.authorId===options.botId) for(const footer of message.footers) if(footer.startsWith(receipt+':')) found.set(footer,message.id);
      if(messages.length<100 || messages.at(-1)!.createdAt<task.createdAt) break;
      before=messages.at(-1)!.id;
    }
  }
  let lastId='';
  const controls=options.actionable ? decisions(task) : [];
  for(const [i,chunk] of chunks.entries()) {
    const key=`${receipt}:${i}`;
    if(found.has(key)) {lastId=found.get(key)!;continue;}
    const embed=new EmbedBuilder().setDescription(redact(chunk) || 'Update').setFooter({text:key});
    if(task.kind==='change' || task.kind==='build') {
      embed.setTitle(taskName(task));
      const standing=task.acceptance==='pending' ? 'Published; waiting for your acceptance. Delivery continues separately.' : task.status.replaceAll('_',' ');
      const call=task.questions?.map(q=>q.question).join('\n') || task.question || (task.acceptance==='pending' ? 'Try the result. Accept, or Request changes.' : 'Nothing for now.');
      embed.addFields({name:'Where it stands',value:standing.slice(0,1000)},{name:'Your call',value:redact(call).slice(0,1000)});
      const links=[task.prUrl ? `[Change](${task.prUrl})` : '',task.runUrl ? `[Build](${task.runUrl})` : ''].filter(Boolean).join(' · ');
      if(links) embed.addFields({name:'More',value:links});
    } else if(task.questions?.length) embed.addFields({name:'Your call',value:task.questions.map((q,i)=>`${i+1}. ${q.question}`).join('\n').slice(0,1000)});
    const last=i===chunks.length-1;
    const report=last && (task.kind==='change' || task.kind==='build' || text.length>3500) ? [{attachment:Buffer.from(taskReport(task,text),'utf8'),name:`${task.game}-report.txt`}] : [];
    const sent=await channel.send({embeds:[embed.toJSON()],components:last ? controls : [],files:report,allowedMentions:{parse:[]}});lastId=sent.id;
  }
  if(options.previousDecision && options.previousDecision!==lastId) await channel.clear(options.previousDecision);
  return {lastId,decisionId:controls.length ? lastId : undefined};
}
