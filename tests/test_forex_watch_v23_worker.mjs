import test from "node:test";
import assert from "node:assert/strict";
import {assess,fxMarketClosed,sundayGrace,minutesAgo,deliver,runCheck,readPublic}
  from "../experimentos/forex_watch_v23_worker.mjs";

const NOW=Date.parse("2026-11-04T16:00:00Z");
const stamp=(mins,now=NOW)=>new Date(now-mins*60000).toISOString();
const state=(updated=4,candle=20,opened=false,now=NOW,extra={})=>({
  mode:"PAPER_ONLY",last_checked_at:stamp(updated,now),updated_at:stamp(updated,now),
  last_candle_at:stamp(candle,now),open_position:opened,position_side:opened?"SELL":null,
  status:"monitoring",gap_halt:false,balance:99.5,estimated_equity:99.45,closed_trades:1,...extra
});
const runs=(ages=[4,19,34,49,64],now=NOW)=>ages.map((min,k)=>({
  id:k+1,head_branch:"main",name:"Forex Bot Pro V2 - Paper EURUSD",
  status:"completed",conclusion:"success",event:"schedule",created_at:stamp(min,now),
  run_started_at:stamp(min-1,now)
}));
const codes=(r)=>r.issues.map(x=>x.code);
test("healthy 15min schedule",()=>{
  const r=assess(state(),runs(),NOW);
  assert.equal(r.status,"OK");assert.deepEqual(r.scheduleGapsMin,[15,15,15,15]);
});
test("fresh heartbeat but stale market candles = critical",()=>{
  const r=assess(state(2,120),runs(),NOW);
  assert.equal(r.status,"CRITICO");assert.ok(codes(r).includes("CANDLE_STALE"));
});
test("paper open unmonitored >35min",()=>{
  assert.ok(codes(assess(state(36,40,true),runs(),NOW)).includes("POSITION_UNCHECKED"));
});
test("run stuck distinguish from schedule delay",()=>{
  const r=runs();r[0].status="in_progress";r[0].conclusion=null;r[0].run_started_at=stamp(25);
  const c=codes(assess(state(),r,NOW));assert.ok(c.includes("RUN_STUCK"));
  assert.ok(!c.includes("SCHEDULE_MISSING"));
});
test("GitHub queue stuck distinguish",()=>{
  const r=runs([24,39,54,69,84]);r[0].status="queued";r[0].conclusion=null;
  assert.ok(codes(assess(state(),r,NOW)).includes("QUEUE_STUCK"));
});
test("55min intermittent schedule delays",()=>{
  const r=assess(state(),runs([4,59,114]),NOW);
  assert.ok(codes(r).includes("CADENCE_DELAY"));
});
test("one schedule results in insufficient history warning",()=>{
  assert.ok(codes(assess(state(),runs([4]),NOW)).includes("INSUFFICIENT_SCHEDULE_HISTORY"));
});
test("old failed run retained as warning",()=>{
  const r=runs();r[1].conclusion="failure";
  assert.ok(codes(assess(state(),r,NOW)).includes("RECENT_FAILURE"));
});
test("weekend market gap ignored, heartbeat still monitored",()=>{
  const sat=Date.parse("2026-11-07T15:00:00Z");
  const out=assess(state(4,1200,false,sat),runs([4,19,34,49,64],sat),sat);
  assert.equal(out.marketClosedWeekend,true);
  assert.ok(!codes(out).includes("CANDLE_STALE"));
});
test("New York DST Fri closing and Sun opening",()=>{
  assert.equal(fxMarketClosed(Date.parse("2026-07-03T21:01:00Z")),true);
  assert.equal(fxMarketClosed(Date.parse("2026-07-03T20:59:00Z")),false);
  assert.equal(sundayGrace(Date.parse("2026-11-08T22:20:00Z")),true);
  assert.equal(sundayGrace(Date.parse("2026-11-08T23:00:00Z")),false);
});
test("rejected future timestamp",()=>{
  assert.throws(()=>minutesAgo(stamp(-10),NOW));
});
test("not an authorized live trading mode",()=>{
  assert.throws(()=>assess(state(4,20,false,NOW,{mode:"LIVE"}),runs(),NOW));
});
test("no KV or Telegram => no notifications",async()=>{
  const alerts=[];
  const r=await deliver({status:"CRITICO",issues:[{code:"DATA_GAP",message:"gap"}]}, {},NOW,async (...a)=>{alerts.push(a);return new Response("ok")});
  assert.equal(r.notified,false);assert.equal(alerts.length,0);
});
test("delivery: alert once, suppress repeat, recovery once",async()=>{
  const records=new Map(),notify=[];
  const KV={get:async k=>records.get(k)??null,
    put:async(k,v)=>{records.set(k,v)},delete:async k=>records.delete(k)};
  const env={INCIDENT_KV:KV,TELEGRAM_BOT_TOKEN:"TEST_ONLY",TELEGRAM_CHAT_ID:"TEST_CHAT"};
  const fetcher=async url=>{notify.push(String(url));return new Response(JSON.stringify({ok:true}),{status:200})};
  const bad={status:"CRITICO",issues:[{severity:"CRITICO",code:"DATA_GAP",message:"gap"}],
    balance:99.5,openPosition:true,checkedAt:stamp(20)};
  assert.equal((await deliver(bad,env,NOW,fetcher)).notified,true);
  assert.equal((await deliver(bad,env,NOW+15*60000,fetcher)).notified,false);
  const good={status:"OK",issues:[]};
  assert.equal((await deliver(good,env,NOW+30*60000,fetcher)).recovery,true);
  assert.equal((await deliver(good,env,NOW+45*60000,fetcher)).notified,false);
  assert.equal(notify.length,2);
});
test("live read-only GitHub fetch and NOT encrypted state",async()=>{
  const urls=[];
  const fake=async url=>{
    urls.push(String(url));
    const body=String(url).includes("/contents/")
      ? {encoding:"base64",content:Buffer.from(JSON.stringify(state())).toString("base64")}
      : {workflow_runs:runs()};
    return new Response(JSON.stringify(body),{status:200,headers:{"content-type":"application/json"}});
  };
  const result=await readPublic(NOW,fake);
  assert.equal(result.status,"OK");
  assert.equal(urls.length,2);
  assert.ok(urls.every(u=>u.includes("api.github.com")));
  assert.ok(urls.every(u=>!u.includes("state.enc")&&!u.includes("secrets")));
});
test("upstream GitHub outage => INDETERMINADO, not bot failed",async()=>{
  let count=0;
  const unavailable=async()=>{count++;return new Response("unavailable",{status:403})};
  const out=await runCheck({},NOW,unavailable);
  assert.equal(out.report.status,"INDETERMINADO");
  assert.ok(codes(out.report).includes("MONITOR_UNAVAILABLE"));
  assert.ok(count>0);
});
