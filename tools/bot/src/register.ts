import { REST, Routes } from 'discord.js';
import { loadConfig } from './config.js';
import { commands } from './commands.js';
const config=loadConfig();
await new REST({version:'10'}).setToken(config.discordToken).put(Routes.applicationGuildCommands(config.applicationId,config.guildId),{body:commands(config)});
console.log('Registered five commands in the configured Discord server.');
