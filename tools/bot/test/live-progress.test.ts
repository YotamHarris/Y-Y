import test from 'node:test';
import assert from 'node:assert/strict';
import { refreshLiveProgress, type ProgressTarget } from '../src/live-progress.js';
import type { Task } from '../src/types.js';

test('finished archived threads are cleaned and one channel failure cannot hide another task’s updates',async()=>{
  const base={game:'tapdemo',kind:'change',provider:'codex',prompt:'Change',createdAt:1,updatedAt:99} as Task;
  const tasks=[{...base,id:'unavailable',status:'ready'},{...base,id:'finished',status:'ready'},{...base,id:'apple',status:'processing',phaseStartedAt:1000,progress:'Waiting for required CI',lastStep:'Read source',runUrl:'https://example.invalid/run'}] as Task[];
  const calls:string[]=[],errors:string[]=[],ids=new Map(tasks.map(t=>[t.id,'line']));let content='';
  const archived:ProgressTarget={archived:true,setArchived:async value=>{archived.archived=value;calls.push('archived:'+value);},fetch:async()=>({id:'line',edit:async()=>{},delete:async()=>{if(archived.archived) throw new Error('Thread is archived');calls.push('delete');}}),send:async()=>{throw new Error('Unexpected send');}};
  await refreshLiveProgress(tasks,{readId:t=>ids.get(t.id),writeId:(t,id)=>{ids.set(t.id,id);},onError:t=>{errors.push(t.id);},
    target:async t=>{if(t.id==='unavailable') throw new Error('Channel missing');if(t.id==='finished') return archived;
      return {fetch:async()=>({id:'line',edit:async payload=>{content=payload.content;},delete:async()=>{}}),send:async()=>{throw new Error('Unexpected send');}};}
  },11000);
  assert.deepEqual(errors,['unavailable']);assert.deepEqual(calls,['archived:false','delete','archived:true']);assert.equal(ids.get('finished'),'');
  assert.match(content,/Waiting on services · 10s/);assert.match(content,/Apple/);assert.match(content,/no coding agent/);assert(!content.includes('codex'));assert(!content.includes('required CI'));assert(!content.includes('Read source'));
});
