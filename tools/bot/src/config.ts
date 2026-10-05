import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';
import type { GameConfig, Provider } from './types.js';

export interface Config {
  root: string; data: string; guildId: string; applicationId: string; users: Set<string>;
  discordToken: string; githubToken: string; repository: string; defaultProvider: Provider;
  auth: Record<Provider,'subscription' | 'api'>; executables: Record<Provider,string>;
  timeout: number; games: Record<string,GameConfig>;
  conversationChannelId?:string; talkChannelId?:string;
}
export function loadConfig(env: NodeJS.ProcessEnv=process.env): Config {
  const root=resolve(env.YY_REPO_ROOT || fileURLToPath(new URL('../../../../',import.meta.url)));
  const required=(key:string) => { const value=env[key]?.trim(); if(!value) throw new Error(`Set ${key} in .env`); return value; };
  const snowflake=(key:string) => { const value=required(key); if(!/^\d{17,20}$/.test(value)) throw new Error(`${key} must be a Discord ID`); return value; };
  const users=new Set(required('DISCORD_USER_IDS').split(',').map(x=>x.trim()));
  if(users.size!==2 || [...users].some(x=>!/^\d{17,20}$/.test(x))) throw new Error('DISCORD_USER_IDS must contain exactly two distinct Discord user IDs');
  const repository=required('GITHUB_REPOSITORY');
  if(!/^[\w.-]+\/[\w.-]+$/.test(repository)) throw new Error('Invalid GITHUB_REPOSITORY');
  const authMode=(key:string): 'subscription'|'api' => {
    const value=env[key] || 'subscription'; if(value!=='subscription' && value!=='api') throw new Error(`Invalid ${key}`); return value;
  };
  const provider=env.YY_DEFAULT_PROVIDER || 'codex';
  if(provider!=='codex' && provider!=='claude') throw new Error('Invalid YY_DEFAULT_PROVIDER');
  const games=JSON.parse(readFileSync(resolve(root,'config/games.json'),'utf8')) as Record<string,GameConfig>;
  for(const [id,g] of Object.entries(games)) {
    if(!/^[a-z][a-z0-9_-]*$/.test(id) || !/^\w+$/.test(g.target) || !/^games\/[a-z0-9_-]+$/.test(g.directory)
      || !/^[\w.-]+$/.test(g.bundleId) || !/^\d+\.\d+\.\d+$/.test(g.version) || !g.internalGroup) throw new Error(`Invalid game configuration: ${id}`);
  }
  const timeout=Number(env.YY_AGENT_TIMEOUT_MS || 1_800_000);
  if(!Number.isSafeInteger(timeout) || timeout<1000) throw new Error('Invalid YY_AGENT_TIMEOUT_MS');
  for(const key of ['YY_DISCORD_CHANNEL_ID','YY_DISCORD_TALK_CHANNEL_ID']) if(env[key] && !/^\d{17,20}$/.test(env[key]!)) throw new Error(`${key} must be a Discord ID`);
  return {root,data:resolve(env.YY_DATA_DIR || resolve(root,'.yy')),guildId:snowflake('DISCORD_GUILD_ID'),applicationId:snowflake('DISCORD_APPLICATION_ID'),users,
    discordToken:required('DISCORD_TOKEN'),githubToken:required('GITHUB_TOKEN'),repository,defaultProvider:provider,
    auth:{codex:authMode('YY_CODEX_AUTH'),claude:authMode('YY_CLAUDE_AUTH')},
    executables:{codex:env.YY_CODEX_PATH || 'codex',claude:env.YY_CLAUDE_PATH || 'claude'},timeout,games,
    conversationChannelId:env.YY_DISCORD_CHANNEL_ID,talkChannelId:env.YY_DISCORD_TALK_CHANNEL_ID};
}
export function authorized(config: Pick<Config,'guildId'|'users'>,guildId:string|null,userId:string) {
  return guildId===config.guildId && config.users.has(userId);
}
// Provider subprocesses receive neither Discord/GitHub nor Apple credentials.
export function agentEnvironment(provider:Provider,auth:'subscription'|'api',source:NodeJS.ProcessEnv=process.env) {
  const env:NodeJS.ProcessEnv={};
  const allowed=['PATH','Path','SystemRoot','WINDIR','COMSPEC','PATHEXT','TEMP','TMP','TMPDIR','HOME','USERPROFILE','APPDATA','LOCALAPPDATA','PROGRAMFILES','PROGRAMFILES(X86)','SSL_CERT_FILE','SSL_CERT_DIR','LANG','LC_ALL','CLAUDE_CODE_GIT_BASH_PATH'];
  for(const key of allowed) if(source[key]!==undefined) env[key]=source[key];
  if(provider==='codex') {
    if(source.CODEX_HOME) env.CODEX_HOME=source.CODEX_HOME;
    if(auth==='api') { if(!source.OPENAI_API_KEY) throw new Error('API mode requires OPENAI_API_KEY'); env.OPENAI_API_KEY=source.OPENAI_API_KEY; }
  } else {
    if(source.CLAUDE_CONFIG_DIR) env.CLAUDE_CONFIG_DIR=source.CLAUDE_CONFIG_DIR;
    if(auth==='api') { if(!source.ANTHROPIC_API_KEY) throw new Error('API mode requires ANTHROPIC_API_KEY'); env.ANTHROPIC_API_KEY=source.ANTHROPIC_API_KEY; }
    else if(source.CLAUDE_CODE_OAUTH_TOKEN) env.CLAUDE_CODE_OAUTH_TOKEN=source.CLAUDE_CODE_OAUTH_TOKEN;
  }
  return env;
}
