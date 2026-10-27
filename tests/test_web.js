// Run the actual embedded UI script against a minimal DOM/API fixture.
// Checks that clicking one reset channel cannot block or select the other two.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const crypto = require('node:crypto').webcrypto;
const html = fs.readFileSync(path.join(__dirname, '../main/web/index.html'), 'utf8');
const source = html.match(/<script>([\s\S]*)<\/script>/)[1];
const nodes = new Map();
class Element {
    constructor() { this.children = []; this.disabled = false; this.dataset = {}; }
    set id(value) { this._id = value; nodes.set(value, this); }
    get id() { return this._id; }
    set innerHTML(value) {
        this.children = [];
        for (const match of value.matchAll(/<(\w+)[^>]*\bid="([^"]+)"([^>]*)>/g)) {
            const child = new Element(); child.tag = match[1]; child.id = match[2];
            const channel = match[3].match(/data-reset-channel="(\d+)"/);
            if (channel) child.dataset.resetChannel = channel[1];
            this.children.push(child);
        }
    }
    querySelector(tag) { return this.children.find(child => child.tag === tag); }
    append(node) { this.children.push(node); }
    replaceChildren() { this.children = []; }
    addEventListener() {}
}
for (const match of html.split('<script>')[0].matchAll(/\bid="([^"]+)"/g)) {
    const node = new Element(); node.id = match[1];
}
const channels = [1,2,3].map(channel => ({channel, gpio:channel+2, cooldown_ms:0,
    reset_count:0, enabled:true, active:false}));
const calls = [];
let release;
let defer = true;
const context = vm.createContext({
    document:{getElementById:id=>nodes.get(id), createElement:()=>new Element(),
        querySelectorAll:()=>[...nodes.values()].filter(node=>node.dataset.resetChannel)},
    sessionStorage:{getItem:()=>'',setItem(){},removeItem(){}},
    Date, Number, Array, Set, crypto, AbortSignal, confirm:()=>true,
    setTimeout(){},setInterval(){},
    fetch:async (url, options={})=>{
        if (url === '/api/status') return {ok:true,status:200,json:async()=>({ok:true,channels,
            connected:true,ssid:'test',ip:'',target:'esp32c3',version:'0.2.0',uptime_s:1,
            reset_count:0,ap_enabled:false})};
        assert.equal(url,'/api/reset');
        const body=JSON.parse(options.body);calls.push(body);
        assert.equal(options.headers['X-Reset-Token'],'test-token');
        assert.equal(body.request_id.length,32);
        if(defer)await new Promise(resolve=>{release=resolve;});
        return {ok:true,status:200,json:async()=>({ok:true,channel:body.channel,state:'released'})};
    }
});
async function run() {
    vm.runInContext(source,context);
    vm.runInContext("token='test-token'",context);
    await vm.runInContext('refresh()',context);
    for(let ch=1;ch<=3;ch++) assert.equal(nodes.get('reset-'+ch).disabled,false);
    const pending = vm.runInContext('resetChannel(2)',context);
    assert.equal(nodes.get('reset-2').disabled,true);
    assert.equal(nodes.get('reset-1').disabled,false);
    assert.equal(nodes.get('reset-3').disabled,false);
    assert.equal(calls[0].channel,2);
    release();defer=false;await pending;
    await vm.runInContext('refresh()',context);
    assert.equal(nodes.get('reset-2').disabled,true);
    assert.equal(nodes.get('reset-1').disabled,false);
    assert.equal(nodes.get('reset-3').disabled,false);
    await vm.runInContext('resetChannel(2)',context); // cooldown: no extra request
    assert.equal(calls.length,1);
    await vm.runInContext('resetChannel(1)',context);
    await vm.runInContext('resetChannel(3)',context);
    assert.deepEqual(calls.map(call=>call.channel),[2,1,3]);
    console.log('Embedded web UI: channel selection and independent cooldown passed');
}
run().catch(error=>{console.error(error);process.exitCode=1;});
