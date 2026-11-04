/**
 * Forex Watch V2.3 Cloudflare Worker — independent, read-only external monitor.
 * No trading, no encrypted state, no GitHub writes. Telegram + KV optional.
 * Cloudflare Cron Trigger is best effort; this is alerting, not stop-loss execution.
 */
export const GITHUB_REPO = "veloraspaces0/forex-bot-pro-v1-demo";
export const BOT_NAME = "Forex Bot Pro V2 - Paper EURUSD";
const ENDPOINT = "https://api.github.com/repos/" + GITHUB_REPO;
const FAILURE = new Set(["failure", "cancelled", "timed_out", "action_required", "startup_failure"]);
const MAX_API_BYTES = 2_000_000;
const FETCH_ATTEMPTS = 2;
const FETCH_TIMEOUT_MS = 5000;
const MONITOR_WORKFLOW = "forex-v2.yml";
const RECOVERY_OBSERVATION_MINUTES = 10;
const RETRY_CODES = new Set([429, 500, 502, 503, 504]);

export function minutesAgo(when, now) {
  const then = Date.parse(when);
  if (!Number.isFinite(then)) throw new Error("Invalid UTC timestamp");
  const age = (now - then) / 60000;
  if (age < -2) throw new Error("Future timestamp");
  return Math.max(0, Math.round(age * 100) / 100);
}
export function nyClock(now) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York", weekday: "short", hour: "2-digit",
    minute: "2-digit", hourCycle: "h23"
  }).formatToParts(new Date(now));
  return Object.fromEntries(parts.map(p => [p.type, p.value]));
}
export function fxMarketClosed(now) {
  const p = nyClock(now);
  const hour = Number(p.hour);
  return (p.weekday === "Fri" && hour >= 17) || p.weekday === "Sat" ||
    (p.weekday === "Sun" && hour < 17);
}
export function sundayGrace(now) {
  const p = nyClock(now);
  return p.weekday === "Sun" && Number(p.hour) === 17 && Number(p.minute) < 45;
}
export function assess(state, runs, now) {
  if (!state || typeof state !== "object" || !Array.isArray(runs)) throw new Error("Bad public JSON");
  if (state.mode && state.mode !== "PAPER_ONLY") throw new Error("Non-paper state");
  const issues = [];
  const flag = (severity, code, message) => issues.push({ severity, code, message });
  const matching = runs.filter(r => r && r.name === BOT_NAME && r.head_branch === "main")
    .sort((a,b) => Date.parse(b.created_at) - Date.parse(a.created_at));
  const latest = matching[0];
  const opened = state.open_position === true;
  let runAge = null;
  if (!latest) flag("CRITICO", "NO_BOT_RUNS", "Nenhuma execução do robô encontrada.");
  else {
    runAge = minutesAgo(latest.created_at, now);
    if (["queued", "waiting"].includes(latest.status)) {
      if (runAge > 20) flag("CRITICO", "QUEUE_STUCK", "Execução parada na fila.");
    } else if (latest.status === "in_progress") {
      if (!latest.run_started_at)
        flag(runAge>75?"CRITICO":"ATENCAO","RUN_START_UNKNOWN",
          "Sem run_started_at: duração real da execução é desconhecida.");
      else if (minutesAgo(latest.run_started_at, now) > 20)
        flag("CRITICO", "RUN_STUCK", "Execução travada em andamento.");
    } else if (latest.status === "completed") {
      if (FAILURE.has(latest.conclusion))
        flag("CRITICO", "RUN_FAILED", "Workflow terminou sem sucesso.");
      else if (latest.conclusion !== "success")
        flag("ATENCAO", "RUN_UNKNOWN", "Conclusão não reconhecida.");
    } else flag("ATENCAO", "RUN_UNKNOWN", "Estado do workflow não reconhecido.");
    if (!["queued", "waiting", "in_progress"].includes(latest.status)) {
      if (runAge > 75) flag("CRITICO", "SCHEDULE_MISSING", "Sem execução recente por mais de 75 min.");
      else if (runAge > 30) flag("ATENCAO", "SCHEDULE_DELAY", "Última execução começou há mais de 30 min.");
    }
  }
  const checked = state.last_checked_at || state.updated_at;
  const stateAge = checked ? minutesAgo(checked, now) : null;
  if (stateAge === null) flag("CRITICO", "MISSING_CHECK", "Sem horário do estado.");
  else if (opened && stateAge > 35) flag("CRITICO", "POSITION_UNCHECKED", "Posição paper sem avaliação >35 min.");
  else if (stateAge > 75) flag("CRITICO", "STATE_STALE", "Estado sem atualização >75 min.");
  else if (stateAge > 30) flag("ATENCAO", "STATE_DELAY", "Estado sem atualização >30 min.");
  const closed = fxMarketClosed(now);
  const candleAge = state.last_candle_at ? minutesAgo(state.last_candle_at, now) : null;
  if (!closed && !sundayGrace(now)) {
    if (candleAge === null) flag("CRITICO", "CANDLE_MISSING", "Sem candle registrado na sessão aberta.");
    else if (candleAge > 75) flag("CRITICO", "CANDLE_STALE", "Candle obsoleto >75 min.");
    else if (opened && candleAge > 35) flag("CRITICO", "POSITION_CANDLE_STALE", "Posição paper com candle obsoleto.");
    else if (candleAge > 35) flag("ATENCAO", "CANDLE_DELAY", "Candle sem avanço >35 min.");
  }
  if (state.gap_halt || ["data_gap", "gap_halt"].includes(state.status))
    flag("CRITICO", "DATA_GAP", "Parada por lacuna de candles.");
  if (state.status === "api_error")
    flag(opened || (stateAge !== null && stateAge > 30) ? "CRITICO" : "ATENCAO",
         "API_ERROR", "Estado público registra erro da API.");
  const schedules = matching.filter(x => x.event === "schedule");
  const gaps = [];
  for (let i=0; i<Math.min(4, schedules.length-1); i++)
    gaps.push(Math.round((Date.parse(schedules[i].created_at)-Date.parse(schedules[i+1].created_at))/6000)/10);
  if (!gaps.length) flag("ATENCAO", "INSUFFICIENT_SCHEDULE_HISTORY", "Histórico insuficiente.");
  else if (Math.max(...gaps)>30) flag("ATENCAO", "CADENCE_DELAY", "Intervalo entre execuções agendadas >30 min.");
  if (matching.slice(1,5).some(x => FAILURE.has(x.conclusion) && minutesAgo(x.created_at,now)<90))
    flag("ATENCAO", "RECENT_FAILURE", "Workflow teve falha recente.");
  const status = issues.some(i => i.severity === "CRITICO") ? "CRITICO" :
    (issues.length ? "ATENCAO" : "OK");
  return {status, issues, lastRunAgeMin:runAge, stateAgeMin:stateAge,
    candleAgeMin:candleAge, marketClosedWeekend:closed, scheduleGapsMin:gaps,
    openPosition:opened, side:opened?state.position_side:null,
    balance:state.balance, equity:state.estimated_equity,
    checkedAt:checked, candleAt:state.last_candle_at,
    lastRunId:latest?.id || null, source:"PUBLIC_GITHUB_ONLY"};
}
async function getJson(url, fetcher=fetch, pause=(ms)=>new Promise(resolve=>setTimeout(resolve,ms))) {
  for (let n=0; n<FETCH_ATTEMPTS; n++) {
    const controller = new AbortController();
    const timer = setTimeout(()=>controller.abort(),FETCH_TIMEOUT_MS);
    try {
      const response = await fetcher(url,{headers:{"Accept":"application/vnd.github+json",
        "User-Agent":"Forex-Watch-V2.3","Cache-Control":"no-cache"},
        cache:"no-store",signal:controller.signal});
      if (!response.ok) {
        const err = new Error("GitHub API HTTP "+response.status);
        if (!RETRY_CODES.has(response.status) || n===FETCH_ATTEMPTS-1) throw err;
      } else {
        const body=await response.text();
        if (body.length>MAX_API_BYTES) throw new Error("GitHub response too large");
        return JSON.parse(body);
      }
    } catch(error) {
      if (n===FETCH_ATTEMPTS-1 || /HTTP (401|403|404)/.test(String(error.message))) throw error;
    } finally { clearTimeout(timer); }
    await pause(1000);
  }
  throw new Error("GitHub API unavailable");
}
export async function readPublic(now=Date.now(), fetcher=fetch) {
  const [raw, data] = await Promise.all([
    getJson(ENDPOINT+"/contents/v2/state.json?ref=main",fetcher),
    getJson(ENDPOINT+"/actions/workflows/"+MONITOR_WORKFLOW+"/runs?branch=main&per_page=100",fetcher),
  ]);
  if (raw.encoding !== "base64" || typeof raw.content !== "string")
    throw new Error("GitHub state file format invalid");
  let state;
  try {
    const binary = atob(raw.content.replace(/\s/g,""));
    const decoded = new Uint8Array(binary.length);
    for (let i=0;i<binary.length;i++) decoded[i]=binary.charCodeAt(i);
    state = JSON.parse(new TextDecoder().decode(decoded));
  } catch(error) {
    throw new Error("STATE_BASE64_OR_JSON_INVALID");
  }
  if (!Array.isArray(data.workflow_runs)) throw new Error("Missing GitHub workflow_runs");
  return assess(state,data.workflow_runs,now);
}
function incidentFingerprint(report) {
  const codes=(report.issues||[]).map(x=>x.code).sort();
  const runSpecific=codes.some(x=>["RUN_FAILED","RUN_STUCK","QUEUE_STUCK"].includes(x));
  return report.status+"|"+codes.join(",")+(runSpecific && report.lastRunId?"|run:"+report.lastRunId:"");
}
function alertText(report) {
  const url = "https://github.com/" + GITHUB_REPO + "/actions";
  const event = report.status === "INDETERMINADO"
    ? "Falha de consulta ao GitHub; situação do bot NÃO confirmada."
    : report.issues.map(x=>x.code + ": " + x.message).join("\n");
  return ["Forex Watch V2.3 — "+report.status,event,
    "Posição paper: "+String(report.openPosition??"desconhecida"),
    "Saldo virtual: "+String(report.balance??"não disponível"),
    "Estado verificado: "+String(report.checkedAt??"não disponível"),url].join("\n").slice(0,3600);
}
async function sendTelegram(env,message,fetcher=fetch) {
  if (!env.TELEGRAM_BOT_TOKEN || !env.TELEGRAM_CHAT_ID)
    throw new Error("ALERT_CHANNEL_NOT_CONFIGURED");
  const url="https://api.telegram.org/bot"+env.TELEGRAM_BOT_TOKEN+"/sendMessage";
  const controller=new AbortController();
  const timer=setTimeout(()=>controller.abort(),FETCH_TIMEOUT_MS);
  try {
    const response=await fetcher(url,{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({chat_id:env.TELEGRAM_CHAT_ID,text:message,disable_web_page_preview:true}),
      signal:controller.signal});
    if (!response.ok) throw new Error("TELEGRAM_HTTP_"+response.status);
    let data;
    try { data=await response.json(); } catch(error) { throw new Error("TELEGRAM_INVALID_RESPONSE"); }
    if (!data || data.ok!==true) throw new Error("TELEGRAM_REJECTED");
    return true;
  } finally { clearTimeout(timer); }
}
export async function deliver(report,env,now=Date.now(),fetcher=fetch) {
  // An opt-in release gate. A worker in observe mode only logs diagnostics.
  if (env.ALERT_MODE!=="active") return {notified:false,reason:"observe_only"};
  const missing=["INCIDENT_KV","TELEGRAM_BOT_TOKEN","TELEGRAM_CHAT_ID"]
    .filter(key=>!env[key]);
  if(missing.length) throw new Error("ALERT_CHANNEL_NOT_CONFIGURED:"+missing.join(","));
  const key="forex-watch-v23-incident";
  let previous=null,kvReadable=true;
  try {
    const oldRaw=await env.INCIDENT_KV.get(key);
    previous=oldRaw?JSON.parse(oldRaw):null;
  } catch(error) {
    kvReadable=false;
    console.error("INCIDENT_STORE_READ_ERROR");
  }
  if(report.status==="OK") {
    // Never say recovered when prior incident state cannot be read.
    if(!kvReadable) return {notified:false,reason:"kv_unavailable_recovery_blocked"};
    if(!previous) return {notified:false,reason:"healthy"};
    // Require two distinct healthy observations, with a fresh public state.
    if(!report.checkedAt) return {notified:false,reason:"healthy_observation_missing"};
    const first=previous.healthySince||0;
    const distinct=now-first>=RECOVERY_OBSERVATION_MINUTES*60*1000 &&
      previous.healthyCheckAt!==report.checkedAt;
    if(!first || !distinct) {
      // Do not restart the clock on identical observations; keep the original.
      if(!first) {
        try {
          await env.INCIDENT_KV.put(key,JSON.stringify({...previous,
            healthySince:now,healthyCheckAt:report.checkedAt}),{expirationTtl:172800});
        } catch(error) {
          console.error("INCIDENT_STORE_WRITE_ERROR");
          return {notified:false,reason:"kv_unavailable_recovery_blocked"};
        }
      }
      return {notified:false,reason:"recovery_pending_second_check"};
    }
    await sendTelegram(env,"Forex Watch V2.3 — RECUPERADO\nDuas verificações saudáveis distintas.",fetcher);
    try { await env.INCIDENT_KV.delete(key); }
    catch(error) { console.error("INCIDENT_STORE_DELETE_ERROR"); }
    return {notified:true,recovery:true};
  }
  const fingerprint=incidentFingerprint(report);
  const repeat=previous && now-previous.notifiedAt>4*60*60*1000;
  const isNew=!previous || previous.fingerprint!==fingerprint;
  if(kvReadable && !isNew && !repeat) {
    if(previous.healthySince) {
      // Incident reappeared during recovery; reset its recovery confirmation.
      try { await env.INCIDENT_KV.put(key,JSON.stringify({...previous,
        healthySince:0,healthyCheckAt:null}),{expirationTtl:172800}); }
      catch(error) { console.error("INCIDENT_STORE_WRITE_ERROR"); }
    }
    return {notified:false,reason:"deduplicated"};
  }
  // Alert FIRST; never persist "notified" if Telegram did not accept it.
  await sendTelegram(env,alertText(report),fetcher);
  try {
    await env.INCIDENT_KV.put(key,JSON.stringify({
      fingerprint,status:report.status,notifiedAt:now,
      healthySince:0,healthyCheckAt:null}),{expirationTtl:172800});
  } catch(error) { console.error("INCIDENT_STORE_WRITE_ERROR"); }
  return {notified:true,recovery:false,storeDegraded:!kvReadable};
}
export async function runCheck(env, now=Date.now(), fetcher=fetch) {
  let report;
  try {
    report=await readPublic(now,fetcher);
  } catch(error) {
    const code=String(error.message).includes("STATE_BASE64_OR_JSON_INVALID")?
      "STATE_BASE64_OR_JSON_INVALID":"MONITOR_UNAVAILABLE";
    report={status:"INDETERMINADO",issues:[{
      code,severity:"INDETERMINADO",
      message:"API GitHub indisponível ou resposta inválida; NÃO inferir falha do bot."}],
      checkedAt:null,openPosition:null,balance:null};
  }
  console.log(JSON.stringify({monitor:"Forex Watch V2.3",status:report.status,
    codes:report.issues.map(x=>x.code),checkedAt:report.checkedAt??null}));
  const delivery=await deliver(report,env,now,fetcher);
  return {report,delivery};
}
/**
 * Ping an independent Healthchecks.io dead-man's-switch. The URL is a secret:
 * NEVER log it, never publish it in the repository or HTTP responses.
 * A successful ping proves THIS monitor completed its cycle; not that trading
 * is profitable, risk controlled, or the underlying EUR/USD feed is fresh.
 */
