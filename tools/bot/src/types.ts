export type Provider = 'codex' | 'claude';
export type Kind = 'ask' | 'change' | 'build' | 'plan';
export interface PlannedTask { title:string; prompt:string; depends:number[] }
export interface Question { question:string; options:string[] }
export type Status = 'queued' | 'preparing' | 'implementing' | 'checking' | 'reviewing' | 'awaiting_checks' | 'merging' | 'building' | 'uploaded' | 'processing' | 'ready' | 'completed' | 'waiting_input' | 'interrupted' | 'failed' | 'cancelled';
export interface GameConfig { target: string; directory: string; bundleId: string; version: string; internalGroup: string }
export interface Task {
  id: string; eventId: string; kind: Kind; game: string; provider: Provider; model?: string;
  userId: string; channelId: string; threadId?: string; prompt: string; status: Status;
  createdAt: number; updatedAt: number; worktree?: string; branch?: string; baseSha?: string;
  headSha?: string; mergeSha?: string; pr?: number; prUrl?: string; runId?: number; runUrl?: string;
  sessionId?: string; attempt: number; error?: string; summary?: string; cancelRequested?: boolean;
  dispatchAt?: number; remoteErrorAt?: number; cancelPending?: boolean; rerunAt?: number; pausedFrom?: Status;
  source?: 'discord' | 'multica'; boardIssueId?:string; dependencies?:string[];
  proposal?:PlannedTask[]; proposalAt?:number; approvedBy?:string; parentTaskId?:string;
  progress?:string; question?:string;
  conversationParentId?:string; replyVersion?:number;
  originBoardIssueId?:string;
  goalName?:string; planRound?:number;
  reviewSha?:string; acceptedSha?:string; acceptance?:'pending'|'accepted'; revision?:number;
  deliveries?:{sha:string;url?:string;runId?:number;status:Status}[];
  questions?:Question[]; questionVersion?:number; answers?:Record<number,string>;
  workerSessionId?:string; sessionAt?:number; quickKey?:string;
  runStartedAt?:number; runCount?:number; totalRunMs?:number; lastStep?:string;
  usage?:{input:number;output:number;cached:number};
  number?:number;
  remotePollAt?:number;
  phaseStartedAt?:number;
}
export interface AgentResult { outcome: 'completed' | 'needs_input'; summary: string; question: string; review: 'approve' | 'request_changes' | 'none'; sessionId?: string; asks?:Question[]; goalName?:string }
export interface AgentEvent { type: 'progress' | 'session' | 'error' | 'step' | 'usage'; text?: string; sessionId?: string; usage?:{input:number;output:number;cached:number} }
export const terminal = new Set<Status>(['ready','completed','failed','cancelled']);
export const remote = new Set<Status>(['awaiting_checks','merging','building','uploaded','processing']);
