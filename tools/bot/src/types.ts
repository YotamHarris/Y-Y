export type Provider = 'codex' | 'claude';
export type Kind = 'ask' | 'change' | 'build' | 'plan';
export interface PlannedTask { title:string; prompt:string; depends:number[] }
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
}
export interface AgentResult { outcome: 'completed' | 'needs_input'; summary: string; question: string; review: 'approve' | 'request_changes' | 'none'; sessionId?: string }
export interface AgentEvent { type: 'progress' | 'session' | 'error'; text?: string; sessionId?: string }
export const terminal = new Set<Status>(['ready','completed','failed','cancelled']);
export const remote = new Set<Status>(['awaiting_checks','merging','building','uploaded','processing']);