export async function signalHeartbeat(env,success,fetcher=fetch) {
  const secret=env.MONITOR_HEARTBEAT_URL;
  if(!secret) return {sent:false,reason:"heartbeat_not_configured"};
  let parsed;
  try { parsed=new URL(secret); }
  catch { throw new Error("HEARTBEAT_URL_INVALID"); }
  if(parsed.protocol!=="https:" || parsed.hostname!=="hc-ping.com" ||
     parsed.username || parsed.password || parsed.port || parsed.search || parsed.hash ||
     !/^\\/[0-9a-fA-F-]{36}\\/?$/.test(parsed.pathname)) {
    throw new Error("HEARTBEAT_URL_INVALID");
  }
  const target=parsed.href.replace(/\\/$/,"")+(success?"":"/fail");
  const controller=new AbortController();
  const timer=setTimeout(()=>controller.abort(),FETCH_TIMEOUT_MS);
  try {
    const response=await fetcher(target,{method:"POST",cache:"no-store",
      headers:{"User-Agent":"Forex-Watch-V2.3-heartbeat"},signal:controller.signal});
    if(!response.ok) throw new Error("HEARTBEAT_HTTP_"+response.status);
    return {sent:true,success};
  } finally { clearTimeout(timer); }
}
/**
 * A cycle must signal /fail when an alert could not be delivered or the public
 * sources were unreachable. In active mode heartbeat configuration is required.
 * Exposed for deterministic offline testing only, never via public HTTP.
 */
