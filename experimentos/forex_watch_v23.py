#!/usr/bin/env python3
"""Forex Watch V2.3 — read-only, deterministic monitor of PUBLIC GitHub metadata.

Never reads encrypted state, never trades, never writes to the repository.
This local CLI is complementary to the independent Cloudflare Worker prototype.
"""
import argparse
import base64
import json
import math
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

REPOSITORY = "veloraspaces0/forex-bot-pro-v1-demo"
WORKFLOW_NAME = "Forex Bot Pro V2 - Paper EURUSD"
STATE_ENDPOINT = "contents/v2/state.json?ref=main"
RUNS_ENDPOINT = "actions/workflows/forex-v2.yml/runs?branch=main&per_page=100"
TIMEOUT_SEC = 12
MAX_RESPONSE_BYTES = 2_000_000
NY = ZoneInfo("America/New_York")

def utc(value):
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("datetime sem fuso horário")
        return value.astimezone(timezone.utc)
    if not isinstance(value, str) or not value:
        raise ValueError("timestamp ausente/inválido")
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timestamp sem fuso horário")
    return dt.astimezone(timezone.utc)

def age_min(value, now):
    diff = (utc(now) - utc(value)).total_seconds() / 60.0
    if diff < -2:
        raise ValueError("timestamp mais de 2 minutos no futuro")
    return round(max(0.0, diff), 2)

def market_closed(now):
    """Approximate regular FX weekend: Fri 17:00 → Sun 17:00 New York.

    Holidays, extraordinary closures and broker-specific gaps are not inferred.
    """
    ny = utc(now).astimezone(NY)
    day = ny.weekday()  # Monday=0, Friday=4, Sunday=6
    return (day == 4 and ny.hour >= 17) or day == 5 or (day == 6 and ny.hour < 17)

def sunday_open_grace(now):
    ny = utc(now).astimezone(NY)
    return ny.weekday() == 6 and ny.hour == 17 and ny.minute < 45

