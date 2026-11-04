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
const FETCH_ATTEMPTS = 3;
const FETCH_TIMEOUT_MS = 10000;
const MONITOR_WORKFLOW = "forex-v2.yml";
const RECOVERY_OBSERVATION_MINUTES = 10;
const RETRY_CODES = new Set([429, 500, 502, 503, 504]);

export function parseTimestamp(when) {
  // Explicit offset is mandatory. Never use the runner's implicit local timezone.
  if(typeof when!=="string" || !/(Z|[+-][0-9]{2}:[0-9]{2})$/i.test(when))
    throw new Error("Timestamp missing explicit timezone");
  const ms=Date.parse(when);
  if(!Number.isFinite(ms)) throw new Error("Invalid UTC timestamp");
  return ms;
}
export function minutesAgo(when, now) {
  const then = parseTimestamp(when);
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
  if (state.mode !== "PAPER_ONLY" || state.pair !== "EUR/USD" ||
      state.interval !== "15min" || typeof state.open_position !== "boolean" ||
      !Number.isInteger(state.closed_trades) || state.closed_trades < 0 ||
      !Number.isInteger(state.risk_violations) || state.risk_violations < 0 ||
      !Number.isFinite(state.balance) || !Number.isFinite(state.estimated_equity)) {
    throw new Error("PUBLIC_STATE_SCHEMA_INVALID");
  }
  const issues = [];
  const flag = (severity, code, message) => issues.push({ severity, code, message });
  // Historical public dashboard self-consistency. This never reads trades
  // from the encrypted state, nor attempts to reconstruct >20 closed results.
  const lastPoint=Array.isArray(state.equity_curve)
    ? state.equity_curve[state.equity_curve.length-1] : null;
  const closedResults=Array.isArray(state.closed_results)?state.closed_results:[];
  const cashFromShortHistory=state.closed_trades>20 || (
    closedResults.every(t=>t && Number.isFinite(t.pnl)) &&
    Math.abs(state.initial_balance+
      closedResults.reduce((sum,t)=>sum+t.pnl,0)-state.balance) <
      (0.016+closedResults.length*0.006)
  );
  const historyCoherent =
    Number.isInteger(state.wins) && state.wins>=0 &&
    Number.isInteger(state.losses) && state.losses>=0 &&
    state.wins+state.losses===state.closed_trades &&
    Array.isArray(state.closed_results) &&
    state.closed_results.length===Math.min(20,state.closed_trades) &&
    cashFromShortHistory &&
    Number.isFinite(state.initial_balance) &&
    Number.isFinite(state.pnl_total) &&
    lastPoint && lastPoint.n===state.closed_trades &&
    Number.isFinite(lastPoint.balance) &&
    Math.abs(lastPoint.balance-state.balance)<0.021 &&
    Math.abs(state.initial_balance+state.pnl_total-state.estimated_equity)<0.021;
  if(!historyCoherent)
    flag("CRITICO","PUBLIC_HISTORY_INCONSISTENT",
      "Resumo público diverge em trades, resultado, saldo ou curva de capital.");
  const matching = runs.filter(r => r && r.name === BOT_NAME && r.head_branch === "main");
  // A malformed run can otherwise make cadence comparisons silently NaN.
  for(const r of matching) parseTimestamp(r.created_at);
  matching.sort((a,b) => parseTimestamp(b.created_at) - parseTimestamp(a.created_at));
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
  // updated_at can advance independently. It does not prove the PAPER
  // evaluation timestamp exists, so the fallback must not hide its absence.
  if (!state.last_checked_at)
    flag("CRITICO","LAST_CHECK_MISSING",
      "Timestamp de avaliação PAPER ausente; updated_at não comprova avaliação.");
  // An Actions run can report success even though its PAPER job did not
  // publish a new public state. Avoid false positives while the GitHub commit
  // is propagating: allow at least five minutes after completed run metadata.
  if (latest && latest.status==="completed" && latest.conclusion==="success" &&
      latest.event==="schedule" && checked) {
    const completedAt=latest.updated_at || latest.created_at;
    const propagationAge=minutesAgo(completedAt, now);
    const afterRunStart=parseTimestamp(checked)-parseTimestamp(latest.created_at);
    if (propagationAge>5 && afterRunStart < -2*60000)
      flag(opened?"CRITICO":"ATENCAO","SUCCESS_WITHOUT_NEW_STATE",
        "GitHub reportou sucesso, mas não há avaliação PAPER posterior ao início da execução.");
  }
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
  if (state.risk_violations > 0)
    flag("CRITICO", "RISK_VIOLATIONS", "Histórico público indica violação do risco planejado.");
  if (state.status === "api_error")
    flag(opened || (stateAge !== null && stateAge > 30) ? "CRITICO" : "ATENCAO",
         "API_ERROR", "Estado público registra erro da API.");
  if (state.status === "awaiting_api_key")
    flag("CRITICO", "API_CREDENTIAL_MISSING",
         "Motor PAPER informa ausência da credencial da fonte de dados.");
  else if (state.status === "market_closed_or_stale" && !closed && !sundayGrace(now))
    flag(opened ? "CRITICO" : "ATENCAO","MARKET_DATA_NOT_FRESH",
         "Motor informou preço de mercado não atualizado durante sessão regular.");
  else if (!["monitoring","market_closed_or_stale","awaiting_api_key",
               "api_error","data_gap","gap_halt"].includes(state.status))
    flag("ATENCAO","UNRECOGNIZED_BOT_STATUS","Estado público apresenta status não reconhecido.");
  const schedules = matching.filter(x => x.event === "schedule");
  const gaps = [];
  for (let i=0; i<Math.min(4, schedules.length-1); i++)
    gaps.push(Math.round((Date.parse(schedules[i].created_at)-Date.parse(schedules[i+1].created_at))/6000)/10);
  if (!gaps.length) flag("ATENCAO", "INSUFFICIENT_SCHEDULE_HISTORY", "Histórico insuficiente.");
  else if (Math.max(...gaps)>30) flag("ATENCAO", "CADENCE_DELAY", "Intervalo entre execuções agendadas >30 min.");
  const recentFailedRun=matching.slice(1,5).find(x =>
    FAILURE.has(x.conclusion) && minutesAgo(x.created_at,now)<90);
  if(recentFailedRun)
    flag("ATENCAO", "RECENT_FAILURE", "Workflow teve falha recente.");
  const status = issues.some(i => i.severity === "CRITICO") ? "CRITICO" :
    (issues.length ? "ATENCAO" : "OK");
  return {status, issues, lastRunAgeMin:runAge, stateAgeMin:stateAge,
    candleAgeMin:candleAge, marketClosedWeekend:closed, scheduleGapsMin:gaps,
    openPosition:opened, side:opened?state.position_side:null,
    balance:state.balance, equity:state.estimated_equity,
    closedTrades:state.closed_trades, riskViolations:state.risk_violations,
    wins:state.wins, losses:state.losses,
    maxClosedDrawdownPct:state.max_closed_drawdown_pct,
    // A corrupted closed_results is a history inconsistency, not an
    // unhandled exception converting a known anomaly into INDETERMINADO.
    // runner.py emits closed_results in REVERSE chronological order:
    // index 0 is the newest closed trade, not the oldest.
    lastClosedTradeKey:closedResults.length && closedResults[0] &&
      typeof closedResults[0]==="object" && !Array.isArray(closedResults[0])?
      JSON.stringify(["at","side","pnl","reason"].map(k=>closedResults[0][k]??null)):null,
    checkedAt:checked, candleAt:state.last_candle_at,
    lastRunId:latest?.id || null, recentFailedRunId:recentFailedRun?.id || null,
    source:"PUBLIC_GITHUB_ONLY"};
}
async function getJson(url, fetcher=fetch, pause=(ms)=>new Promise(resolve=>setTimeout(resolve,ms)), token=null) {
  const resource=url.includes("/contents/")?"STATE":"RUNS";
  for(let n=0;n<FETCH_ATTEMPTS;n++){
    const controller=new AbortController();
    const timer=setTimeout(()=>controller.abort(),FETCH_TIMEOUT_MS);
    let retryDelay=1000*Math.pow(2,n);
    try {
      const headers={"Accept":"application/vnd.github+json",
        "User-Agent":"Forex-Watch-V2.3.2","Cache-Control":"no-cache"};
      if(token) headers.Authorization="Bearer "+token;
      const response=await fetcher(url,{headers,cache:"no-store",signal:controller.signal});
      if(response.ok){
        const contentLength=Number(response.headers?.get?.("content-length"));
        if(Number.isFinite(contentLength) && contentLength>MAX_API_BYTES)
          throw new Error("GITHUB_"+resource+"_RESPONSE_TOO_LARGE");
        let body;
        if(response.body?.getReader){
          const reader=response.body.getReader();
          const chunks=[]; let size=0;
          try {
            while(true){
              const {done,value}=await reader.read();
              if(done) break;
              size+=value.byteLength;
              if(size>MAX_API_BYTES) throw new Error("GITHUB_"+resource+"_RESPONSE_TOO_LARGE");
              chunks.push(value);
            }
          } finally {await reader.cancel().catch(()=>{});}
          const all=new Uint8Array(size);let offset=0;
          for(const chunk of chunks){all.set(chunk,offset);offset+=chunk.byteLength;}
          body=new TextDecoder().decode(all);
        } else {
          body=await response.text();
          if(body.length>MAX_API_BYTES) throw new Error("GITHUB_"+resource+"_RESPONSE_TOO_LARGE");
        }
        try{return JSON.parse(body);}
        catch {throw new Error("GITHUB_"+resource+"_INVALID_JSON");}
      }
      const remaining=response.headers?.get?.("x-ratelimit-remaining");
      const retryAfter=response.headers?.get?.("retry-after");
      const reset=response.headers?.get?.("x-ratelimit-reset");
      // Read at most 2048 bytes for classification. Never log or forward the body.
      let hint="";
      if(response.status===403){
        try {
          // Read at most 2048 bytes, rather than downloading an unbounded error body.
          if(response.body?.getReader){
            const reader=response.body.getReader();
            const chunks=[]; let bytes=0;
            try {
              while(bytes<2048){
                const {done,value}=await reader.read();
                if(done) break;
                const chunk=value.subarray(0,2048-bytes);
                chunks.push(chunk); bytes+=chunk.length;
              }
            } finally {await reader.cancel().catch(()=>{});}
            const combined=new Uint8Array(bytes);let offset=0;
            for(const chunk of chunks){combined.set(chunk,offset);offset+=chunk.length;}
            hint=new TextDecoder().decode(combined).toLowerCase();
          } else hint=(await response.text()).slice(0,2048).toLowerCase();
        } catch {}
      }
      const secondary=/secondary rate limit|abuse detection|secondary-rate-limit|api rate limit exceeded|rate limit exceeded/.test(hint);
      const limited=(response.status===403 || response.status===429) &&
        (remaining==="0" || retryAfter!==null && retryAfter!==undefined || secondary || response.status===429);
      const code="GITHUB_"+resource+"_HTTP_"+response.status+
        (limited?"_RATE_LIMIT":token && response.status===403?"_POSSIBLE_PERMISSION":"");
      if(!limited && !RETRY_CODES.has(response.status)) throw new Error(code);
      if(n===FETCH_ATTEMPTS-1) throw new Error(code);
      // Respect GitHub Retry-After and reset; never retry early if wait exceeds
      // our bounded 10-second budget. The next cron cycle can try again.
      if(retryAfter!==null && retryAfter!==undefined){
        const seconds=Number(retryAfter);
        const date=Date.parse(retryAfter);
        const wait=Number.isFinite(seconds)?seconds*1000:Number.isFinite(date)?date-Date.now():NaN;
        if(!Number.isFinite(wait) || wait>10000) throw new Error(code);
        retryDelay=Math.max(retryDelay,Math.max(0,wait));
      } else if(remaining==="0" && reset!==null && reset!==undefined){
        const wait=Number(reset)*1000-Date.now();
        if(!Number.isFinite(wait) || wait>10000) throw new Error(code);
        retryDelay=Math.max(retryDelay,Math.max(0,wait));
      }
      await pause(retryDelay);
      continue;
    } catch(error){
      const msg=String(error?.message||"");
      const code=error?.name==="AbortError"?"GITHUB_"+resource+"_TIMEOUT":
        /^GITHUB_/.test(msg)?msg:"GITHUB_"+resource+"_NETWORK_ERROR";
      if(n===FETCH_ATTEMPTS-1 || /HTTP_(401|404)|HTTP_403(?!_RATE_LIMIT)|INVALID_JSON|RESPONSE_TOO_LARGE/.test(code))
        throw new Error(code);
      await pause(retryDelay);
    } finally {clearTimeout(timer);}
  }
  throw new Error("GITHUB_"+resource+"_NETWORK_ERROR");
}
export async function readPublic(now=Date.now(), fetcher=fetch, token=null) {
  const results = await Promise.allSettled([
    // Public raw-content fallback avoids the GitHub REST API's stricter
    // unauthenticated rate limit. It is read-only and never uses credentials.
    (async()=>{
      try {
        return await getJson(ENDPOINT+"/contents/v2/state.json?ref=main",fetcher,undefined,token);
      } catch(error) {
        if(!/^GITHUB_STATE_HTTP_(403|429)/.test(String(error?.message||""))) throw error;
        const rawUrl="https://raw.githubusercontent.com/"+GITHUB_REPO+"/main/v2/state.json";
        // Bound the fallback independently; a hung raw endpoint must not
        // prevent the monitor from reaching its fail-closed state.
        const fallbackController=new AbortController();
        const fallbackTimer=setTimeout(()=>fallbackController.abort(),FETCH_TIMEOUT_MS);
        let body;
        try {
          const response=await fetcher(rawUrl,{headers:{"Accept":"application/json",
            "User-Agent":"Forex-Watch-V2.3.3"},cache:"no-store",redirect:"error",
            signal:fallbackController.signal});
          if(!response.ok) throw error; // preserve the REST diagnostic
          const length=Number(response.headers?.get?.("content-length"));
          if(Number.isFinite(length)&&length>MAX_API_BYTES)
            throw new Error("GITHUB_STATE_RESPONSE_TOO_LARGE");
          if(response.body?.getReader){
            const reader=response.body.getReader();
            const chunks=[];let size=0;
            try {
              while(true){
                const {done,value}=await reader.read();
                if(done) break;
                size+=value.byteLength;
                if(size>MAX_API_BYTES) throw new Error("GITHUB_STATE_RESPONSE_TOO_LARGE");
                chunks.push(value);
              }
            } finally {await reader.cancel().catch(()=>{});}
            const all=new Uint8Array(size);let offset=0;
            for(const chunk of chunks){all.set(chunk,offset);offset+=chunk.byteLength;}
            body=new TextDecoder().decode(all);
          } else body=await response.text();
        } catch(fallbackError) {
          if(fallbackError?.name==="AbortError")
            throw new Error("GITHUB_STATE_RAW_TIMEOUT");
          throw fallbackError;
        } finally {clearTimeout(fallbackTimer);}

        if(body.length>MAX_API_BYTES) throw new Error("GITHUB_STATE_RESPONSE_TOO_LARGE");
        let parsed;
        try { parsed=JSON.parse(body); } catch { throw new Error("GITHUB_STATE_INVALID_JSON"); }
        return {encoding:"raw_json",content:parsed};
      }
    })(),
    getJson(ENDPOINT+"/actions/workflows/"+MONITOR_WORKFLOW+"/runs?branch=main&per_page=20",fetcher,undefined,token),
  ]);
  const failed=results.find(x=>x.status==="rejected");
  if(failed) throw failed.reason;
  const [raw,data]=results.map(x=>x.value);
  if (!((raw.encoding === "base64" && typeof raw.content === "string") ||
        (raw.encoding === "raw_json" && raw.content && typeof raw.content === "object")))
    throw new Error("GitHub state file format invalid");
  let state;
  try {
    if(raw.encoding === "raw_json") state = raw.content;
    else {
    const binary = atob(raw.content.replace(/\s/g,""));
    const decoded = new Uint8Array(binary.length);
    for (let i=0;i<binary.length;i++) decoded[i]=binary.charCodeAt(i);
    state = JSON.parse(new TextDecoder().decode(decoded));
    }
  } catch(error) {
    throw new Error("STATE_BASE64_OR_JSON_INVALID");
  }
  if (!Array.isArray(data.workflow_runs)) throw new Error("Missing GitHub workflow_runs");
  return assess(state,data.workflow_runs,now);
}
function incidentFingerprint(report) {
  const codes=(report.issues||[]).map(x=>x.code).sort();
  const runSpecific=codes.some(x=>["RUN_FAILED","RUN_STUCK","QUEUE_STUCK"].includes(x));
  const latestId=runSpecific && report.lastRunId?"|run:"+report.lastRunId:"";
  const recentId=codes.includes("RECENT_FAILURE") && report.recentFailedRunId?
    "|recentFailed:"+report.recentFailedRunId:"";
  const riskCount=codes.includes("RISK_VIOLATIONS")?
    "|riskViolations:"+String(report.riskViolations??"unknown"):"";
  return report.status+"|"+codes.join(",")+latestId+recentId+riskCount;
}
function alertText(report) {
  const url = "https://github.com/" + GITHUB_REPO + "/actions";
  const event = report.status === "INDETERMINADO"
    ? "Falha de consulta ao GitHub; situação do bot NÃO confirmada.\nMotivo: "+
      String(report.issues?.[0]?.code||"MONITOR_UNAVAILABLE")
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
    let storeDegraded=false;
    try { await env.INCIDENT_KV.delete(key); }
    catch(error) {
      storeDegraded=true;
      console.error("INCIDENT_STORE_DELETE_ERROR");
    }
    // The recovery notification was accepted, but failed KV cleanup can
    // cause duplicate recovery notifications on the next cycle.
    return {notified:true,recovery:true,storeDegraded};
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
  let kvWritable=true;
  try {
    await env.INCIDENT_KV.put(key,JSON.stringify({
      fingerprint,status:report.status,notifiedAt:now,
      healthySince:0,healthyCheckAt:null}),{expirationTtl:172800});
  } catch(error) {
    kvWritable=false;
    console.error("INCIDENT_STORE_WRITE_ERROR");
  }
  return {notified:true,recovery:false,storeDegraded:!kvReadable || !kvWritable};
}
/**
 * Detect a public-state rollback relative to the last seen valid observation.
 * This does NOT access encrypted state. KV is eventually consistent, so this
 * provides a best-effort audit signal, not an atomic history guarantee.
 * Never update the baseline with regressed data.
 */
