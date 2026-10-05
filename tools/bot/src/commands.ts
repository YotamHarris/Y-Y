import { SlashCommandBuilder } from 'discord.js';
import type { Config } from './config.js';

export function commands(_config:Config) {
  // Everyday work begins with prose; only global manager controls are commands.
  return [
    ['status','Show working tasks, decisions and what the queue waits on'],
    ['models','Show worker and quick-chat providers'],
    ['pause','Let active work finish; start no new workers'],
    ['resume','Resume approved work after pausing or stopping'],
    ['stop','Stop managed local runs and preserve their work']
  ].map(([name,description])=>new SlashCommandBuilder().setName(name!).setDescription(description!).toJSON());
}
