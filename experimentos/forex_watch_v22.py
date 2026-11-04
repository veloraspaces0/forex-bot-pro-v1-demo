#!/usr/bin/env python3
"""Forex Watch V2.2: READ-ONLY public status / GitHub Actions cadence checker.

Does not use secrets, encrypted state, or place trades. No GitHub writes.
"""
import argparse
import base64
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

REPOSITORY = "veloraspaces0/forex-bot-pro-v1-demo"
WORKFLOW_NAME = "Forex Bot Pro V2 - Paper EURUSD"
WARN_AFTER_MIN = 30
CRITICAL_AFTER_MIN = 75
POSITION_STALE_AFTER_MIN = 35

def parse_date(value):
    if not value:
        raise ValueError("missing timestamp")
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timestamp must contain timezone")
    return dt.astimezone(timezone.utc)

def age_minutes(then, now):
    age = (now - parse_date(then)).total_seconds() / 60
    if age < -2:
        raise ValueError("timestamp is in the future")
    return round(max(age, 0), 1)

def assess(state, workflow_runs, now):
    """Deterministic read-only health report; no I/O and no state mutation."""
    now = parse_date(now) if not isinstance(now, datetime) else now.astimezone(timezone.utc)
    bot_runs = sorted(
        (r for r in workflow_runs if r.get("name") == WORKFLOW_NAME and r.get("head_branch") == "main"),
        key=lambda x: x.get("created_at") or "", reverse=True
    )
    problems = []
    level = 0  # 0 OK, 1 ATTENTION, 2 CRITICAL
    def raise_to(severity, reason):
        nonlocal level
        level = max(level, severity)
        problems.append(reason)
    if not bot_runs:
        raise_to(2, "Nenhuma execução do bot na main encontrada.")
        run_age = None
    else:
        latest = bot_runs[0]
        run_age = age_minutes(latest["created_at"], now)
        if latest.get("conclusion") in {"failure", "timed_out", "cancelled", "action_required"}:
            raise_to(2, "Última execução terminou sem sucesso: " + str(latest["conclusion"]))
        elif latest.get("status") not in {"completed", "in_progress", "queued", "waiting"}:
            raise_to(1, "Status do workflow desconhecido.")
        if run_age > CRITICAL_AFTER_MIN:
            raise_to(2, "Sem início de execução há mais de 75 minutos.")
        elif run_age > WARN_AFTER_MIN:
            raise_to(1, "Execução do bot atrasada há mais de 30 minutos.")
    checked = state.get("last_checked_at") or state.get("updated_at")
    state_age = age_minutes(checked, now) if checked else None
    if state_age is None:
        raise_to(2, "Estado público sem horário de verificação.")
    elif state_age > CRITICAL_AFTER_MIN:
        raise_to(2, "Estado público sem atualização há mais de 75 minutos.")
    elif state.get("open_position") and state_age > POSITION_STALE_AFTER_MIN:
        raise_to(2, "Posição paper aberta sem avaliação há mais de 35 minutos.")
    elif state_age > WARN_AFTER_MIN:
        raise_to(1, "Estado público atrasado há mais de 30 minutos.")
    if state.get("gap_halt") or state.get("status") == "data_gap":
        raise_to(2, "Parada por lacuna de candles: revisão manual obrigatória.")
    if state.get("status") == "api_error":
        raise_to(2 if state.get("open_position") else 1, "Último estado indicou erro da API.")
    scheduled = [r for r in bot_runs if r.get("event") == "schedule"]
    cadence = [
        round((parse_date(a["created_at"]) - parse_date(b["created_at"])).total_seconds() / 60, 1)
        for a, b in zip(scheduled[:3], scheduled[1:4])
    ]
    if cadence and max(cadence) > WARN_AFTER_MIN:
        raise_to(1, "Intervalos reais de agendamento acima de 30 min: " + ", ".join(map(str, cadence)) + " min.")
    return {
        "status": ("OK", "ATENCAO", "CRITICO")[level],
        "reasons": problems,
        "last_run_age_min": run_age,
        "state_age_min": state_age,
        "recent_schedule_intervals_min": cadence,
        "balance_usd": state.get("balance"),
        "estimated_equity_usd": state.get("estimated_equity"),
        "closed_trades": state.get("closed_trades"),
        "open_position": bool(state.get("open_position")),
        "position_side": state.get("position_side"),
        "gap_halt": bool(state.get("gap_halt")),
        "risk_violations": state.get("risk_violations"),
        "source": "PUBLIC DATA ONLY; no encrypted state or credentials",
    }

def github_json(path):
    req = urllib.request.Request(
        "https://api.github.com/repos/" + REPOSITORY + "/" + path,
        headers={"User-Agent": "Forex-Watch-V2.2-readonly", "Accept": "application/vnd.github+json"}
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.loads(response.read(1000000))

def live_check():
    """Fetches *only* public GitHub metadata; never calls brokerage or Twelve Data."""
    contents = github_json("contents/v2/state.json?ref=main")
    if contents.get("encoding") != "base64":
        raise ValueError("Unexpected public JSON encoding")
    state = json.loads(base64.b64decode(contents["content"], validate=False))
    runs = github_json("actions/runs?branch=main&per_page=30").get("workflow_runs", [])
    return assess(state, runs, datetime.now(timezone.utc))

def main():
    parser = argparse.ArgumentParser(description="Forex Watch V2.2 — somente leitura")
    parser.add_argument("--json", action="store_true", help="Imprimir relatório completo em JSON")
    args = parser.parse_args()
    try:
        report = live_check()
    except (urllib.error.URLError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print("Falha ao consultar dados públicos: " + type(exc).__name__, file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("FOREX WATCH V2.2 —", report["status"])
        print("Saldo:", report["balance_usd"], "USD | Equity:", report["estimated_equity_usd"], "USD")
        print("Trades fechados:", report["closed_trades"],
              "| Posição paper:", report["position_side"] if report["open_position"] else "Nenhuma")
        print("Intervalos agendados recentes (min):", report["recent_schedule_intervals_min"])
        for reason in report["reasons"]:
            print("  -", reason)
        print("Somente leitura; não altera trades, estados ou secrets.")
    return 0 if report["status"] == "OK" else 1

if __name__ == "__main__":
    raise SystemExit(main())