export async function assessContinuity(report,env,now=Date.now()) {
  if(env.ALERT_MODE!=="active" || report.status==="INDETERMINADO")
    return report;
  const baselineKey="forex-watch-v23-public-history";
  if(!env.INCIDENT_KV) return report; // deliver() rejects incomplete active config
  try {
    const raw=await env.INCIDENT_KV.get(baselineKey);
    const previous=raw?JSON.parse(raw):null;
    const regressed=[];
    if(previous) {
      if(Number.isInteger(previous.closedTrades) &&
         report.closedTrades < previous.closedTrades) regressed.push("closed_trades");
      if(Number.isInteger(previous.riskViolations) &&
         report.riskViolations < previous.riskViolations) regressed.push("risk_violations");
      if(Number.isInteger(previous.wins) && report.wins < previous.wins)
        regressed.push("wins");
      if(Number.isInteger(previous.losses) && report.losses < previous.losses)
        regressed.push("losses");
      if(Number.isFinite(previous.maxClosedDrawdownPct) &&
         report.maxClosedDrawdownPct+0.011 < previous.maxClosedDrawdownPct)
        regressed.push("closed_drawdown");
      if(previous.closedTrades===report.closedTrades) {
        if(Number.isFinite(previous.balance) &&
           Math.abs(report.balance-previous.balance)>0.011)
          regressed.push("balance_changed_without_closed_trade");
        if(previous.lastClosedTradeKey && report.lastClosedTradeKey &&
           previous.lastClosedTradeKey!==report.lastClosedTradeKey)
          regressed.push("last_closed_trade_rewritten");
      }
      if(previous.candleAt && report.candleAt &&
         parseTimestamp(report.candleAt)<parseTimestamp(previous.candleAt))
        regressed.push("last_candle_at");
      if(previous.checkedAt && report.checkedAt &&
         parseTimestamp(report.checkedAt)<parseTimestamp(previous.checkedAt))
        regressed.push("last_checked_at");
    }
    if(regressed.length) {
      report.issues.push({severity:"CRITICO",code:"HISTORY_REGRESSION_SUSPECTED",
        message:"Possível regressão do histórico público: "+regressed.join(", ")});
      report.status="CRITICO";
    } else if(report.checkedAt && report.candleAt &&
              Number.isInteger(report.closedTrades) &&
              !report.issues.some(i=>i.code==="PUBLIC_HISTORY_INCONSISTENT")) {
      // Never poison the continuity baseline with a self-inconsistent public
      // snapshot. The next repaired snapshot must compare to last trusted one.
      // Only store non-regressed observations. Trade balance can change and is
      // deliberately not expected to be monotonic.
      await env.INCIDENT_KV.put(baselineKey,JSON.stringify({
        checkedAt:report.checkedAt,candleAt:report.candleAt,
        closedTrades:report.closedTrades,balance:report.balance,
        wins:report.wins,losses:report.losses,
        maxClosedDrawdownPct:report.maxClosedDrawdownPct,
        riskViolations:report.riskViolations,
        lastClosedTradeKey:report.lastClosedTradeKey,
        observedAt:now}),{expirationTtl:604800});
    }
  } catch(error) {
    report.issues.push({severity:"ATENCAO",code:"HISTORY_CONTINUITY_UNAVAILABLE",
      message:"Não foi possível confirmar continuidade do histórico público."});
    if(report.status==="OK") report.status="ATENCAO";
    report.continuityDegraded=true;
    console.error("PUBLIC_HISTORY_MONITOR_UNAVAILABLE");
  }
  return report;
}
export async function runCheck(env, now=Date.now(), fetcher=fetch) {
  let report;
  try {
    report=await readPublic(now,fetcher,env?.GITHUB_READ_TOKEN||null);
    await assessContinuity(report,env,now);
  } catch(error) {
    const rawCode=String(error?.message||"");
    const code=/^(GITHUB_(STATE|RUNS)_(HTTP_[0-9]{3}(?:_RATE_LIMIT|_POSSIBLE_PERMISSION)?|RESPONSE_TOO_LARGE|INVALID_JSON|TIMEOUT|NETWORK_ERROR)|STATE_BASE64_OR_JSON_INVALID)$/.test(rawCode)
      ? rawCode:rawCode==="GitHub state file format invalid"?"STATE_FILE_FORMAT_INVALID":
        rawCode==="Missing GitHub workflow_runs"?"GITHUB_RUNS_MALFORMED":"MONITOR_UNAVAILABLE";
    // Log only a controlled error code, never an HTTP body or credential.
    console.error("WATCH_GITHUB_READ_FAILED",code);
    report={status:"INDETERMINADO",issues:[{
      code,severity:"INDETERMINADO",
      message:"Consulta GitHub falhou ("+code+"); situação do bot NÃO confirmada."}],
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
  const parts=parsed.pathname.split("/").filter(Boolean);
  const validUuid=parts.length===1 &&
    /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/.test(parts[0]);
  if(parsed.protocol!=="https:" || parsed.hostname!=="hc-ping.com" ||
     parsed.username || parsed.password || parsed.port || parsed.search || parsed.hash || !validUuid) {
    throw new Error("HEARTBEAT_URL_INVALID");
  }
  const base=parsed.href.endsWith("/")?parsed.href.slice(0,-1):parsed.href;
  const target=base+(success?"":"/fail");
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
    if(!["active","observe"].includes(env.ALERT_MODE))
      throw new Error("ALERT_MODE_INVALID");
    if(env.ALERT_MODE==="active" && !env.MONITOR_HEARTBEAT_URL)
      throw new Error("HEARTBEAT_NOT_CONFIGURED");
    const result=await runCheck(env,now,fetcher);
    report=result.report;
    notification=result.delivery;
    if(report.continuityDegraded || notification?.storeDegraded ||
       notification?.reason==="kv_unavailable_recovery_blocked")
      throw new Error("MONITOR_PERSISTENCE_DEGRADED");
    if(report.status==="INDETERMINADO")
      throw new Error("MONITOR_DATA_UNAVAILABLE");
  } catch(error) {
    failure=error;
    console.error("WATCH_CYCLE_FAILED",String(error?.message||"Unknown error").slice(0,160));
  }
  try {
    // OBSERVE is intentionally NOT operational monitoring. Do not ping the
    // independent dead-man switch when Telegram alerts are disabled.
    if (env.ALERT_MODE==="active") {
      const ping=await signalHeartbeat(env,!failure,fetcher);
      if(!ping.sent) throw new Error("HEARTBEAT_NOT_CONFIGURED");
    }
  } catch(error) {
    if(!failure) failure=error;
    console.error("HEARTBEAT_DELIVERY_FAILED",String(error?.message||"Unknown error").slice(0,100));
  }
  if(failure) throw new Error("WATCH_CYCLE_FAILED");
  return {report,notification};
}
export default {
  async scheduled(controller,env,ctx) {
    // Use ACTUAL invocation time. Cron scheduledTime is the nominal trigger time:
    // delayed dispatch must not make the bot appear younger/healthier.
    ctx.waitUntil(scheduledCheck(env,Date.now()));
  },
  async fetch() {
    // No public endpoint exposing health / Telegram tokens.
    return new Response("Forex Watch V2.3: scheduled checks only", {status:404});
  }
};
