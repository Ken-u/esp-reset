// Runs only in the trusted main-push job after both board builds and all tests.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

module.exports = async ({github, context, core, directory = 'release-assets', version = process.env.RELEASE_VERSION}) => {
    if (!/^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.test(version)) throw Error('Invalid release version');
    if (context.eventName !== 'push' || context.ref !== 'refs/heads/main') throw Error('Release requires a main push');
    const repo = context.repo, sha = context.sha, tag = `v${version}`;
    const names = ['esp32s3-n16r8', 'esp32c3-supermini'].flatMap(board =>
        ['.zip', '-ota.bin'].map(suffix => `esp-reset-${board}-${tag}${suffix}`));
    const expected = [...names, 'SHA256SUMS'].sort();
    if (JSON.stringify(fs.readdirSync(directory).sort()) !== JSON.stringify(expected)) throw Error('Incomplete release assets');
    const checksums = new Map(fs.readFileSync(path.join(directory,'SHA256SUMS'),'utf8').trim().split('\n').map(line => {
        const match = line.match(/^([a-f0-9]{64})  (.+)$/); if (!match) throw Error('Invalid checksums'); return [match[2],match[1]];
    }));
    if (checksums.size !== names.length) throw Error('Checksum count mismatch');
    for (const name of names) if (crypto.createHash('sha256').update(fs.readFileSync(path.join(directory,name))).digest('hex') !== checksums.get(name)) throw Error('Checksum mismatch: '+name);
    try {
        const commit = await github.rest.repos.getCommit({...repo, ref:tag});
        if (commit.data.sha !== sha) throw Error('Refusing to reuse tag pointing to another commit');
    } catch (error) { if (error.status !== 404) throw error; }
    let release;
    try { release = (await github.rest.repos.getReleaseByTag({...repo,tag})).data; }
    catch (error) { if (error.status !== 404) throw error; }
    if (!release) {
        // The by-tag endpoint documents published releases only; drafts require listing.
        const drafts = (await github.paginate(github.rest.repos.listReleases,{...repo,per_page:100}))
            .filter(item => item.draft && item.tag_name === tag);
        if (drafts.length > 1) throw Error('Multiple drafts exist for this version');
        release = drafts[0];
    }
    if (release && !release.draft) {
        const assets = await github.paginate(github.rest.repos.listReleaseAssets,{...repo,release_id:release.id,per_page:100});
        if (!expected.every(name => assets.some(asset => asset.name === name))) throw Error('Published release is incomplete; refusing to overwrite');
        core.info('Release already published; leaving it unchanged'); return;
    }
    if (release && release.target_commitish !== sha) throw Error('Draft release belongs to another commit');
    if (!release) release = (await github.rest.repos.createRelease({...repo,tag_name:tag,target_commitish:sha,
        name:`ESP Reset ${tag}`,draft:true,generate_release_notes:true,
        body:'下载对应板型的 ZIP，解压后只有一个入口：`./espreset --help`。首次使用 `./espreset setup`；网络控制 `./espreset reset --ip 设备IP`，不指定IP走USB。已有设备可只下载对应板型的 `-ota.bin` 在网页升级。校验文件为 SHA256SUMS。'})).data;
    const assets = await github.paginate(github.rest.repos.listReleaseAssets,{...repo,release_id:release.id,per_page:100});
    for (const name of expected) {
        const old = assets.find(asset => asset.name === name);
        if (old) await github.rest.repos.deleteReleaseAsset({...repo,asset_id:old.id}); // Draft-only replacement for safe retry.
        const data = fs.readFileSync(path.join(directory,name));
        await github.rest.repos.uploadReleaseAsset({...repo,release_id:release.id,name,
            headers:{'content-type':'application/octet-stream','content-length':data.length},data});
    }
    const uploaded = await github.paginate(github.rest.repos.listReleaseAssets,{...repo,release_id:release.id,per_page:100});
    if (!expected.every(name => uploaded.some(asset => asset.name === name && asset.state === 'uploaded'))) throw Error('Asset upload incomplete; keeping draft');
    await github.rest.repos.updateRelease({...repo,release_id:release.id,draft:false,make_latest:'true'});
    core.info(`Published ${tag} at ${sha}`);
};
