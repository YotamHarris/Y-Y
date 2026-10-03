import { SlashCommandBuilder } from 'discord.js';
import type { Config } from './config.js';

export function commands(config:Config) {
  const choices=Object.keys(config.games).map(id=>({name:id,value:id}));
  if(choices.length>25) throw new Error('Discord supports at most 25 game choices');
  const task=(name:string,description:string)=>new SlashCommandBuilder().setName(name).setDescription(description)
    .addStringOption(o=>o.setName('game').setDescription('Game project').setRequired(true).addChoices(...choices))
    .addStringOption(o=>o.setName('request').setDescription('Describe the question or change').setRequired(true).setMaxLength(4000))
    .addStringOption(o=>o.setName('provider').setDescription('Agent provider').addChoices({name:'Codex',value:'codex'},{name:'Claude',value:'claude'}))
    .addStringOption(o=>o.setName('model').setDescription('Optional provider model').setMaxLength(100));
  return [task('ask','Discuss a game idea without changing code'),task('change','Implement, check, merge, and distribute a game change'),
    new SlashCommandBuilder().setName('build').setDescription('Build and distribute a game at the latest main commit')
      .addStringOption(o=>o.setName('game').setDescription('Game project').setRequired(true).addChoices(...choices)),
    new SlashCommandBuilder().setName('status').setDescription('Show task status or resume an interrupted task')
      .addStringOption(o=>o.setName('task').setDescription('Task ID; otherwise use this thread'))
      .addBooleanOption(o=>o.setName('resume').setDescription('Resume paused or failed work')),
    new SlashCommandBuilder().setName('cancel').setDescription('Stop a task; preserve any work already completed')
      .addStringOption(o=>o.setName('task').setDescription('Task ID; otherwise use this thread'))].map(c=>c.toJSON());
}
