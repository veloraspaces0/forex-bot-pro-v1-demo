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
      if (minutesAgo(latest.run_started_at || latest.created_at, now) > 20)
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
  let lastError;
  for (let n=0;n<3;n++) {
    try {
      const controller = new AbortController();
      const timeout = setTimeout(()=>controller.abort(), 8000);
      let response;
      try {
        response = await fetcher(url,{headers:{"Accept":"application/vnd.github+json",
          "User-Agent":"Forex-Watch-V2.3","Cache-Control":"no-cache"},
          cache:"no-store",signal:controller.signal});
      } finally { clearTimeout(timeout); }
      if (!response.ok) {
        const error = new Error("GitHub API HTTP " + response.status);
        if (!RETRY_CODES.has(response.status) || n === 2) throw error;
        lastError = error;
      } else {
        const body = await response.text();
        if (body.length>MAX_API_BYTES) throw new Error("GitHub response too large");
        return JSON.parse(body);
      }
    } catch (error) {
      lastError = error;
      if (n === 2 || /HTTP (401|403|404)/.test(String(error.message))) throw error;
    }
    await pause(2 ** n * 1000);
  }
  throw lastError || new Error("GitHub API unavailable");
}
export async function readPublic(now=Date.now(), fetcher=fetch) {
  const [raw, data] = await Promise.all([
    getJson(ENDPOINT+"/contents/v2/state.json?ref=main",fetcher),
    getJson(ENDPOINT+"/actions/runs?branch=main&per_page=100",fetcher),
  ]);
  if (raw.encoding !== "base64" || typeof raw.content !== "string")
    throw new Error("GitHub state file format invalid");
  const binary = atob(raw.content.replace(/\s/g,""));
  const decoded = new Uint8Array(binary.length);
  for (let i=0;i<binary.length;i++) decoded[i]=binary.charCodeAt(i);
  const state = JSON.parse(new TextDecoder().decode(decoded));
  if (!Array.isArray(data.workflow_runs)) throw new Error("Missing GitHub workflow_runs");
  return assess(state,data.workflow_runs,now);
}
function incidentFingerprint(report) {
  return report.status + "|" + (report.issues||[]).map(x=>x.code).sort().join(",");
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
async function sendTelegram(env, message, fetcher=fetch) {
  if (!env.TELEGRAM_BOT_TOKEN || !env.TELEGRAM_CHAT_ID) return false;
  const url = "https://api.telegram.org/bot"+env.TELEGRAM_BOT_TOKEN+"/sendMessage";
  const response = await fetcher(url,{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({chat_id:env.TELEGRAM_CHAT_ID,text:message,disable_web_page_preview:true})});
  if (!response.ok) throw new Error("Notification failed; HTTP "+response.status);
  return true;
}
export async function deliver(report, env, now=Date.now(), fetcher=fetch) {
  // No KV -> only logs, NEVER spam Telegram on every cron tick.
  if (!env.INCIDENT_KV || !env.TELEGRAM_CHAT_ID || !env.TELEGRAM_BOT_TOKEN)
    return {notified:false,reason:"KV and Telegram not configured; log only"};
  const key = "forex-watch-v23-incident";
  const oldRaw = await env.INCIDENT_KV.get(key);
  const previous = oldRaw ? JSON.parse(oldRaw) : null;
  if (report.status === "OK") {
    if (!previous) return {notified:false,reason:"healthy"};
    await sendTelegram(env,"Forex Watch V2.3 — RECUPERADO\nMonitor sem novas anomalias.",fetcher);
    await env.INCIDENT_KV.delete(key);
    return {notified:true,recovery:true};
  }
  const fingerprint=incidentFingerprint(report);
  const escalation = previous && previous.status !== "CRITICO" && report.status === "CRITICO";
  const repeat = previous && now - previous.notifiedAt > 4*60*60*1000;
  const isNew = !previous || previous.fingerprint !== fingerprint;
  if (!isNew && !escalation && !repeat) return {notified:false,reason:"deduplicated"};
  await sendTelegram(env,alertText(report),fetcher);
  await env.INCIDENT_KV.put(key,JSON.stringify({fingerprint,status:report.status,notifiedAt:now}),
    {expirationTtl:172800});
  return {notified:true,recovery:false};
}
export async function runCheck(env, now=Date.now(), fetcher=fetch) {
  let report;
  try {
    report=await readPublic(now,fetcher);
  } catch(error) {
    report={status:"INDETERMINADO",issues:[{
      code:"MONITOR_UNAVAILABLE",severity:"INDETERMINADO",
      message:"API GitHub indisponível ou resposta inválida; NÃO inferir falha do bot."}],
      checkedAt:null,openPosition:null,balance:null};
  }
  console.log(JSON.stringify({monitor:"Forex Watch V2.3",status:report.status,
    codes:report.issues.map(x=>x.code),checkedAt:report.checkedAt??null}));
  const delivery=await deliver(report,env,now,fetcher);
  return {report,delivery};
}
export default {
  async scheduled(controller,env,ctx) {
    ctx.waitUntil(runCheck(env,controller.scheduledTime||Date.now()));
  },
  async fetch() {
    // No public endpoint exposing health / Telegram tokens.
    return new Response("Forex Watch V2.3: scheduled checks only", {status:404});
  }
};
