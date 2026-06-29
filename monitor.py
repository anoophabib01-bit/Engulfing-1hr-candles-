"""
EdgeDesk Setup Monitor — Cloud Worker
Triggers Telegram when a 1H candle fully engulfs the previous candle (wicks included).
No direction filter. Session: 07:30–15:30 UTC (1:00 PM – 9:00 PM IST)
"""

import json
import os
import time
import requests
import schedule
import yfinance as yf
from datetime import datetime, timezone

# ── Config ───────────────────────────────────────────────────────────────────
TG_TOKEN  = "8929601303:AAH3CMZaYBZVZsTF9ZdUkG8oQsJoJyuQDA8"
TG_CHAT   = "1634079730"

SYMBOLS = {
    "MNQ": "NQ=F",
    "MGC": "GC=F",
}

COOLDOWN_SEC  = 4 * 60 * 60  # 4 hours — same candle won't re-alert
COOLDOWN_FILE = "cooldowns.json"

# ── Cooldown (persisted to disk so restarts don't re-trigger) ────────────────
def load_cooldowns() -> dict:
    if os.path.exists(COOLDOWN_FILE):
        try:
            with open(COOLDOWN_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_cooldowns(cd: dict):
    try:
        with open(COOLDOWN_FILE, "w") as f:
            json.dump(cd, f)
    except Exception as e:
        print(f"[COOLDOWN SAVE ERROR] {e}")

cooldowns = load_cooldowns()

def is_cooled_down(key: str) -> bool:
    return time.time() - cooldowns.get(key, 0) < COOLDOWN_SEC

def set_cooldown(key: str):
    cooldowns[key] = time.time()
    save_cooldowns(cooldowns)

# ── Telegram ──────────────────────────────────────────────────────────────────
def send_telegram(msg: str):
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        r = requests.post(
            url,
            json={"chat_id": TG_CHAT, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
        print(f"[TG] {r.status_code}")
    except Exception as e:
        print(f"[TG ERROR] {e}")

# ── Session check ─────────────────────────────────────────────────────────────
def get_session() -> str | None:
    now = datetime.now(timezone.utc)
    h = now.hour + now.minute / 60
    if 7.5 <= h <= 15.5:
        return "🇬🇧 London" if h <= 10.5 else "🗽 NY"
    return None

# ── Data fetch ────────────────────────────────────────────────────────────────
def fetch_bars(ticker: str) -> list[dict]:
    try:
        df = yf.download(ticker, period="5d", interval="1h",
                         progress=False, auto_adjust=True)
        if df.empty:
            return []
        if hasattr(df.columns, "levels"):
            df.columns = df.columns.get_level_values(0)
        bars = [
            {
                "open":  float(row["Open"]),
                "high":  float(row["High"]),
                "low":   float(row["Low"]),
                "close": float(row["Close"]),
            }
            for _, row in df.iterrows()
        ]
        return bars[:-1]  # drop last potentially-forming candle
    except Exception as e:
        print(f"[FETCH ERROR] {ticker}: {e}")
        return []

# ── Engulf check ──────────────────────────────────────────────────────────────
def check_engulf(bars: list, label: str, sess: str):
    if len(bars) < 2:
        print(f"[{label}] Not enough bars.")
        return

    prev = bars[-2]
    curr = bars[-1]

    # Full engulf including wicks: curr range must completely contain prev range
    engulfed = curr["high"] >= prev["high"] and curr["low"] <= prev["low"]

    direction = "Bullish" if curr["close"] > curr["open"] else "Bearish"

    if engulfed:
        key = f"{label}_engulf"
        if not is_cooled_down(key):
            set_cooldown(key)
            print(f"[{label}] ✅ Engulfing candle detected ({direction})")
            send_telegram(
                f"⚡ <b>1H Engulfing — {label} {sess}</b>\n"
                f"Direction: {direction}\n"
                f"Prev range:  {prev['low']:.1f} – {prev['high']:.1f}\n"
                f"Curr range:  {curr['low']:.1f} – {curr['high']:.1f}\n"
                f"Curr candle: {curr['open']:.1f} → {curr['close']:.1f}\n"
                f"Price: {curr['close']:.1f}"
            )
        else:
            print(f"[{label}] Engulf detected but in cooldown — skipping.")
    else:
        print(f"[{label}] No engulf. "
              f"Curr H:{curr['high']:.1f} L:{curr['low']:.1f} | "
              f"Prev H:{prev['high']:.1f} L:{prev['low']:.1f}")

# ── Main scan ─────────────────────────────────────────────────────────────────
def run_scan():
    now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")
    sess = get_session()
    if not sess:
        print(f"[{now_str}] Outside session — skipping.")
        return

    print(f"[{now_str}] Scanning — {sess}")

    for label, ticker in SYMBOLS.items():
        try:
            bars = fetch_bars(ticker)
            if not bars:
                send_telegram(
                    f"⚠️ <b>EdgeDesk Monitor — Data Error</b>\n"
                    f"No data for {label} ({ticker})."
                )
                continue
            check_engulf(bars, label, sess)
        except Exception as e:
            send_telegram(
                f"⚠️ <b>EdgeDesk Monitor — Error</b>\n"
                f"{label}: {str(e)}"
            )

# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("EdgeDesk Setup Monitor starting...")
    send_telegram(
        "🟢 <b>EdgeDesk Monitor Online</b>\n"
        "MNQ + MGC · 1H full engulf (wicks) · Any direction\n"
        "Session: 1:00 PM – 9:00 PM IST"
    )

    schedule.every(5).minutes.do(run_scan)
    run_scan()  # run immediately on start

    while True:
        schedule.run_pending()
        time.sleep(30)
