// No network: execute the exact publisher against a simulated GitHub API.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),crypto=require('node:crypto');
const publish=require('../tools/publish_release.cjs');
const directory=fs.mkdtempSync(path.join(os.tmpdir(),'esp-release-'));
const names=['esp32s3-n16r8','esp32c3-supermini'].flatMap(b=>['.zip','-ota.bin'].map(s=>`esp-reset-${b}-v0.5.0${s}`));
for(const name of names)fs.writeFileSync(path.join(directory,name),'binary-'+name);
fs.writeFileSync(path.join(directory,'SHA256SUMS'),names.map(name=>crypto.createHash('sha256').update(fs.readFileSync(path.join(directory,name))).digest('hex')+'  '+name).join('\n')+'\n');
function fixture({failUpload=false,wrongTag=false}={}){
 const calls=[];let release=null,assets=[];
 const notFound=()=>{throw Object.assign(Error('missing'),{status:404});};
 const repos={
  getCommit:async()=>wrongTag?{data:{sha:'wrong'}}:notFound(),
  getReleaseByTag:async()=>release&&!release.draft?{data:release}:notFound(),
  listReleases:async()=>({data:release?[release]:[]}),
  createRelease:async args=>{assert.equal(args.draft,true);calls.push('draft');release={id:1,draft:true,tag_name:args.tag_name,target_commitish:args.target_commitish};return{data:release};},
  listReleaseAssets:async()=>({data:assets}),
  deleteReleaseAsset:async args=>{assets=assets.filter(a=>a.id!==args.asset_id);},
  uploadReleaseAsset:async args=>{calls.push('upload');if(failUpload)throw Error('upload interrupted');assets.push({id:assets.length+1,name:args.name,state:'uploaded'});},
  updateRelease:async args=>{calls.push('publish');assert.equal(args.draft,false);assert.equal(assets.length,5);release.draft=false;}
 };
 const github={rest:{repos},paginate:async(fn,args)=>(await fn(args)).data};
 const args={github,context:{repo:{owner:'test',repo:'test'},sha:'commit-one',eventName:'push',ref:'refs/heads/main'},core:{info(){}},directory,version:'0.5.0'};
 return{args,calls,resume:()=>{failUpload=false;}};
}
(async()=>{
 const first=fixture();await publish(first.args);assert.equal(first.calls.at(-1),'publish');
 const uploads=first.calls.filter(x=>x==='upload').length;await publish(first.args);assert.equal(first.calls.filter(x=>x==='upload').length,uploads);
 const interrupted=fixture({failUpload:true});await assert.rejects(publish(interrupted.args),/interrupted/);assert.ok(!interrupted.calls.includes('publish'));interrupted.resume();await publish(interrupted.args);assert.equal(interrupted.calls.filter(x=>x==='draft').length,1);assert.equal(interrupted.calls.at(-1),'publish');
 const wrong=fixture({wrongTag:true});await assert.rejects(publish(wrong.args),/another commit/);assert.equal(wrong.calls.length,0);
 const pr=fixture();pr.args.context.eventName='pull_request';await assert.rejects(publish(pr.args),/main push/);assert.equal(pr.calls.length,0);
 console.log('Publisher: complete assets before publish, safe retry, immutable existing release, tag identity and PR guard passed');
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(()=>fs.rmSync(directory,{recursive:true,force:true}));
