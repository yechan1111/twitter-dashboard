import {test} from 'node:test';
import assert from 'node:assert/strict';
import worker, {refresh} from '../worker.mjs';
class KV {
 constructor(){this.store=new Map()}
 async get(key,type){const v=this.store.get(key);return v === undefined ? null : type==='json' ? JSON.parse(v) : v}
 async put(key,value){this.store.set(key,value)}
}
function env(){return {DASHBOARD:new KV(),DASHBOARD_PASSWORD:'a-secure-test-password',TWITTERAPI_IO_KEY:'test-key',ASSETS:{fetch:async()=>new Response('<h1>dashboard</h1>')}}}
function req(path='/',options={}){return new Request('https://dashboard.example'+path,{...options,headers:{Authorization:'Basic '+btoa('admin:a-secure-test-password'),...options.headers}})}
test('protects static assets and API before authentication',async()=>{
 const e=env();for(const path of ['/','/api/dashboard']) assert.equal((await worker.fetch(new Request('https://dashboard.example'+path),e)).status,401);
 assert.equal((await worker.fetch(req('/',{headers:{Authorization:'Basic '+btoa('admin:wrong')}}),e)).status,401);
 assert.equal((await worker.fetch(req(),{...e,DASHBOARD_PASSWORD:''})).status,503);
 assert.equal((await worker.fetch(req(),e)).headers.get('X-Frame-Options'),'DENY');
});
test('scheduled refresh normalizes actual provider shape and preserves data on billing failure',async()=>{
 const e=env();const now=Date.now();let calls=0;
 const api=async url=>{calls++;return Response.json(url.includes('/info')?{data:{id:'1',followers:91}}:{data:{tweets:[{id:'123',text:'hello',likeCount:2}]}})};
 const result=await refresh(e,now,api);assert.equal(calls,6);assert.equal(result.usage,6);
 let data=await (await worker.fetch(req('/api/dashboard'),e)).json();
 assert.equal(data.accounts[0].posts[0].likes,2);assert.equal(data.accounts[0].followers,91);
 assert.equal(JSON.stringify(data).includes('test-key'),false);
 assert.equal((await refresh(e,now+1000,api)).skipped,'recent attempt');assert.equal(calls,6);
 await refresh(e,now+5*3600000,async()=>new Response('',{status:402}));
 data=await (await worker.fetch(req('/api/dashboard'),e)).json();assert.equal(data.accounts[0].posts[0].text,'hello');assert.match(data.accounts[0].error,/402/);
});
test('record storage and cross-site rejection',async()=>{
 const e=env();const payload={handle:'GGzy55',text:'실제 게시 기록',category:'공부',url:'https://x.com/GGzy55/status/123'};
 const request=origin=>req('/api/records',{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(payload)});
 assert.equal((await worker.fetch(request('https://evil.example'),e)).status,403);
 assert.equal((await worker.fetch(request('https://dashboard.example'),e)).status,201);
 const data=await (await worker.fetch(req('/api/dashboard'),e)).json();assert.equal(data.records[0].text,payload.text);
 assert.equal((await worker.fetch(req('/api/records',{method:'POST',headers:{Origin:'https://dashboard.example','Content-Type':'application/json'},body:JSON.stringify({...payload,handle:'other'})}),e)).status,400);
});
test('monthly cap prevents additional paid calls',async()=>{
 const e=env();const now=Date.now();const month=new Date(now+9*3600000).toISOString().slice(0,7);await e.DASHBOARD.put('usage:'+month,'600');
 let calls=0;await refresh(e,now,async()=>{calls++;throw Error('must not fetch')});assert.equal(calls,0);
 const data=await e.DASHBOARD.get('snapshot','json');assert.match(data.accounts[0].error,/상한/);
});
