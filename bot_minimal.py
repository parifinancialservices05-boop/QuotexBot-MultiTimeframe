"""
MINIMAL BOT CODE - NO UI EXTRAS
Only core bot logic for signal generation and logging
"""

import csv
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import yfinance as yf


PAIRS = {
    "EUR/USD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X",
    "USD/JPY": "USDJPY=X",
    "USD/CHF": "USDCHF=X",
    "AUD/USD": "AUDUSD=X",
    "USD/CAD": "USDCAD=X",
    "NZD/USD": "NZDUSD=X",
    "EUR/GBP": "EURGBP=X",
    "EUR/JPY": "EURJPY=X",
    "GBP/JPY": "GBPJPY=X",
}

TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1D"]
TIMEFRAME_WEIGHT = {"1m": 1, "5m": 2, "15m": 3, "1h": 4, "4h": 5, "1D": 6}

PERIOD_BY_TIMEFRAME = {
    "1m": "7d", "5m": "60d", "15m": "60d",
    "1h": "3mo", "4h": "3mo", "1D": "1y",
}

SUMMARY_FILE = "bot_signals.csv"


def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    return 100 - (100 / (1 + rs))


def get_one(pair, symbol, timeframe):
    try:
        period = PERIOD_BY_TIMEFRAME.get(timeframe, "3mo")
        data = yf.download(symbol, period=period, interval=timeframe, auto_adjust=False, progress=False)
    except Exception as exc:
        raise RuntimeError(f"Download failed: {str(exc)[:30]}")

    if data.empty:
        raise RuntimeError("No data")

    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)

    if "Close" not in data.columns:
        raise RuntimeError("No Close column")

    data = data.dropna(subset=["Close"]).copy()
    data["EMA9"] = data["Close"].ewm(span=9, adjust=False).mean()
    data["EMA21"] = data["Close"].ewm(span=21, adjust=False).mean()
    data["RSI14"] = rsi(data["Close"], 14)
    data = data.dropna()

    if len(data) < 3:
        raise RuntimeError("Not enough candles")

    row = data.iloc[-2]
    candle_time = pd.Timestamp(data.index[-2]).strftime("%Y-%m-%d %H:%M:%S")
    close = float(row["Close"])
    ema9 = float(row["EMA9"])
    ema21 = float(row["EMA21"])
    rsi14 = float(row["RSI14"])

    if ema9 > ema21 and 50 <= rsi14 <= 70 and close > ema9:
        signal = "CALL"
    elif ema9 < ema21 and 30 <= rsi14 <= 50 and close < ema9:
        signal = "PUT"
    else:
        signal = "WAIT"

    trend = "UP" if ema9 > ema21 else "DOWN" if ema9 < ema21 else "FLAT"

    return {
        "pair": pair,
        "timeframe": timeframe,
        "price": close,
        "rsi": rsi14,
        "trend": trend,
        "signal": signal,
        "candle": candle_time,
    }


def summarize_pair(rows):
    total_score = 0
    valid_rows = [r for r in rows if r["signal"] != "ERROR"]

    for row in valid_rows:
        tf = row["timeframe"]
        weight = TIMEFRAME_WEIGHT.get(tf, 1)
        if row["signal"] == "CALL":
            total_score += weight
        elif row["signal"] == "PUT":
            total_score -= weight

    if total_score > 2:
        final_signal = "CALL"
    elif total_score < -2:
        final_signal = "PUT"
    else:
        final_signal = "WAIT"

    return {"final_signal": final_signal, "score": total_score}


def get_strength(score):
    abs_score = abs(score)
    if abs_score >= 20:
        return "VERY_STRONG"
    elif abs_score >= 10:
        return "STRONG"
    elif abs_score >= 5:
        return "MODERATE"
    else:
        return "WEAK"


def scan():
    """Single scan of all pairs and timeframes"""
    results = []

    with ThreadPoolExecutor(max_workers=15) as pool:
        futures = {
            pool.submit(get_one, pair, symbol, tf): (pair, tf)
            for pair, symbol in PAIRS.items()
            for tf in TIMEFRAMES
        }

        for future in as_completed(futures):
            pair, tf = futures[future]
            try:
                result = future.result()
                results.append(result)
            except Exception as exc:
                results.append({
                    "pair": pair,
                    "timeframe": tf,
                    "price": None,
                    "signal": "ERROR",
                })

    # Group by pair
    grouped = {}
    for row in results:
        grouped.setdefault(row["pair"], []).append(row)

    # Summarize
    signals = []
    for pair in PAIRS.keys():
        rows = grouped.get(pair, [])
        if rows:
            summary = summarize_pair(rows)
            strength = get_strength(summary["score"])
            signals.append({
                "pair": pair,
                "signal": summary["final_signal"],
                "score": summary["score"],
                "strength": strength,
            })

    return signals


def log_signals(signals):
    """Log strong signals only"""
    path = Path(SUMMARY_FILE)
    new_file = not path.exists()

    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if new_file:
            writer.writerow(["timestamp", "pair", "signal", "score", "strength"])

        for sig in signals:
            if sig["signal"] in ("CALL", "PUT") and sig["strength"] in ("STRONG", "VERY_STRONG"):
                writer.writerow([
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    sig["pair"],
                    sig["signal"],
                    sig["score"],
                    sig["strength"],
                ])


if __name__ == "__main__":
    import time

    print("🤖 MINIMAL BOT - Running...")
    scan_count = 0

    while True:
        try:
            scan_count += 1
            print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Scan #{scan_count}")
            
            signals = scan()
            log_signals(signals)

            # Print strong signals only
            strong = [s for s in signals if s["strength"] in ("STRONG", "VERY_STRONG")]
            if strong:
                print("🚀 STRONG SIGNALS FOUND:")
                for s in strong:
                    print(f"  {s['pair']}: {s['signal']} (Score: {s['score']}, {s['strength']})")
            else:
                print("✓ No strong signals")

            print(f"⏳ Next scan in 30 seconds...")
            time.sleep(30)

        except KeyboardInterrupt:
            print("\n✓ Bot stopped by user")
            break
        except Exception as e:
            print(f"❌ Error: {e}")
            time.sleep(30)