def assess(state, runs, now):
    """Pure assessment, intentionally no I/O, no state mutation."""
    now = utc(now)
    if not isinstance(state, dict) or not isinstance(runs, list):
        raise ValueError("resposta JSON com estrutura inválida")
    if state.get("mode") != "PAPER_ONLY" or state.get("pair") != "EUR/USD" or state.get("interval") != "15min":
        raise ValueError("PUBLIC_STATE_SCHEMA_INVALID")
    if type(state.get("open_position")) is not bool:
        raise ValueError("PUBLIC_STATE_SCHEMA_INVALID")
    if type(state.get("closed_trades")) is not int or state["closed_trades"] < 0:
        raise ValueError("PUBLIC_STATE_SCHEMA_INVALID")
    if type(state.get("risk_violations")) is not int or state["risk_violations"] < 0:
        raise ValueError("PUBLIC_STATE_SCHEMA_INVALID")
    if any(type(state.get(k)) not in (float, int) or not math.isfinite(state[k])
           for k in ("balance", "estimated_equity")):
        raise ValueError("PUBLIC_STATE_SCHEMA_INVALID")
    issues = []
    def flag(severity, code, message, **details):
        issues.append({"severity": severity, "code": code, "message": message, **details})
    bot = [r for r in runs if isinstance(r, dict) and r.get("name") == WORKFLOW_NAME and r.get("head_branch") == "main"]
    bot.sort(key=lambda r: utc(r.get("created_at")), reverse=True)
    last = bot[0] if bot else None
    last_run_age = age_min(last["created_at"], now) if last else None
    if last is None:
        flag("CRITICO", "NO_BOT_RUNS", "Nenhuma execução do robô encontrada na amostra.")
    else:
        run_status = last.get("status")
        conclusion = last.get("conclusion")
        if run_status == "queued" or run_status == "waiting":
            if last_run_age > 20:
                flag("CRITICO", "QUEUE_STUCK", "Execução presa na fila do GitHub.", age_min=last_run_age)
        elif run_status == "in_progress":
            started = last.get("run_started_at")
            if not started:
                flag("CRITICO" if last_run_age > 75 else "ATENCAO",
                     "RUN_START_UNKNOWN",
                     "Execução em andamento sem run_started_at: duração real desconhecida.",
                     run_created_age_min=last_run_age)
            else:
                elapsed = age_min(started, now)
                if elapsed > 20:
                    flag("CRITICO", "RUN_STUCK", "Execução em andamento além do limite esperado.", age_min=elapsed)
        elif run_status == "completed":
            if conclusion in {"failure", "cancelled", "timed_out", "action_required", "startup_failure"}:
                flag("CRITICO", "RUN_FAILED", "Última execução terminou sem sucesso.", conclusion=conclusion)
            elif conclusion != "success":
                flag("ATENCAO", "RUN_UNKNOWN", "Execução concluída com resultado não reconhecido.", conclusion=conclusion)
        else:
            flag("ATENCAO", "RUN_UNKNOWN", "Status do workflow desconhecido.", workflow_status=run_status)
        if last_run_age > 75 and run_status not in {"queued", "waiting", "in_progress"}:
            flag("CRITICO", "SCHEDULE_MISSING", "Nenhum início recente do robô (mais de 75 min).", age_min=last_run_age)
        elif last_run_age > 30 and run_status not in {"queued", "waiting", "in_progress"}:
            flag("ATENCAO", "SCHEDULE_DELAY", "Intervalo desde último início ultrapassou 30 min.", age_min=last_run_age)
    checked = state.get("last_checked_at") or state.get("updated_at")
    check_age = age_min(checked, now) if checked else None
    position = state.get("open_position") is True
    if check_age is None:
        flag("CRITICO", "MISSING_CHECK", "Estado sem horário de verificação.")
    elif check_age > 35 and position:
        flag("CRITICO", "POSITION_UNCHECKED", "Posição PAPER aberta sem avaliação há mais de 35 min.", age_min=check_age)
    elif check_age > 75:
        flag("CRITICO", "STATE_STALE", "Estado público não atualizado há mais de 75 min.", age_min=check_age)
    elif check_age > 30:
        flag("ATENCAO", "STATE_DELAY", "Estado público não atualizado há mais de 30 min.", age_min=check_age)
    closed = market_closed(now)
    last_candle = state.get("last_candle_at")
    candle_age = age_min(last_candle, now) if last_candle else None
    if not closed and not sunday_open_grace(now):
        if candle_age is None:
            flag("CRITICO", "CANDLE_MISSING", "Estado público sem candle processado durante sessão aberta.")
        elif candle_age > 75:
            flag("CRITICO", "CANDLE_STALE", "Último candle está muito antigo durante sessão aberta.", age_min=candle_age)
        elif candle_age > 35 and position:
            flag("CRITICO", "POSITION_CANDLE_STALE", "Posição PAPER e último candle sem avanço há mais de 35 min.", age_min=candle_age)
        elif candle_age > 35:
            flag("ATENCAO", "CANDLE_DELAY", "Último candle tem mais de 35 min durante sessão aberta.", age_min=candle_age)
    if state.get("gap_halt") is True or state.get("status") in {"data_gap", "gap_halt"}:
        flag("CRITICO", "DATA_GAP", "Bloqueio por lacuna de candles.")
    if state["risk_violations"] > 0:
        flag("CRITICO", "RISK_VIOLATIONS", "Histórico público indica violação do risco planejado.")
    if state.get("status") == "api_error":
        flag("CRITICO" if position or (check_age is not None and check_age > 30) else "ATENCAO",
             "API_ERROR", "Estado público registra erro da API; causa exata requer logs.")
    scheduled = [r for r in bot if r.get("event") == "schedule"]
    intervals = [
        round((utc(a["created_at"]) - utc(b["created_at"])).total_seconds() / 60, 1)
        for a, b in zip(scheduled[:4], scheduled[1:5])
    ]
    if len(intervals) == 0:
        flag("ATENCAO", "INSUFFICIENT_SCHEDULE_HISTORY", "Amostra insuficiente para verificar cadência.")
    elif max(intervals) > 30:
        flag("ATENCAO", "CADENCE_DELAY", "Intervalos reais entre agendamentos acima de 30 min.", intervals_min=intervals)
    # A failed run within 90 min can be relevant even if a newer run succeeded.
    for previous in bot[1:5]:
        if previous.get("conclusion") in {"failure", "timed_out", "cancelled"} and age_min(previous["created_at"], now) < 90:
            flag("ATENCAO", "RECENT_FAILURE", "Falha anterior recente no workflow.", run_id=previous.get("id"))
            break
    status = "CRITICO" if any(i["severity"] == "CRITICO" for i in issues) else (
        "ATENCAO" if issues else "OK"
    )
    return {
        "status": status,
        "issues": issues,
        "last_run_age_min": last_run_age,
        "state_age_min": check_age,
        "candle_age_min": candle_age,
        "market_closed_weekend": closed,
        "recent_schedule_intervals_min": intervals,
        "last_run_id": last.get("id") if last else None,
        "last_run_status": last.get("status") if last else None,
        "last_run_conclusion": last.get("conclusion") if last else None,
        "last_checked_at": checked,
        "last_candle_at": last_candle,
        "open_position": position,
        "position_side": state.get("position_side") if position else None,
        "balance_usd": state.get("balance"),
        "equity_usd": state.get("estimated_equity"),
        "closed_trades": state.get("closed_trades"),
        "gap_halt": bool(state.get("gap_halt")),
        "source": "GitHub PUBLIC only; no encrypted state, no credentials, no trading",
    }

