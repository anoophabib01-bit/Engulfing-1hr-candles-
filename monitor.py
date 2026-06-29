"""
EdgeDesk Setup Monitor — Cloud Worker
Monitors NQ=F (MNQ proxy) and GC=F (MGC proxy) every 5 minutes.
Checks Playbook A/B/C on 1H bars and fires Telegram when conditions are met.
Session window: 07:30–15:30 UTC (1:00 PM – 9:00 PM IST)
"""

import time
import requests
import schedule
import yfinance as yf
from datetime import datetime, timezone

# ── Config ──────────────────────────────────────────────────────────────────
TG_TOKEN  = "8929601303:AAH3CMZaYBZVZsTF9ZdUkG8oQsJoJyuQDA8"
TG_CHAT   = "1634079730"

# Yahoo Finance tickers — same price action as MNQ1! / MGC1!
SYMBOLS = {
    "MNQ": "NQ=F",
    "MGC": "GC=F",
}

COOLDOWN_SEC = 300   # 5-min cooldown per playbook per symbol
cooldowns: dict = {}

# ── Telegram ─────────────────────────────────────────────────────────────────
def send_telegram(msg: str):
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        requests.post(
            url,
            json={"chat_id": TG_CHAT, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        print(f"[TG ERROR] {e}")


# ── Session check ─────────────────────────────────────────────────────────────
def get_session() -> str | None:
    """Returns session label if within 07:30–15:30 UTC, else None."""
    now = datetime.now(timezone.utc)
    h = now.hour + now.minute / 60
    if 7.5 <= h <= 15.5:
        return "🇬🇧 London" if h <= 10.5 else "🗽 NY"
    return None


# ── Data fetch ────────────────────────────────────────────────────────────────
def fetch_bars(ticker: str, interval: str, period: str) -> list[dict]:
    """Returns list of OHLC dicts, excluding the last (potentially forming) bar."""
    try:
        df = yf.download(ticker, period=period, interval=interval,
                         progress=False, auto_adjust=True)
        if df.empty:
            return []
        bars = [
            {
                "open":  float(row["Open"].iloc[0])  if hasattr(row["Open"], "iloc") else float(row["Open"]),
                "high":  float(row["High"].iloc[0])  if hasattr(row["High"], "iloc") else float(row["High"]),
                "low":   float(row["Low"].iloc[0])   if hasattr(row["Low"], "iloc") else float(row["Low"]),
                "close": float(row["Close"].iloc[0]) if hasattr(row["Close"], "iloc") else float(row["Close"]),
            }
            for _, row in df.iterrows()
        ]
        return bars[:-1]  # drop last forming candle
    except Exception as e:
        print(f"[FETCH ERROR] {ticker} {interval}: {e}")
        return []


# ── Playbook detection ────────────────────────────────────────────────────────
def check_playbooks(bars_1h: list, bars_4h: list, label: str, sess: str):
    now_ts = time.time()

    if len(bars_1h) < 10:
        print(f"[{label}] Not enough 1H bars ({len(bars_1h)}), skipping.")
        return

    c = bars_1h[-10:]   # last 10 confirmed closed 1H candles
    recent = bars_1h[-6:]  # last 6 for SFP/FVG

    # 4H bias
    bias_4h = None
    if bars_4h:
        last4h = bars_4h[-1]
        bias_4h = "Bullish" if last4h["close"] > last4h["open"] else "Bearish"

    # ── PLAYBOOK A: 1H Engulfing + 4H Direction ──────────────────────────
    if bias_4h and len(c) >= 2:
        prev, curr = c[-2], c[-1]

        bull_eng = (
            curr["close"] > curr["open"]
            and curr["open"]  <= prev["close"]
            and curr["close"] >= prev["open"]
        )
        bear_eng = (
            curr["close"] < curr["open"]
            and curr["open"]  >= prev["close"]
            and curr["close"] <= prev["open"]
        )
        eng_dir = "Bullish" if bull_eng else ("Bearish" if bear_eng else None)

        if eng_dir and eng_dir == bias_4h:
            key = f"{label}_A"
            if now_ts - cooldowns.get(key, 0) > COOLDOWN_SEC:
                cooldowns[key] = now_ts
                print(f"[{label}] PLAYBOOK A triggered")
                send_telegram(
                    f"⚡ <b>PLAYBOOK A — {label} {sess}</b>\n"
                    f"✅ 1H Engulfing candle confirmed\n"
                    f"✅ Matches 4H {bias_4h} bias\n"
                    f"Candle: {prev['open']:.1f}→{prev['close']:.1f} "
                    f"engulfed by {curr['open']:.1f}→{curr['close']:.1f}\n"
                    f"Price: {curr['close']:.1f}\n"
                    f"Check {label} chart now."
                )

    # ── PLAYBOOK B: SFP + FVG ────────────────────────────────────────────
    sfp_found = False
    for i in range(1, len(recent)):
        pb, cb = recent[i - 1], recent[i]
        bear_sfp = cb["high"] > pb["high"] and cb["close"] < pb["high"]
        bull_sfp = cb["low"]  < pb["low"]  and cb["close"] > pb["low"]
        if bear_sfp or bull_sfp:
            sfp_found = True
            break

    fvg_found = False
    for i in range(len(recent) - 2):
        a, _, cc = recent[i], recent[i + 1], recent[i + 2]
        if cc["low"] > a["high"] or cc["high"] < a["low"]:
            fvg_found = True
            break

    if sfp_found and fvg_found:
        key = f"{label}_B"
        if now_ts - cooldowns.get(key, 0) > COOLDOWN_SEC:
            cooldowns[key] = now_ts
            print(f"[{label}] PLAYBOOK B triggered")
            send_telegram(
                f"⚡ <b>PLAYBOOK B — {label} {sess}</b>\n"
                f"✅ SFP detected on 1H\n"
                f"✅ FVG present as entry zone\n"
                f"Price: {c[-1]['close']:.1f}\n"
                f"Check {label} chart now."
            )

    # ── PLAYBOOK C: Liquidity Raid ────────────────────────────────────────
    if len(c) >= 6:
        window     = c[-6:-1]
        swing_high = max(b["high"] for b in window)
        swing_low  = min(b["low"]  for b in window)
        last = c[-1]

        bull_raid = last["low"]  < swing_low  and last["close"] > swing_low
        bear_raid = last["high"] > swing_high and last["close"] < swing_high

        if bull_raid or bear_raid:
            direction = "Bullish" if bull_raid else "Bearish"
            level     = swing_low if bull_raid else swing_high
            key = f"{label}_C"
            if now_ts - cooldowns.get(key, 0) > COOLDOWN_SEC:
                cooldowns[key] = now_ts
                print(f"[{label}] PLAYBOOK C triggered")
                send_telegram(
                    f"⚡ <b>PLAYBOOK C — {label} {sess}</b>\n"
                    f"✅ Liquidity {direction} raid on 1H\n"
                    f"Swept level: {level:.1f}\n"
                    f"Closed back at: {last['close']:.1f}\n"
                    f"Check {label} chart now."
                )


# ── Main scan ─────────────────────────────────────────────────────────────────
def run_scan():
    sess = get_session()
    if not sess:
        print(f"[{datetime.now(timezone.utc).strftime('%H:%M')} UTC] Outside session window — skipping.")
        return

    print(f"[{datetime.now(timezone.utc).strftime('%H:%M')} UTC] Scanning — {sess}")

    for label, ticker in SYMBOLS.items():
        try:
            bars_1h = fetch_bars(ticker, "1h", "7d")
            bars_4h = fetch_bars(ticker, "4h", "14d")

            if not bars_1h:
                send_telegram(
                    f"⚠️ <b>EdgeDesk Monitor — Data Error</b>\n"
                    f"No 1H data returned for {label} ({ticker}).\n"
                    f"Yahoo Finance may be rate-limiting."
                )
                continue

            check_playbooks(bars_1h, bars_4h, label, sess)

        except Exception as e:
            send_telegram(
                f"⚠️ <b>EdgeDesk Monitor — Error</b>\n"
                f"Symbol: {label} ({ticker})\n"
                f"Error: {str(e)}"
            )


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("EdgeDesk Setup Monitor starting...")
    send_telegram(
        "🟢 <b>EdgeDesk Monitor Online</b>\n"
        "Scanning MNQ + MGC every 5 min\n"
        "Session: 1:00 PM – 9:00 PM IST\n"
        "Playbooks: A (Engulf+4H) · B (SFP+FVG) · C (Liq Raid)"
    )

    schedule.every(5).minutes.do(run_scan)
    run_scan()  # run immediately on start

    while True:
        schedule.run_pending()
        time.sleep(30)