export async function scheduledCheck(env,now=Date.now(),fetcher=fetch) {
  let failure=null,report=null,notification=null;
  try {
    if(env.ALERT_MODE==="active" && !env.MONITOR_HEARTBEAT_URL)
      throw new Error("HEARTBEAT_NOT_CONFIGURED");
    const result=await runCheck(env,now,fetcher);
    report=result.report;
    notification=result.delivery;
    if(report.status==="INDETERMINADO")
      throw new Error("MONITOR_DATA_UNAVAILABLE");
  } catch(error) {
    failure=error;
    console.error("WATCH_CYCLE_FAILED",String(error?.message||"Unknown error").slice(0,160));
  }
  try {
    const ping=await signalHeartbeat(env,!failure,fetcher);
    if(!ping.sent && env.ALERT_MODE==="active")
      throw new Error("HEARTBEAT_NOT_CONFIGURED");
  } catch(error) {
    if(!failure) failure=error;
    console.error("HEARTBEAT_DELIVERY_FAILED",String(error?.message||"Unknown error").slice(0,100));
  }
  if(failure) throw new Error("WATCH_CYCLE_FAILED");
  return {report,notification};
}
export default {
  async scheduled(controller,env,ctx) {
    ctx.waitUntil(scheduledCheck(env,controller.scheduledTime||Date.now()));
  },
  async fetch() {
    // No public endpoint exposing health / Telegram tokens.
    return new Response("Forex Watch V2.3: scheduled checks only", {status:404});
  }
};
