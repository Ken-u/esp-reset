// Exercise the actual embedded UI: lost POST replies must only cause GET queries.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const assert=require('node:assert/strict'),crypto=require('node:crypto').webcrypto;
const html=fs.readFileSync(path.join(__dirname,'../main/web/index.html'),'utf8');
const source=html.match(/<script>([\s\S]*)<\/script>/)[1];
const nodes=new Map(),storage=new Map();
class Element{
 constructor(){this.children=[];this.disabled=false;this.dataset={};this.value='1500';}
 set id(v){this._id=v;nodes.set(v,this);}get id(){return this._id;}
 set innerHTML(v){this.children=[];for(const m of v.matchAll(/<(\w+)[^>]*\bid="([^"]+)"([^>]*)>/g)){const n=new Element();n.tag=m[1];n.id=m[2];const ch=m[3].match(/data-reset-channel="(\d+)"/);if(ch)n.dataset.resetChannel=ch[1];this.children.push(n);}}
 querySelector(t){return this.children.find(n=>n.tag===t);}append(n){this.children.push(n);}replaceChildren(){this.children=[];}addEventListener(){}
}
for(const m of html.split('<script>')[0].matchAll(/\bid="([^"]+)"/g)){const n=new Element();n.id=m[1];}
let postLost=false,queryLost=false,releasePost=null,deferPost=false;
let active=null;const posts=[];
const ctx=vm.createContext({
 document:{getElementById:id=>nodes.get(id),createElement:()=>new Element(),querySelectorAll:()=>[...nodes.values()].filter(n=>n.dataset.resetChannel)},
 sessionStorage:{getItem:k=>storage.get(k)||'',setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
 Date,Number,Array,Set,JSON,crypto,AbortSignal,encodeURIComponent,confirm:()=>true,setTimeout(){},setInterval(){},
 fetch:async(url,options={})=>{
  const response=x=>({ok:true,status:200,json:async()=>({ok:true,...x})});
  if(url==='/api/status')return response({boot_id:'boot-one',channels:[1,2,3].map(channel=>({channel,gpio:channel+3,cooldown_ms:0,reset_count:0,enabled:true,active:false})),connected:true,ssid:'test',ip:'',target:'esp32s3',version:'0.4.0',uptime_s:1,reset_count:0});
  if(options.method==='POST'){
   assert.equal(url,'/api/action');active=JSON.parse(options.body);posts.push(active);
   if(deferPost)await new Promise(r=>{releasePost=r;});
   if(postLost)throw Error('signal timed out');return response({state:'queued',request_id:active.request_id,boot_id:'boot-one'});
  }
  assert.ok(url.startsWith('/api/action?id='));
  if(queryLost)throw Error('signal timed out');
  return response({state:'released',request_id:url.split('id=')[1],boot_id:'boot-one',pins:[]});
 }
});
async function run(){
 storage.set('esp-reset-action',JSON.stringify({request_id:'old',boot_id:'old-boot',label:'旧操作'}));
 vm.runInContext(source,ctx);vm.runInContext("token='test-token'",ctx);await vm.runInContext('refresh()',ctx);
 assert.equal(nodes.get('boot-loader').disabled,false); // Historical pending receipt is informational.
 deferPost=true;const pending=vm.runInContext('resetChannel(3)',ctx);
 await new Promise(r=>setImmediate(r));
 assert.equal(posts.length,1);assert.equal(posts[0].channel,3);
 assert.equal(nodes.get('reset-3').disabled,true);assert.equal(nodes.get('boot-loader').disabled,true);
 await vm.runInContext('resetChannel(3)',ctx);assert.equal(posts.length,1);
 releasePost();deferPost=false;await pending;await new Promise(r=>setImmediate(r));
 postLost=true;queryLost=true;
 await vm.runInContext("startAction('loader',2)",ctx);await new Promise(r=>setImmediate(r));
 assert.equal(posts.length,2);assert.equal(posts[1].mode,'loader');assert.equal(posts[1].hold_ms,1500);
 assert.equal(nodes.get('boot-maskrom').disabled,false);assert.equal(nodes.get('reset-3').disabled,false);
 assert.ok(storage.get('esp-reset-action'));
 await vm.runInContext("startAction('maskrom',3)",ctx);await new Promise(r=>setImmediate(r));
 assert.equal(posts.length,3);assert.equal(posts[2].mode,'maskrom');
 assert.notEqual(posts[1].request_id,posts[2].request_id);
 assert.equal(nodes.get('boot-loader').disabled,false);
 await vm.runInContext('checkAction()',ctx);assert.equal(posts.length,3); // No automatic POST retry.
 console.log('Historical / timed-out receipts never block new manual actions; no automatic replay passed');
}
run().catch(e=>{console.error(e);process.exitCode=1;});
