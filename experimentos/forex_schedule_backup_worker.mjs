/**
 * Optional isolated Cloudflare backup for GitHub's best-effort Forex PAPER cron.
 * SAFETY: no deployment or activation from this repository. Disabled by default.
 * Only workflow_dispatch on the fixed PAPER_ONLY workflow/ref is allowed.
 * Never touches brokerage APIs, state.enc, GitHub contents or the Forex Watch.
 */
const REPO="veloraspaces0/forex-bot-pro-v1-demo";
const WORKFLOW="forex-v2.yml";
const RAW_HEARTBEAT="https://raw.githubusercontent.com/"+REPO+"/main/v2/heartbeat.json";
const RUNS_API="https://api.github.com/repos/"+REPO+"/actions/workflows/"+WORKFLOW+"/runs?branch=main&per_page=10";
const DISPATCH_API="https://api.github.com/repos/"+REPO+"/actions/workflows/"+WORKFLOW+"/dispatches";
const WAIT_FOR_HEARTBEAT_MS=35*60*1000;
const RECENT_RUN_MS=28*60*1000;
const REQUEST_TIMEOUT_MS=8000;
const MAX_JSON_BYTES=500_000;

function isoAge(value,now) {
  if(typeof value!=="string" || !/(Z|[+-]\d{2}:\d{2})$/i.test(value))
    throw new Error("TIMESTAMP_UNTRUSTED");
  const t=Date.parse(value);
  if(!Number.isFinite(t) || t-now>2*60*1000) throw new Error("TIMESTAMP_UNTRUSTED");
  return Math.max(0,now-t);
}
async function request(url,token,fetcher,method="GET",body=null,parseJson=false){
  const controller=new AbortController();
  const timer=setTimeout(()=>controller.abort(),REQUEST_TIMEOUT_MS);
  const headers={"Accept":"application/vnd.github+json","User-Agent":"Forex-Paper-Backup-Scheduler"};
  if(token)headers.Authorization="Bearer "+token;
  if(body!==null)headers["Content-Type"]="application/json";
  try {
    const response=await fetcher(url,{method,headers,cache:"no-store",signal:controller.signal,
      ...(body===null?{}:{body:JSON.stringify(body)})});
    return parseJson?await readJson(response):response;
  } finally { clearTimeout(timer); }
}
async function readJson(response){
  if(!response.ok) throw new Error("UPSTREAM_HTTP_"+response.status);
  const len=response.headers?.get?.("content-length");
  if(len!==null && len!==undefined && Number(len)>MAX_JSON_BYTES)
    throw new Error("UPSTREAM_RESPONSE_TOO_LARGE");
  let value;
  if(response.body?.getReader) {
    const reader=response.body.getReader(),chunks=[];
    let size=0;
    try {
      while(true){
        const part=await reader.read();
        if(part.done)break;
        size+=part.value.byteLength;
        if(size>MAX_JSON_BYTES) throw new Error("UPSTREAM_RESPONSE_TOO_LARGE");
        chunks.push(part.value);
      }
    } finally { await reader.cancel().catch(()=>{}); }
    const bytes=new Uint8Array(size);let index=0;
    for(const chunk of chunks){bytes.set(chunk,index);index+=chunk.length;}
    value=new TextDecoder().decode(bytes);
  } else {
    value=await response.text();
    if(value.length>MAX_JSON_BYTES)throw new Error("UPSTREAM_RESPONSE_TOO_LARGE");
  }
  try {return JSON.parse(value);}
  catch { throw new Error("UPSTREAM_INVALID_JSON"); }
}
export async function backupCheck(env,now=Date.now(),fetcher=fetch) {
  // Intentional opt-in. No token or KV read, no GitHub call, no dispatch.
  if(env.BACKUP_SCHEDULER_MODE!=="active")return {action:"OBSERVE_ONLY"};
  if(!env.GITHUB_DISPATCH_TOKEN || !env.BACKUP_DISPATCH_GUARD)
    throw new Error("BACKUP_CONFIG_INCOMPLETE");

  const heartbeat=await request(RAW_HEARTBEAT,null,fetcher,"GET",null,true);
  if(heartbeat.schema_version!==1 || heartbeat.mode!=="PAPER_ONLY" ||
    heartbeat.status!=="SUCCESS" || !/^\d+$/.test(String(heartbeat.run_id||"")))
    throw new Error("HEARTBEAT_INVALID");
  const age=isoAge(heartbeat.completed_at,now);
  if(age<=WAIT_FOR_HEARTBEAT_MS) return {action:"SKIP_RECENT_HEARTBEAT"};

  // The independent Durable Object reserves atomically AFTER the read-only
  // eligibility checks and BEFORE any non-idempotent GitHub dispatch.
  // Never release the reservation after an ambiguous upstream failure.
  const runs=await request(RUNS_API,env.GITHUB_DISPATCH_TOKEN,fetcher,"GET",null,true);
  if(!Array.isArray(runs.workflow_runs))throw new Error("RUNS_RESPONSE_INVALID");
  const main=runs.workflow_runs.filter(run=>
    run?.head_branch==="main" && run?.name==="Forex Bot Pro V2 - Paper EURUSD");
  if(!main.length) throw new Error("RUNS_HISTORY_INSUFFICIENT");
  // A currently queued/running job is a reason NOT to trigger another one.
  if(main.some(run=>["queued","waiting","in_progress","requested","pending"].includes(run.status)))
    return {action:"SKIP_BOT_ALREADY_RUNNING"};
  // A recently scheduled/successful/failed run should get its own chance to
  // publish the heartbeat before we create another workflow run.
  if(main.some(run=>isoAge(run.created_at,now)<=RECENT_RUN_MS))
    return {action:"SKIP_RECENT_GITHUB_RUN"};
  // Only one backup invocation per 40-minute window may cross this point.
  // This does not control GitHub's separate native schedule; keep the fixed
  // PAPER job concurrency group and last_bar-based idempotency on main.
  let granted;
  try {
    const gate=env.BACKUP_DISPATCH_GUARD.getByName("forex-v21-main");
    granted=await gate.reserveDispatch();
  } catch {throw new Error("COORDINATOR_UNAVAILABLE");}
  if(typeof granted!=="boolean")throw new Error("COORDINATOR_INVALID_RESPONSE");
  if(!granted)return {action:"SKIP_ATOMIC_COOLDOWN"};
  const result=await request(DISPATCH_API,env.GITHUB_DISPATCH_TOKEN,fetcher,
    "POST",{ref:"main"});
  if(result.status!==204)throw new Error("DISPATCH_HTTP_"+result.status);
  return {action:"BACKUP_DISPATCHED",workflow:WORKFLOW,ref:"main"};
}
export default {
  async scheduled(_controller,env,ctx) {
    ctx.waitUntil(backupCheck(env,Date.now()).then(r=>{
      console.log("FOREX_BACKUP_SCHEDULER",r.action);
    }).catch(e=>{
      // Never log secret values, URLs with credentials, or external bodies.
      const message=String(e?.message||"BACKUP_UNKNOWN_ERROR");
      console.error("FOREX_BACKUP_SCHEDULER_ERROR",
        /^(UPSTREAM_HTTP_\d+|UPSTREAM_RESPONSE_TOO_LARGE|UPSTREAM_INVALID_JSON|HEARTBEAT_INVALID|TIMESTAMP_UNTRUSTED|BACKUP_CONFIG_INCOMPLETE|COORDINATOR_UNAVAILABLE|COORDINATOR_INVALID_RESPONSE|RUNS_RESPONSE_INVALID|RUNS_HISTORY_INSUFFICIENT|DISPATCH_HTTP_\d+)$/.test(message)
          ? message:"BACKUP_UNKNOWN_ERROR");
      throw new Error("BACKUP_CYCLE_FAILED");
    }));
  },
  async fetch() {return new Response("Not found",{status:404});}
};