def github_json(path, *, opener=None, sleeper=time.sleep, retries=3):
    """Retrieve public GitHub JSON with bounded retries for transient errors."""
    if not path.startswith(("contents/v2/state.json?", "actions/workflows/forex-v2.yml/runs?")):
        raise ValueError("GitHub endpoint fora da lista permitida")
    opener = opener or urllib.request.urlopen
    req = urllib.request.Request(
        "https://api.github.com/repos/" + REPOSITORY + "/" + path,
        headers={"User-Agent": "Forex-Watch-V2.3-readonly", "Accept": "application/vnd.github+json",
                 "Cache-Control": "no-cache"},
    )
    for attempt in range(retries):
        try:
            with opener(req, timeout=TIMEOUT_SEC) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise ValueError("resposta pública excede limite")
                return json.loads(raw)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            retryable = not isinstance(exc, urllib.error.HTTPError) or exc.code in {429, 500, 502, 503, 504}
            if not retryable or attempt + 1 >= retries:
                raise
            sleeper(2 ** attempt)
    raise RuntimeError("unreachable")

def live_check(*, fetcher=github_json, now=None):
    now = utc(now or datetime.now(timezone.utc))
    data = fetcher(STATE_ENDPOINT)
    if not isinstance(data, dict) or data.get("encoding") != "base64":
        raise ValueError("github contents response inválida")
    raw = base64.b64decode(data.get("content", "").replace("\n", ""), validate=True)
    state = json.loads(raw)
    run_data = fetcher(RUNS_ENDPOINT)
    if not isinstance(run_data, dict) or not isinstance(run_data.get("workflow_runs"), list):
        raise ValueError("github actions response inválida")
    return assess(state, run_data["workflow_runs"], now)

def main(argv=None):
    parser = argparse.ArgumentParser(description="Forex Watch V2.3 — somente leitura")
    parser.add_argument("--json", action="store_true", help="Relatório JSON estruturado")
    args = parser.parse_args(argv)
    try:
        report = live_check()
    except (ValueError, TypeError, KeyError, UnicodeError, urllib.error.URLError,
            TimeoutError, json.JSONDecodeError) as exc:
        report = {"status": "INDETERMINADO", "issues": [
            {"severity": "INDETERMINADO", "code": "MONITOR_UNAVAILABLE",
             "message": "Impossível confirmar saúde do robô via dados públicos.",
             "error_type": type(exc).__name__}],
            "source": "GitHub PUBLIC; no inference on bot status"}
        print(json.dumps(report, ensure_ascii=False, indent=2), file=sys.stdout)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json
          else "\n".join([f"FOREX WATCH V2.3 — {report['status']}"] +
                          [f"- {i['code']}: {i['message']}" for i in report["issues"]]))
    return 1 if report["status"] == "CRITICO" else 0

if __name__ == "__main__":
    raise SystemExit(main())
