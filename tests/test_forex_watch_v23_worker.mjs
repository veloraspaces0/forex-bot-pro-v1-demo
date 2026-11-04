import test from "node:test";
import assert from "node:assert/strict";
import {assess,assessContinuity,fxMarketClosed,sundayGrace,minutesAgo,deliver,runCheck,readPublic,signalHeartbeat,scheduledCheck}
  from "../experimentos/forex_watch_v23_worker.mjs";

const NOW=Date.parse("2026-11-04T16:00:00Z");
const stamp=(mins,now=NOW)=>new Date(now-mins*60000).toISOString();
const state=(updated=4,candle=20,opened=false,now=NOW,extra={})=>({
  mode:"PAPER_ONLY",pair:"EUR/USD",interval:"15min",risk_violations:0,
  last_checked_at:stamp(updated,now),updated_at:stamp(updated,now),
  last_candle_at:stamp(candle,now),open_position:opened,position_side:opened?"SELL":null,
  status:"monitoring",gap_halt:false,balance:99.5,estimated_equity:99.45,
  initial_balance:100,pnl_total:-0.55,
  closed_trades:1,wins:0,losses:1,
  closed_results:[{at:"2026-11-04T07:30:00Z",side:"SELL",pnl:-0.5}],
  equity_curve:[{n:0,balance:100},{n:1,balance:99.5}],...extra
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
test("observe mode performs diagnostics only and NEVER asserts heartbeat",async()=>{
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
  assert.equal(urls.length,2);
  assert.ok(urls.every(u=>!u.includes("hc-ping.com")));
});
test("active mode healthy monitor sends heartbeat only after GitHub check",async()=>{
  const urls=[];
  const env={...activeEnv(memoryKV()),MONITOR_HEARTBEAT_URL:
    "https://hc-ping.com/01234567-89ab-cdef-0123-456789abcdef"};
  const fake=async(url)=>{
    const u=String(url);urls.push(u);
    if(u.includes("hc-ping.com")) return new Response("",{status:200});
    return new Response(JSON.stringify(u.includes("/contents/")
      ?{encoding:"base64",content:Buffer.from(JSON.stringify(state())).toString("base64")}
      :{workflow_runs:runs()}),{status:200});
  };
  const out=await scheduledCheck(env,NOW,fake);
  assert.equal(out.report.status,"OK");
  assert.equal(urls.length,3);
  assert.ok(urls[2].startsWith("https://hc-ping.com/"));
});
test("upstream GitHub failure sends /fail and fails scheduled invocation",async()=>{
  const urls=[];
  const env={...activeEnv(memoryKV()),MONITOR_HEARTBEAT_URL:
    "https://hc-ping.com/01234567-89ab-cdef-0123-456789abcdef"};
  const fake=async(url)=>{
    urls.push(String(url));
    if(String(url).includes("hc-ping.com"))return new Response("",{status:200});
    if(String(url).includes("api.telegram.org"))
      return new Response('{"ok":true}',{status:200});
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
  // A public-history observation can be written before Telegram fails,
  // but the incident must NEVER be marked as delivered.
  assert.ok(!store.records.has("forex-watch-v23-incident"));
});

test("public dashboard history self-consistency detects counter and PnL corruption",()=>{
  const cases=[
    {closed_trades:2},{wins:2},{losses:2},{closed_results:[]},
    {equity_curve:[{n:0,balance:100}]},
    {equity_curve:[{n:1,balance:95}]},
    {pnl_total:-9.0}
  ];
  for(const changed of cases) {
    const out=assess(state(4,20,false,NOW,changed),runs(),NOW);
    assert.equal(out.status,"CRITICO");
    assert.ok(codes(out).includes("PUBLIC_HISTORY_INCONSISTENT"),JSON.stringify(changed));
  }
});

test("public risk violations always require a critical alarm",()=>{
  const r=assess(state(4,20,false,NOW,{risk_violations:1}),runs(),NOW);
  assert.equal(r.status,"CRITICO");
  assert.ok(codes(r).includes("RISK_VIOLATIONS"));
});
test("invalid or missing public state schema must fail closed",()=>{
  for(const overrides of [
    {mode:null},{pair:"GBP/USD"},{interval:null},{open_position:"false"},
    {risk_violations:-1},{balance:Number.NaN},{closed_trades:-2}
  ]) {
    assert.throws(()=>assess(state(4,20,false,NOW,overrides),runs(),NOW),
      /PUBLIC_STATE_SCHEMA_INVALID/);
  }
});
test("a late Cron scheduledTime must use the actual invocation clock",async()=>{
  // Simulate dispatch 50 minutes late, without any HTTP/network call.
  const module=(await import("../experimentos/forex_watch_v23_worker.mjs")).default;
  const realFetch=globalThis.fetch;
  const now=Date.now();
  const calls=[];
  const fake=async(url)=>{
    const path=String(url);calls.push(path);
    return new Response(JSON.stringify(path.includes("/contents/")
      ?{encoding:"base64",content:Buffer.from(JSON.stringify(state(4,20,false,now))).toString("base64")}
      :{workflow_runs:runs([4,19,34,49,64],now)}),{status:200});
  };
  globalThis.fetch=fake;
  let task=null;
  try {
    await module.scheduled({scheduledTime:now-50*60000},{ALERT_MODE:"observe"},
      {waitUntil:promise=>{task=promise}});
    assert.ok(task);
    const result=await task;
    assert.equal(result.report.status,"OK",
      "Nominal scheduled time cannot make a fresh reading appear stale or younger");
    assert.equal(calls.length,2);
  } finally { globalThis.fetch=realFetch; }
});

test("UTC or explicit offset mandatory for dates from external APIs",()=>{
  for(const malformed of ["2026-11-04T12:00:00","2026-11-04","not a time",null]) {
    assert.throws(()=>minutesAgo(malformed,NOW));
  }
  assert.equal(minutesAgo("2026-11-04T13:50:00-02:00",NOW),10);
});
test("malformed middle GitHub workflow timestamp cannot quietly pass green",()=>{
  const rr=runs();
  rr[3].created_at="2026-11-04T15:10:00";
  assert.throws(()=>assess(state(),rr,NOW),/Timestamp missing explicit timezone/);
});

test("historical closed-trade rollback or candle regression is CRITICO",async()=>{
  const store=memoryKV(),env=activeEnv(store);
  const first=assess(state(),runs(),NOW);
  await assessContinuity(first,env,NOW);
  assert.equal(first.status,"OK");
  const later=NOW+15*60000;
  const decreased=assess(state(4,50,false,later,{
    closed_trades:0, balance:99.50,estimated_equity:99.45}),
    runs([4,19,34,49,64],later),later);
  await assessContinuity(decreased,env,later);
  assert.equal(decreased.status,"CRITICO");
  assert.ok(codes(decreased).includes("HISTORY_REGRESSION_SUSPECTED"));
  const stored=JSON.parse(store.records.get("forex-watch-v23-public-history"));
  assert.equal(stored.closedTrades,1,"Do not overwrite good baseline with regressed public history");
});
test("floating paper equity is not mistaken for a history regression",async()=>{
  const store=memoryKV(),env=activeEnv(store);
  const first=assess(state(),runs(),NOW);
  await assessContinuity(first,env,NOW);
  const later=NOW+15*60000;
  const next=assess(state(4,20,false,later,{
    balance:99.50,estimated_equity:97.01}),
    runs([4,19,34,49,64],later),later);
  await assessContinuity(next,env,later);
  assert.ok(!codes(next).includes("HISTORY_REGRESSION_SUSPECTED"));
});
test("KV outage during continuity is surfaced as degraded",async()=>{
  const env=activeEnv({get:async()=>{throw Error("KV down")}});
  const report=assess(state(),runs(),NOW);
  await assessContinuity(report,env,NOW);
  assert.equal(report.continuityDegraded,true);
  assert.ok(codes(report).includes("HISTORY_CONTINUITY_UNAVAILABLE"));
  assert.equal(report.status,"ATENCAO");
});
test("KV outage sends Telegram warning and /fail heartbeat",async()=>{
  const addresses=[];
  const KV={
    get:async()=>{throw Error("KV offline")},
    put:async()=>{throw Error("KV offline")},
    delete:async()=>{throw Error("KV offline")}
  };
  const env={...activeEnv(KV),MONITOR_HEARTBEAT_URL:
    "https://hc-ping.com/01234567-89ab-cdef-0123-456789abcdef"};
  const fake=async(url)=>{
    const u=String(url);addresses.push(u);
    if(u.includes("hc-ping.com"))return new Response("",{status:200});
    if(u.includes("api.telegram.org"))return new Response('{"ok":true}',{status:200});
    if(u.includes("/contents/"))return new Response(JSON.stringify({
      encoding:"base64",content:Buffer.from(JSON.stringify(state())).toString("base64")
    }),{status:200});
    return new Response(JSON.stringify({workflow_runs:runs()}),{status:200});
  };
  await assert.rejects(scheduledCheck(env,NOW,fake),/WATCH_CYCLE_FAILED/);
  assert.ok(addresses.some(x=>x.includes("api.telegram.org")),"Failure should reach primary alert channel");
  assert.ok(addresses.some(x=>x.endsWith("/fail")),"Independent dead-man switch must signal degraded persistence");
});
test("missing or invalid ALERT_MODE is rejected, not silently observe",async()=>{
  for(const mode of [null,"observ","ACTIVE",""]) {
    const env={ALERT_MODE:mode};
    let called=0;
    await assert.rejects(scheduledCheck(env,NOW,async()=>{called++;throw Error("No network allowed")}),
      /WATCH_CYCLE_FAILED/);
    assert.equal(called,0);
  }
});


test("fresh GitHub workflow but missing Twelve Data key must be CRITICO",()=>{
  const result=assess(state(4,20,false,NOW,{status:"awaiting_api_key"}),runs(),NOW);
  assert.equal(result.status,"CRITICO");
  assert.ok(codes(result).includes("API_CREDENTIAL_MISSING"));
});
test("market_closed_or_stale while Forex session open with position must be CRITICO",()=>{
  const result=assess(state(4,20,true,NOW,{status:"market_closed_or_stale"}),runs(),NOW);
  assert.equal(result.status,"CRITICO");
  assert.ok(codes(result).includes("MARKET_DATA_NOT_FRESH"));
});
test("market_closed_or_stale on Saturday is not a reason to panic",()=>{
  const sat=Date.parse("2026-11-07T16:00:00Z");
  const result=assess(state(4,2000,false,sat,{status:"market_closed_or_stale"}),runs([4,19,34,49,64],sat),sat);
  assert.ok(!codes(result).includes("MARKET_DATA_NOT_FRESH"));
});
test("arbitrary unexpected engine status is not falsely green",()=>{
  const result=assess(state(4,20,false,NOW,{status:"internal_error"}),runs(),NOW);
  assert.equal(result.status,"ATENCAO");
  assert.ok(codes(result).includes("UNRECOGNIZED_BOT_STATUS"));
});


test("GitHub success cannot mask a skipped PAPER job with no new public state",()=>{
  const rr=runs([12,27,42,57,72]);
  rr[0].updated_at=stamp(10);
  const out=assess(state(20,20,true),rr,NOW);
  assert.equal(out.status,"CRITICO");
  assert.ok(codes(out).includes("SUCCESS_WITHOUT_NEW_STATE"));
});
test("GitHub commit propagation grace prevents premature false positive",()=>{
  const rr=runs([12,27,42,57,72]);
  rr[0].updated_at=stamp(1);
  const out=assess(state(20,20,true),rr,NOW);
  assert.ok(!codes(out).includes("SUCCESS_WITHOUT_NEW_STATE"));
});
test("fresh PAPER state after run is not considered skipped",()=>{
  const rr=runs([12,27,42,57,72]);
  rr[0].updated_at=stamp(9);
  const out=assess(state(4,20,true),rr,NOW);
  assert.ok(!codes(out).includes("SUCCESS_WITHOUT_NEW_STATE"));
});


test("public cash must reconcile with complete closed_results history",()=>{
  const broken=state(4,20,false,NOW,{balance:101,
    equity_curve:[{n:0,balance:100},{n:1,balance:101}]});
  const out=assess(broken,runs(),NOW);
  assert.equal(out.status,"CRITICO");
  assert.ok(codes(out).includes("PUBLIC_HISTORY_INCONSISTENT"));
});
test("same count but rewritten closed trade identity is detected across observations",async()=>{
  const store=memoryKV(),env=activeEnv(store);
  const initial=assess(state(),runs(),NOW);
  await assessContinuity(initial,env,NOW);
  const later=NOW+15*60000;
  const fake=state(4,20,false,later,{
    closed_results:[{at:"2026-11-04T07:30:00Z",side:"SELL",pnl:-0.5,reason:"REWRITTEN"}]
  });
  const current=assess(fake,runs([4,19,34,49,64],later),later);
  assert.equal(current.status,"OK","Static self-consistency cannot see historical edit alone");
  await assessContinuity(current,env,later);
  assert.equal(current.status,"CRITICO");
  assert.ok(codes(current).includes("HISTORY_REGRESSION_SUSPECTED"));
});
test("active floating paper equity may fluctuate without closing or rewriting trades",async()=>{
  const store=memoryKV(),env=activeEnv(store);
  await assessContinuity(assess(state(4,20,true),runs(),NOW),env,NOW);
  const later=NOW+15*60000;
  const next=assess(state(4,20,true,later,{
    estimated_equity:99.01,pnl_total:-0.99}),runs([4,19,34,49,64],later),later);
  await assessContinuity(next,env,later);
  assert.ok(!codes(next).includes("HISTORY_REGRESSION_SUSPECTED"));
});


test("self-inconsistent public state must NEVER poison trusted continuity baseline",async()=>{
  const store=memoryKV(),env=activeEnv(store),key="forex-watch-v23-public-history";
  const initial=assess(state(),runs(),NOW);
  await assessContinuity(initial,env,NOW);
  const trusted=store.records.get(key);
  const later=NOW+15*60000;
  const corrupt=assess(state(4,20,false,later,{closed_trades:2}),
    runs([4,19,34,49,64],later),later);
  assert.ok(codes(corrupt).includes("PUBLIC_HISTORY_INCONSISTENT"));
  await assessContinuity(corrupt,env,later);
  assert.equal(store.records.get(key),trusted,"Must retain last internally consistent history");
  const repairedAt=NOW+30*60000;
  const repaired=assess(state(4,20,false,repairedAt),
    runs([4,19,34,49,64],repairedAt),repairedAt);
  await assessContinuity(repaired,env,repairedAt);
  assert.ok(!codes(repaired).includes("HISTORY_REGRESSION_SUSPECTED"));
});

test("malformed public closed_results must be a history alarm, not unreadable state",()=>{
  for(const corrupted of [null,[null],["invalid"],[{pnl:"not-a-number"}]]) {
    const output=assess(state(4,20,false,NOW,{closed_results:corrupted}),runs(),NOW);
    assert.equal(output.status,"CRITICO");
    assert.ok(codes(output).includes("PUBLIC_HISTORY_INCONSISTENT"));
  }
});
test("fresh updated_at cannot hide missing PAPER last_checked_at",()=>{
  const output=assess(state(4,20,false,NOW,{last_checked_at:null}),runs(),NOW);
  assert.equal(output.status,"CRITICO");
  assert.ok(codes(output).includes("LAST_CHECK_MISSING"));
});
test("recovery KV deletion failure is visible even when Telegram accepted recovery",async()=>{
  const store=memoryKV();
  const env=activeEnv({
    get:store.get,put:store.put,
    delete:async()=>{throw Error("KV delete unavailable")}
  });
  const first={status:"CRITICO",issues:[{severity:"CRITICO",code:"DATA_GAP",message:"gap"}],
    checkedAt:stamp(4),openPosition:true};
  assert.equal((await deliver(first,env,NOW,telegramOk)).notified,true);
  const healthy1={status:"OK",issues:[],checkedAt:stamp(-29)};
  const healthy2={status:"OK",issues:[],checkedAt:stamp(-44)};
  assert.equal((await deliver(healthy1,env,NOW+30*60000,telegramOk)).notified,false);
  const result=await deliver(healthy2,env,NOW+45*60000,telegramOk);
  assert.equal(result.recovery,true);
  assert.equal(result.storeDegraded,true);
  assert.ok(store.records.has("forex-watch-v23-incident"));
});


test("two different recent failures bypass dedupe despite the same alert code",async()=>{
  const store=memoryKV(),env=activeEnv(store);
  let sends=0;
  const sender=async()=>{sends++;return telegramOk()};
  const rr=runs();rr[1].conclusion="failure";rr[1].id=901;
  const initial=assess(state(),rr,NOW);
  assert.equal(initial.recentFailedRunId,901);
  assert.ok(codes(initial).includes("RECENT_FAILURE"));
  assert.equal((await deliver(initial,env,NOW,sender)).notified,true);
  assert.equal((await deliver(initial,env,NOW+1000,sender)).notified,false);
  const next={...initial,recentFailedRunId:902};
  assert.equal((await deliver(next,env,NOW+2000,sender)).notified,true);
  assert.equal(sends,2);
});
test("new increments of risk_violations are not silently deduplicated",async()=>{
  const store=memoryKV(),env=activeEnv(store);
  let sends=0;
  const sender=async()=>{sends++;return telegramOk()};
  const report=assess(state(4,20,false,NOW,{risk_violations:1}),runs(),NOW);
  assert.ok(codes(report).includes("RISK_VIOLATIONS"));
  await deliver(report,env,NOW,sender);
  assert.equal((await deliver(report,env,NOW+500,sender)).notified,false);
  assert.equal((await deliver({...report,riskViolations:2},env,NOW+1000,sender)).notified,true);
  assert.equal(sends,2);
});
