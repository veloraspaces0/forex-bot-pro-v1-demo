"""Coleta candles reais da Twelve Data e atualiza o estado PUBLICO de paper trading."""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from engine import initial_state, normalize_values, summary, update_state, iso

STATE_PATH = Path(__file__).with_name("state.json")
API_URL = "https://api.twelvedata.com/time_series"


class MarketDataError(Exception):
    pass


def get_candles(api_key, now):
    params = {
        "symbol": "EUR/USD", "interval": "15min",
        "outputsize": "250", "timezone": "UTC",
        "format": "JSON", "apikey": api_key
    }
    url = API_URL + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={
        "Accept": "application/json", "User-Agent": "ForexBotProV2-Paper/1.0"
    })
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        raise MarketDataError("Twelve Data retornou HTTP " + str(exc.code)) from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise MarketDataError("Falha de rede ou resposta invalida da API") from None
    if not isinstance(data, dict):
        raise MarketDataError("Formato inesperado de resposta da API")
    if data.get("status") == "error" or "values" not in data:
        message = str(data.get("message", "API sem valores"))
        # Nunca registrar ou publicar a chave.
        raise MarketDataError(message.replace(api_key, "[REDACTED]")[:250])
    meta = data.get("meta") or {}
    if meta.get("symbol") not in ("EUR/USD", None):
        raise MarketDataError("Par retornado pela API nao e EUR/USD")
    if not isinstance(data["values"], list):
        raise MarketDataError("Serie de precos invalida")
    candles = normalize_values(data["values"], now)
    if not candles:
        raise MarketDataError("Nenhum candle de 15 minutos fechado e valido")
    return candles


def load_state():
    if not STATE_PATH.exists():
        return initial_state()
    obj = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    if obj.get("version") != 2 or obj.get("pair") != "EUR/USD":
        raise ValueError("Estado incompatível; recusando alterar capital ou histórico")
    return obj


def save_state(state):
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


def main():
    now = datetime.now(timezone.utc)
    state = load_state()
    key = os.environ.get("TWELVE_DATA_API_KEY", "").strip()
    if not key:
        # Sem chave, NUNCA gerar candles artificiais.
        if state["status"] != "AGUARDANDO_API":
            state["status"] = "AGUARDANDO_API"
            state["error"] = "Configure TWELVE_DATA_API_KEY em Settings > Secrets and variables > Actions."
            state["last_check_utc"] = iso(now)
            save_state(state)
        print("V2 aguardando TWELVE_DATA_API_KEY. Nenhuma operacao realizada.")
        return 0
    try:
        candles = get_candles(key, now)
        state = update_state(state, candles, now)
    except MarketDataError as exc:
        state["status"] = "ERRO_API"
        state["error"] = str(exc)
        state["last_check_utc"] = iso(now)
        save_state(state)
        print("Falha segura na coleta: " + str(exc))
        return 1
    save_state(state)
    print(json.dumps({"status": state["status"], "last_candle": state["last_candle"],
                      "balance": state["balance"], "summary": summary(state)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
