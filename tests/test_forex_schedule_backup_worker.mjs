import test from "node:test";
import assert from "node:assert/strict";
import worker,{backupCheck} from "../experimentos/forex_schedule_backup_worker.mjs";

const NOW=Date.parse("2026-11-05T02:00:00Z");
const at=(minutes)=>new Date(NOW-minutes*60_000).toISOString();
const HB=(age=40)=>({schema_version:1,mode:"PAPER_ONLY",status:"SUCCESS",
  run_id:"41900000000",run_attempt:1,completed_at:at(age),
  state_last_checked_at:at(age)});
const run=(age=45,status="completed")=>({name:"Forex Bot Pro V2 - Paper EURUSD",
  head_branch:"main",created_at:at(age),status,conclusion:"success"});
const kv=()=>{
  const db=new Map();
  return {db,get:async k=>db.get(k)??null,
    put:async (k,v)=>{db.set(k,v);}};
};
const env=(extra={})=>({
  BACKUP_SCHEDULER_MODE:"active",
  GITHUB_DISPATCH_TOKEN:"OFFLINE_FAKE_ACTIONS_TOKEN",
  BACKUP_DISPATCH_KV:kv(),...extra
});
function mock(heartbeat=HB(),runs=[run()],status=204){
  const calls=[];
  const fetcher=async(url,options={})=>{
    calls.push({url:String(url),options});
    if(String(url).includes("raw.githubusercontent.com"))
      return new Response(JSON.stringify(heartbeat),{status:200});
    if(String(url).includes("/dispatches"))
      return new Response(null,{status});
    if(String(url).includes("/runs"))
      return new Response(JSON.stringify({workflow_runs:runs}),{status:200});
    throw Error("Unexpected external URL");
  };
  return {calls,fetcher};
}
const posts=(calls)=>calls.filter(x=>x.options.method==="POST");

test("opt-in is mandatory: default/observe sends no requests",async()=>{
  let seen=0;
  const fetcher=async()=>{seen++;throw Error("network forbidden");};
  assert.equal((await backupCheck({},NOW,fetcher)).action,"OBSERVE_ONLY");
  assert.equal((await backupCheck({BACKUP_SCHEDULER_MODE:"observe"},NOW,fetcher)).action,"OBSERVE_ONLY");
  assert.equal(seen,0);
});
test("active requires token and isolated KV",async()=>{
  await assert.rejects(()=>backupCheck({BACKUP_SCHEDULER_MODE:"active"},NOW),/BACKUP_CONFIG_INCOMPLETE/);
});
test("fresh PAPER heartbeat skips dispatch even when no extra calls",async()=>{
  const m=mock(HB(4));
  assert.equal((await backupCheck(env(),NOW,m.fetcher)).action,"SKIP_RECENT_HEARTBEAT");
  assert.equal(m.calls.length,1);
  assert.equal(posts(m.calls).length,0);
});
test("stale heartbeat with running paper workflow is not double dispatched",async()=>{
  const m=mock(HB(40),[run(42,"in_progress")]);
  assert.equal((await backupCheck(env(),NOW,m.fetcher)).action,"SKIP_BOT_ALREADY_RUNNING");
  assert.equal(posts(m.calls).length,0);
});
test("stale heartbeat with recently completed paper job is not dispatched",async()=>{
  const m=mock(HB(40),[run(9)]);
  assert.equal((await backupCheck(env(),NOW,m.fetcher)).action,"SKIP_RECENT_GITHUB_RUN");
  assert.equal(posts(m.calls).length,0);
});
test("old heartbeat and old runs dispatch PAPER_ONLY main exactly once",async()=>{
  const e=env(),m=mock(HB(80),[run(90)]);
  const out=await backupCheck(e,NOW,m.fetcher);
  assert.equal(out.action,"BACKUP_DISPATCHED");
  assert.equal(posts(m.calls).length,1);
  assert.equal(posts(m.calls)[0].url,
    "https://api.github.com/repos/veloraspaces0/forex-bot-pro-v1-demo/actions/workflows/forex-v2.yml/dispatches");
  assert.deepEqual(JSON.parse(posts(m.calls)[0].options.body),{ref:"main"});
  assert.equal(posts(m.calls)[0].options.headers.Authorization,
    "Bearer OFFLINE_FAKE_ACTIONS_TOKEN");
  assert.equal(m.calls[0].options.headers.Authorization,undefined,
    "raw public heartbeat must not receive the GitHub token");
  const again=await backupCheck(e,NOW+5*60_000,m.fetcher);
  assert.equal(again.action,"SKIP_COOLDOWN");
  assert.equal(posts(m.calls).length,1);
});
test("403 on Actions read fails closed without dispatch",async()=>{
  let posted=false;
  const fetcher=async url=>{
    if(String(url).includes("/dispatches"))posted=true;
    return String(url).includes("raw.githubusercontent.com")?
      new Response(JSON.stringify(HB(80)),{status:200}):
      new Response("Access denied",{status:403});
  };
  await assert.rejects(()=>backupCheck(env(),NOW,fetcher),/UPSTREAM_HTTP_403/);
  assert.equal(posted,false);
});
test("403/500 on dispatch does not record a fake success",async()=>{
  const e=env(),m=mock(HB(80),[run(90)],403);
  await assert.rejects(()=>backupCheck(e,NOW,m.fetcher),/DISPATCH_HTTP_403/);
  assert.equal(e.BACKUP_DISPATCH_KV.db.size,0);
});
test("bad heartbeat identity fails closed",async()=>{
  const m=mock({...HB(90),mode:"LIVE"},[run(90)]);
  await assert.rejects(()=>backupCheck(env(),NOW,m.fetcher),/HEARTBEAT_INVALID/);
  assert.equal(posts(m.calls).length,0);
});
test("future or timezone-free heartbeat timestamp fails closed",async()=>{
  for(const timestamp of ["2026-11-05T02:05:00Z","2026-11-05T01:00:00"]){
    const m=mock({...HB(90),completed_at:timestamp},[run(90)]);
    await assert.rejects(()=>backupCheck(env(),NOW,m.fetcher),/TIMESTAMP_UNTRUSTED/);
    assert.equal(posts(m.calls).length,0);
  }
});
test("malformed cooldown storage fails closed",async()=>{
  const e=env(),m=mock(HB(80),[run(90)]);
  await e.BACKUP_DISPATCH_KV.put("forex-paper-only-backup-last-dispatch-v1","not_timestamp");
  await assert.rejects(()=>backupCheck(e,NOW,m.fetcher),/COOLDOWN_RECORD_INVALID/);
  assert.equal(posts(m.calls).length,0);
});
test("public fetch handler cannot cause dispatch",async()=>{
  assert.equal((await worker.fetch()).status,404);
});
