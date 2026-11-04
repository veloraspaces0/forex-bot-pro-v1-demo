import test from "node:test";
import assert from "node:assert/strict";
import {assess,fxMarketClosed,sundayGrace,minutesAgo,deliver,runCheck,readPublic,signalHeartbeat,scheduledCheck}
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
test("delivery: one alert, second healthy observation needed for recovery",async()=>{
  const records=new Map(),notify=[];
  const KV={get:async k=>records.get(k)??null,
    put:async(k,v)=>{records.set(k,v)},delete:async k=>records.delete(k)};
  const env={ALERT_MODE:"active",INCIDENT_KV:KV,TELEGRAM_BOT_TOKEN:"TEST_ONLY",TELEGRAM_CHAT_ID:"TEST_CHAT"};
  const fetcher=async url=>{notify.push(String(url));return new Response(JSON.stringify({ok:true}),{status:200})};
  const bad={status:"CRITICO",issues:[{severity:"CRITICO",code:"DATA_GAP",message:"gap"}],
    balance:99.5,openPosition:true,checkedAt:stamp(20)};
  assert.equal((await deliver(bad,env,NOW,fetcher)).notified,true);
  assert.equal((await deliver(bad,env,NOW+15*60000,fetcher)).notified,false);
  const first={status:"OK",issues:[],checkedAt:stamp(-29)};
  assert.equal((await deliver(first,env,NOW+30*60000,fetcher)).notified,false);
  assert.equal((await deliver(first,env,NOW+45*60000,fetcher)).notified,false);
  const second={status:"OK",issues:[],checkedAt:stamp(-44)};
  assert.equal((await deliver(second,env,NOW+45*60000,fetcher)).recovery,true);
  assert.equal((await deliver(second,env,NOW+60*60000,fetcher)).notified,false);
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


function activeEnv(KV) {
  return {ALERT_MODE:"active",INCIDENT_KV:KV,
    TELEGRAM_BOT_TOKEN:"TEST_ONLY",TELEGRAM_CHAT_ID:"TEST_CHAT"};
}
function memoryKV() {
  const records=new Map();
  return {records, get:async k=>records.get(k)||null,
    put:async(k,v)=>{records.set(k,v)},delete:async k=>{records.delete(k)}};
}
function critical(runId=1) {
  return {status:"CRITICO",issues:[{severity:"CRITICO",code:"RUN_FAILED",message:"run failed"}],
    lastRunId:runId,checkedAt:stamp(4),openPosition:true,balance:99.5};
}
const telegramOk=async()=>new Response('{"ok":true}',{status:200});
test("production active mode refuses missing alert channel",async()=>{
  await assert.rejects(deliver(critical(),{ALERT_MODE:"active"},NOW,telegramOk),
    /ALERT_CHANNEL_NOT_CONFIGURED/);
});
test("KV read failure does not silence a CRITICO incident",async()=>{
  let sent=0;
  const KV={get:async()=>{throw Error("KV unavailable")},
    put:async()=>{throw Error("KV unavailable")},delete:async()=>{}};
  const out=await deliver(critical(),activeEnv(KV),NOW,async()=>{sent++;return telegramOk()});
  assert.equal(out.notified,true);
  assert.equal(out.storeDegraded,true);
  assert.equal(sent,1);
});
test("Telegram 503 must NOT persist a fake sent notification",async()=>{
  const store=memoryKV();
  await assert.rejects(deliver(critical(),activeEnv(store),NOW,
    async()=>new Response('{"ok":false}',{status:503})), /TELEGRAM_HTTP_503/);
  assert.equal(store.records.size,0);
  assert.equal((await deliver(critical(),activeEnv(store),NOW+60000,telegramOk)).notified,true);
});
test("Telegram 200 ok:false is also a failed delivery",async()=>{
  const store=memoryKV();
  await assert.rejects(deliver(critical(),activeEnv(store),NOW,
    async()=>new Response('{"ok":false}',{status:200})),/TELEGRAM_REJECTED/);
  assert.equal(store.records.size,0);
});
test("KV write failure after Telegram success permits duplicate, not silence",async()=>{
  let sends=0;
  const KV={get:async()=>null, put:async()=>{throw Error("KV down")}, delete:async()=>{}};
  for(let i=0;i<2;i++) {
    const out=await deliver(critical(),activeEnv(KV),NOW+i*60000,
      async()=>{sends++;return telegramOk()});
    assert.equal(out.notified,true);
  }
  assert.equal(sends,2);
});
test("different failed workflow run must bypass incident dedupe",async()=>{
  const store=memoryKV(),env=activeEnv(store);
  let sends=0;
  const sender=async()=>{sends++;return telegramOk()};
  assert.equal((await deliver(critical(100),env,NOW,sender)).notified,true);
  assert.equal((await deliver(critical(100),env,NOW+1000,sender)).notified,false);
  assert.equal((await deliver(critical(101),env,NOW+2000,sender)).notified,true);
  assert.equal(sends,2);
});
test("KV unreachable must NOT emit a recovery notification",async()=>{
  let sends=0;
  const KV={get:async()=>{throw Error("KV down")}, put:async()=>{},delete:async()=>{}};
  const env=activeEnv(KV);
  const ok={status:"OK",issues:[],checkedAt:stamp(2)};
  const result=await deliver(ok,env,NOW,async()=>{sends++;return telegramOk()});
  assert.equal(result.notified,false);
  assert.equal(sends,0);
});
test("corrupt GitHub public base64 classified as INDETERMINADO",async()=>{
  const fake=async url=>new Response(JSON.stringify(
    String(url).includes("/contents/")?
      {encoding:"base64",content:"!!!corrupt!!!"}:{workflow_runs:runs()}),{status:200});
  const out=await runCheck({},NOW,fake);
  assert.equal(out.report.status,"INDETERMINADO");
  assert.ok(codes(out.report).includes("STATE_BASE64_OR_JSON_INVALID"));
});
test("workflow-scoped endpoint excludes unrelated Actions runs",async()=>{
  const urls=[];
  const fake=async url=>{
    urls.push(String(url));
    const body=String(url).includes("/contents/")
      ? {encoding:"base64",content:Buffer.from(JSON.stringify(state())).toString("base64")}
      : {workflow_runs:runs()};
    return new Response(JSON.stringify(body),{status:200});
  };
  await readPublic(NOW,fake);
  assert.ok(urls.some(x=>x.includes("/actions/workflows/forex-v2.yml/runs?branch=main")));
  assert.ok(!urls.some(x=>x.includes("/actions/runs?branch=main")));
});
test("HTTP 429 upstream retried once with no false bot failure",async()=>{
  const calls=new Map();
  const fake=async url=>{
    const key=String(url),count=(calls.get(key)||0)+1;calls.set(key,count);
    if(count===1)return new Response('{"error":"rate limited"}',{status:429});
    return new Response(JSON.stringify(key.includes("/contents/")
      ?{encoding:"base64",content:Buffer.from(JSON.stringify(state())).toString("base64")}
      :{workflow_runs:runs()}),{status:200});
  };
  const out=await readPublic(NOW,fake);
  assert.equal(out.status,"OK");
  assert.deepEqual([...calls.values()].sort(),[2,2]);
});
test("older than 90min no workflow detected as critical",()=>{
  const r=assess(state(90,90),runs([90,105,120,135]),NOW);
  assert.equal(r.status,"CRITICO");
  assert.ok(codes(r).includes("SCHEDULE_MISSING"));
});
test("bad public JSON response yields INDETERMINADO not fake OK",async()=>{
  const bad=async url=>new Response(String(url).includes("/contents/")?"{bad json":"{}",{status:200});
  const out=await runCheck({},NOW,bad);
  assert.equal(out.report.status,"INDETERMINADO");
});
test("KV compare-and-set absent: concurrent runs MAY duplicate, not exactly once",async()=>{
  const store=memoryKV(),env=activeEnv(store);
  let sends=0;
  const sender=async()=>{sends++;return telegramOk()};
  await Promise.all([deliver(critical(),env,NOW,sender),deliver(critical(),env,NOW,sender)]);
  assert.ok(sends>=1);
  // KV is eventually consistent; no claim of exactly-once delivery.
});

test("unknown in-progress start cannot be misdiagnosed as a stuck process",()=>{
  const rr=runs([65,80,95,110]);
  rr[0].status="in_progress";rr[0].conclusion=null;delete rr[0].run_started_at;
  const out=assess(state(),rr,NOW);
  assert.ok(codes(out).includes("RUN_START_UNKNOWN"));
  assert.ok(!codes(out).includes("RUN_STUCK"));
});


test("heartbeat success is a POST only to Healthchecks UUID",async()=>{
  const calls=[];
  const env={MONITOR_HEARTBEAT_URL:"https://hc-ping.com/01234567-89ab-cdef-0123-456789abcdef"};
  const result=await signalHeartbeat(env,true,async(url,options)=>{
    calls.push({url,options});return new Response("",{status:200});
  });
  assert.equal(result.sent,true);
  assert.equal(result.success,true);
  assert.equal(calls.length,1);
  assert.equal(calls[0].url,env.MONITOR_HEARTBEAT_URL);
  assert.equal(calls[0].options.method,"POST");
});
test("heartbeat failure signals /fail to independent watchdog",async()=>{
  const calls=[];
  const env={MONITOR_HEARTBEAT_URL:"https://hc-ping.com/01234567-89ab-cdef-0123-456789abcdef/"};
  const result=await signalHeartbeat(env,false,async(url)=>{
    calls.push(String(url));return new Response("",{status:200});
  });
  assert.equal(result.sent,true);
  assert.equal(result.success,false);
  assert.equal(calls[0],"https://hc-ping.com/01234567-89ab-cdef-0123-456789abcdef/fail");
});
test("heartbeat URL must have fixed trusted origin, no tokens leaked",async()=>{
  const bad=[
    "http://hc-ping.com/01234567-89ab-cdef-0123-456789abcdef",
    "https://hc-ping.com.evil.invalid/01234567-89ab-cdef-0123-456789abcdef",
    "https://evil.invalid/01234567-89ab-cdef-0123-456789abcdef",
    "https://hc-ping.com/01234567-89ab-cdef-0123-456789abcdef?hack=yes",
    "https://hc-ping.com/invalid","not a url"
  ];
  for(const url of bad){
    await assert.rejects(signalHeartbeat({MONITOR_HEARTBEAT_URL:url},true,
      async()=>{throw Error("Forbidden request")}),/HEARTBEAT_URL_INVALID/);
  }
});
test("scheduled monitor observe cycle pings success after valid GitHub data",async()=>{
  const urls=[];
  const env={ALERT_MODE:"observe",MONITOR_HEARTBEAT_URL:
    "https://hc-ping.com/01234567-89ab-cdef-0123-456789abcdef"};
  const fake=async(url)=>{
    const u=String(url);urls.push(u);
    if(u.includes("hc-ping.com"))return new Response("",{status:200});
    if(u.includes("/contents/")){
      return new Response(JSON.stringify({encoding:"base64",
        content:Buffer.from(JSON.stringify(state())).toString("base64")}),{status:200});
    }
    return new Response(JSON.stringify({workflow_runs:runs()}),{status:200});
  };
  const out=await scheduledCheck(env,NOW,fake);
  assert.equal(out.report.status,"OK");
  assert.equal(urls.length,3);
  assert.ok(urls[2].endsWith("01234567-89ab-cdef-0123-456789abcdef"));
});
test("upstream GitHub failure sends /fail and fails scheduled invocation",async()=>{
  const urls=[];
  const env={ALERT_MODE:"observe",MONITOR_HEARTBEAT_URL:
    "https://hc-ping.com/01234567-89ab-cdef-0123-456789abcdef"};
  const fake=async(url)=>{
    urls.push(String(url));
    if(String(url).includes("hc-ping.com"))return new Response("",{status:200});
    return new Response("",{status:403});
  };
  await assert.rejects(scheduledCheck(env,NOW,fake),/WATCH_CYCLE_FAILED/);
  assert.ok(urls.some(u=>u.endsWith("/fail")));
});
test("active mode without heartbeat URL cannot claim monitoring is healthy",async()=>{
  await assert.rejects(scheduledCheck({ALERT_MODE:"active"},NOW,
    async()=>{throw Error("Should not contact any service")}),/WATCH_CYCLE_FAILED/);
});
test("telegram outage sends heartbeat /fail as fallback when active",async()=>{
  const store=memoryKV(),urls=[];
  const env={...activeEnv(store),MONITOR_HEARTBEAT_URL:
    "https://hc-ping.com/01234567-89ab-cdef-0123-456789abcdef"};
  const fake=async(url)=>{
    const u=String(url);urls.push(u);
    if(u.includes("hc-ping.com"))return new Response("",{status:200});
    if(u.includes("api.telegram.org"))return new Response('{"ok":false}',{status:503});
    if(u.includes("/contents/"))return new Response(JSON.stringify({encoding:"base64",
        content:Buffer.from(JSON.stringify(state(40,40,true))).toString("base64")}),{status:200});
    return new Response(JSON.stringify({workflow_runs:runs([40,55,70,85,100])}),{status:200});
  };
  await assert.rejects(scheduledCheck(env,NOW,fake),/WATCH_CYCLE_FAILED/);
  assert.ok(urls.some(u=>u.endsWith("/fail")));
  assert.equal(store.records.size,0);
});
