import test from 'node:test';
import assert from 'node:assert/strict';
import { decisions, deliverUpdate, type DeliveryChannel, type DeliveryMessage } from '../src/discord-view.js';
import type { Task } from '../src/types.js';

const task={id:'task',eventId:'owner',kind:'change',game:'tapdemo',provider:'codex',prompt:'Make targets easier to hit',status:'implementing',createdAt:1,updatedAt:1,attempt:0} as Task;
test('Discord only presents current decisions, including exact published commit',()=>{
  assert.equal(decisions(task).length,0);
  const rows=decisions({...task,status:'building',acceptance:'pending',reviewSha:'123456'}).map(r=>r.toJSON());
  assert.deepEqual(rows[0]?.components.map(c=>(c as any).label),['Accept','Request changes']);assert.match((rows[0]?.components[0] as any).custom_id,/123456$/);
  assert.equal(decisions({...task,kind:'plan',status:'waiting_input',proposalAt:2,proposal:[{title:'Hit area',prompt:'Increase radius',depends:[]}]}).length,1);
});
test('a lost Discord send response is reconciled without duplicate reports or decision buttons',async()=>{
  const history:DeliveryMessage[]=[];const payloads:any[]=[];let lose=true;const cleared:string[]=[];
  const channel:DeliveryChannel={history:async()=>history,clear:async id=>{cleared.push(id);},send:async payload=>{
    payloads.push(payload);const id=String(payloads.length);history.unshift({id,authorId:'bot',createdAt:2,footers:payload.embeds.map((e:any)=>e.footer.text)});
    if(lose) {lose=false;throw new Error('lost response');}return {id};
  }};
  const options={channel,task:{...task,status:'building' as const,acceptance:'pending' as const,reviewSha:'head'},text:'Published result',receipt:'YY event 1',retry:false,botId:'bot',actionable:true,previousDecision:'old'};
  await assert.rejects(()=>deliverUpdate(options),/lost/);
  const recovered=await deliverUpdate({...options,retry:true});assert.equal(payloads.length,1);assert.equal(recovered.decisionId,'1');assert.deepEqual(cleared,['old']);assert.equal(payloads[0].files.length,1);
  await deliverUpdate({...options,text:'Processing continues',receipt:'YY event 2',retry:false,actionable:false,previousDecision:undefined});assert.deepEqual(payloads[1].components,[]);
});
