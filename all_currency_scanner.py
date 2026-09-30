"""
ALL-CURRENCY LIVE SIGNAL SCANNER — DEMO / TESTING ONLY

Scans several major FX pairs at once and displays CALL / PUT / WAIT.
It does NOT:
- connect to Quotex
- use Quotex credentials
- place trades

It uses external Yahoo Finance market data. External quotes may differ
from Quotex quotes, especially OTC instruments.
"""

import csv
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import yfinance as yf
import tkinter as tk
from tkinter import ttk


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
TIMEFRAME_WEIGHT = {
    "1m": 1,
    "5m": 2,
    "15m": 3,
    "1h": 4,
    "4h": 5,
    "1D": 6,
}

# Different periods for different timeframes (Yahoo Finance limitations)
PERIOD_BY_TIMEFRAME = {
    "1m": "7d",
    "5m": "60d",
    "15m": "60d",
    "1h": "3mo",
    "4h": "3mo",
    "1D": "1y",
}

REFRESH_SECONDS = 30  # Scan every 30 seconds (improved from 60)
LOG_FILE = "signals.csv"
SUMMARY_FILE = "summary_log.csv"


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
        data = yf.download(
            symbol,
            period=period,
            interval=timeframe,
            auto_adjust=False,
            progress=False,
        )
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

    # Use last completed candle, not current partial candle
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
        "status": "OK",
    }


def summarize_pair(rows):
    """Calculate final multi-timeframe signal for a pair"""
    total_score = 0
    trend_counts = {"UP": 0, "DOWN": 0, "FLAT": 0}
    signal_count = {"CALL": 0, "PUT": 0, "WAIT": 0}
    valid_rows = [r for r in rows if r["signal"] != "ERROR"]

    for row in valid_rows:
        tf = row["timeframe"]
        weight = TIMEFRAME_WEIGHT.get(tf, 1)

        if row["signal"] == "CALL":
            total_score += weight
            signal_count["CALL"] += 1
        elif row["signal"] == "PUT":
            total_score -= weight
            signal_count["PUT"] += 1
        else:
            signal_count["WAIT"] += 1

        trend_counts[row["trend"]] = trend_counts.get(row["trend"], 0) + 1

    if total_score > 2:
        final_signal = "CALL"
    elif total_score < -2:
        final_signal = "PUT"
    else:
        final_signal = "WAIT"

    dominant_trend = max(trend_counts, key=trend_counts.get) if trend_counts else "FLAT"

    return {
        "final_signal": final_signal,
        "score": total_score,
        "dominant_trend": dominant_trend,
        "valid_count": len(valid_rows),
    }


class Scanner:
    def __init__(self, root):
        self.root = root
        self.root.title("All Currency Signal Scanner - Automated Bot")
        self.root.geometry("1500x900")
        self.root.resizable(True, True)

        self.busy = False
        self.running = True
        self.countdown = 0
        self.last_logged = set()
        self.last_summary_logged = set()

        self.status = tk.StringVar(value="Initializing automated scanner...")
        self.stats = tk.StringVar(value="Scans: 0 | Signals: 0 | CALL: 0 | PUT: 0")

        self.scan_count = 0
        self.signal_count = 0
        self.call_count = 0
        self.put_count = 0

        self.build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(200, self.refresh)
        self.root.after(1000, self.tick)

    def build_ui(self):
        # Title
        ttk.Label(
            self.root,
            text="ALL-CURRENCY LIVE SIGNAL SCANNER — AUTOMATED BOT",
            font=("Segoe UI", 20, "bold"),
        ).pack(pady=(15, 2))

        ttk.Label(
            self.root,
            text="10 FX pairs • 1m, 5m, 15m, 1h, 4h, 1D • Auto-scan every 30 seconds • demo only",
            font=("Segoe UI", 11),
        ).pack(pady=(0, 4))

        # Status bar
        top = ttk.Frame(self.root)
        top.pack(fill="x", padx=20, pady=(0, 6))

        ttk.Label(
            top,
            textvariable=self.status,
            font=("Segoe UI", 10),
        ).pack(side="left")

        ttk.Label(
            top,
            textvariable=self.stats,
            font=("Segoe UI", 10, "bold"),
            foreground="darkblue",
        ).pack(side="left", padx=30)

        self.refresh_button = ttk.Button(
            top,
            text="Scan Now",
            command=self.refresh,
        )
        self.refresh_button.pack(side="right")

        # Detailed timeframe table
        ttk.Label(self.root, text="Detailed Timeframe Analysis", font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=20, pady=(12, 4))

        columns = ("Pair", "1m", "5m", "15m", "1h", "4h", "1D", "Price", "Status")
        self.tree = ttk.Treeview(
            self.root,
            columns=columns,
            show="headings",
            height=14,
        )

        widths = {
            "Pair": 120,
            "1m": 70,
            "5m": 70,
            "15m": 70,
            "1h": 70,
            "4h": 70,
            "1D": 70,
            "Price": 130,
            "Status": 100,
        }

        for col in columns:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=widths[col], anchor="center")

        self.tree.pack(fill="both", expand=True, padx=20, pady=(0, 12))

        self.tree.tag_configure("CALL", background="#dcefe5", foreground="darkgreen")
        self.tree.tag_configure("PUT", background="#f8d7da", foreground="darkred")
        self.tree.tag_configure("WAIT", background="#e2e3e5", foreground="gray")

        # Final summary table
        ttk.Label(self.root, text="Multi-Timeframe Summary — AUTOMATED SIGNALS", font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=20, pady=(12, 4))

        summary_columns = ("Pair", "Final Signal", "Score", "Strength", "Trend", "Action")
        self.summary_tree = ttk.Treeview(
            self.root,
            columns=summary_columns,
            show="headings",
            height=11,
        )

        summary_widths = {
            "Pair": 130,
            "Final Signal": 130,
            "Score": 80,
            "Strength": 100,
            "Trend": 120,
            "Action": 180,
        }

        for col in summary_columns:
            self.summary_tree.heading(col, text=col)
            self.summary_tree.column(col, width=summary_widths[col], anchor="center")

        self.summary_tree.pack(fill="both", expand=True, padx=20, pady=(0, 12))

        self.summary_tree.tag_configure("CALL", background="#dcefe5", foreground="darkgreen")
        self.summary_tree.tag_configure("PUT", background="#f8d7da", foreground="darkred")
        self.summary_tree.tag_configure("WAIT", background="#e2e3e5", foreground="gray")

        # Info bar
        ttk.Label(
            self.root,
            text=(
                "Pairs: EUR/USD • GBP/USD • USD/JPY • USD/CHF • AUD/USD • USD/CAD • NZD/USD • EUR/GBP • EUR/JPY • GBP/JPY"
            ),
            wraplength=1400,
            justify="center",
            font=("Segoe UI", 9),
        ).pack(pady=2)

        ttk.Label(
            self.root,
            text=(
                "⚠ DATA WARNING: Yahoo Finance prices may differ from Quotex. Signals are educational/testing only. "
                "Score = weighted sum (1D counts 6x, 1m counts 1x). Strength shows confidence level."
            ),
            wraplength=1400,
            justify="center",
            font=("Segoe UI", 8),
            foreground="red",
        ).pack(pady=2)

        ttk.Label(
            self.root,
            text="Next auto-scan:",
            font=("Segoe UI", 10, "bold"),
        ).pack()

        self.next_label = ttk.Label(self.root, text="--", font=("Segoe UI", 10, "bold"), foreground="darkblue")
        self.next_label.pack(pady=(0, 8))

    def refresh(self):
        if self.busy:
            return

        self.busy = True
        self.countdown = REFRESH_SECONDS
        self.status.set("🔄 Automated scanning in progress...")
        self.refresh_button.config(state="disabled")

        for item in self.tree.get_children():
            self.tree.delete(item)

        for item in self.summary_tree.get_children():
            self.summary_tree.delete(item)

        thread = __import__("threading").Thread(
            target=self.worker,
            daemon=True,
        )
        thread.start()

    def worker(self):
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
                    result["timeframe"] = tf
                    results.append(result)
                except Exception as exc:
                    results.append({
                        "pair": pair,
                        "timeframe": tf,
                        "price": None,
                        "rsi": None,
                        "trend": "--",
                        "signal": "ERROR",
                        "candle": "--",
                        "status": str(exc)[:25],
                    })

        order = {pair: i for i, pair in enumerate(PAIRS.keys())}
        results.sort(
            key=lambda x: (
                order.get(x["pair"], 999),
                TIMEFRAMES.index(x["timeframe"])
            )
        )

        grouped = {}
        for row in results:
            grouped.setdefault(row["pair"], []).append(row)

        final_rows = []
        for pair, rows in grouped.items():
            summary = summarize_pair(rows)
            final_rows.append({
                "pair": pair,
                "final_signal": summary["final_signal"],
                "score": summary["score"],
                "dominant_trend": summary["dominant_trend"],
                "valid_count": summary["valid_count"],
                "rows": rows,
            })

        final_rows.sort(key=lambda x: list(PAIRS).index(x["pair"]))

        self.root.after(0, self.update_table, results, final_rows)

    def get_strength(self, score):
        """Convert score to strength label"""
        abs_score = abs(score)
        if abs_score >= 20:
            return "VERY STRONG"
        elif abs_score >= 10:
            return "STRONG"
        elif abs_score >= 5:
            return "MODERATE"
        else:
            return "WEAK"

    def get_action(self, signal, strength):
        """Generate automated action recommendation"""
        if signal == "CALL" and strength in ("VERY STRONG", "STRONG"):
            return "✓ BUY CALL (High Confidence)"
        elif signal == "PUT" and strength in ("VERY STRONG", "STRONG"):
            return "✓ BUY PUT (High Confidence)"
        elif signal == "CALL" and strength == "MODERATE":
            return "~ CONSIDER CALL (Medium Confidence)"
        elif signal == "PUT" and strength == "MODERATE":
            return "~ CONSIDER PUT (Medium Confidence)"
        else:
            return "⊘ HOLD / WAIT FOR CONFIRMATION"

    def update_table(self, results, final_rows):
        # Detailed table - grouped by pair with all timeframes in one row
        pair_data = {}
        for r in results:
            if r["pair"] not in pair_data:
                pair_data[r["pair"]] = {"price": None, "status": "OK", "signals": {}}
            pair_data[r["pair"]]["signals"][r["timeframe"]] = r["signal"]
            if r["price"]:
                pair_data[r["pair"]]["price"] = r["price"]
            if r["status"] != "OK":
                pair_data[r["pair"]]["status"] = r["status"]

        for pair in PAIRS.keys():
            data = pair_data.get(pair, {"signals": {}, "price": None, "status": "OK"})
            signals = data["signals"]
            price = f"{data['price']:.6f}" if data["price"] else "--"

            signal_values = [signals.get(tf, "--") for tf in TIMEFRAMES]
            tag = max(signal_values, key=lambda x: 2 if x == "CALL" else 1 if x == "PUT" else 0)

            self.tree.insert(
                "",
                "end",
                values=(
                    pair,
                    signals.get("1m", "--"),
                    signals.get("5m", "--"),
                    signals.get("15m", "--"),
                    signals.get("1h", "--"),
                    signals.get("4h", "--"),
                    signals.get("1D", "--"),
                    price,
                    data["status"],
                ),
                tags=(tag if tag in ("CALL", "PUT", "WAIT") else "",),
            )

        # Summary table with actions
        for row in final_rows:
            strength = self.get_strength(row["score"])
            action = self.get_action(row["final_signal"], strength)
            tag = row["final_signal"] if row["final_signal"] in ("CALL", "PUT", "WAIT") else ""

            self.summary_tree.insert(
                "",
                "end",
                values=(
                    row["pair"],
                    row["final_signal"],
                    row["score"],
                    strength,
                    row["dominant_trend"],
                    action,
                ),
                tags=(tag,),
            )

            # Log strong signals
            if row["final_signal"] in ("CALL", "PUT") and strength in ("STRONG", "VERY STRONG"):
                key = f"{row['pair']}|{row['final_signal']}|{strength}"
                if key not in self.last_summary_logged:
                    self.last_summary_logged.add(key)
                    self.log_summary_signal(row, strength, action)
                    self.signal_count += 1
                    if row["final_signal"] == "CALL":
                        self.call_count += 1
                    elif row["final_signal"] == "PUT":
                        self.put_count += 1

            # Log individual timeframe signals
            for r in row["rows"]:
                if r["signal"] in ("CALL", "PUT"):
                    key = f"{r['pair']}|{r['timeframe']}|{r['candle']}|{r['signal']}"
                    if key not in self.last_logged:
                        self.last_logged.add(key)
                        self.log_signal(r)
                        try:
                            self.root.bell()
                        except Exception:
                            pass

        self.scan_count += 1
        self.status.set(
            "✓ Last scan: " + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " | Bot running automatically..."
        )
        self.stats.set(f"Scans: {self.scan_count} | Strong Signals: {self.signal_count} | CALL: {self.call_count} | PUT: {self.put_count}")
        self.busy = False
        self.refresh_button.config(state="normal")

    def log_signal(self, r):
        path = Path(LOG_FILE)
        new_file = not path.exists()

        with path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)

            if new_file:
                writer.writerow([
                    "logged_at",
                    "pair",
                    "timeframe",
                    "candle_time",
                    "price",
                    "rsi14",
                    "trend",
                    "signal",
                ])

            writer.writerow([
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                r["pair"],
                r["timeframe"],
                r["candle"],
                f"{r['price']:.6f}",
                f"{r['rsi']:.2f}",
                r["trend"],
                r["signal"],
            ])

    def log_summary_signal(self, row, strength, action):
        path = Path(SUMMARY_FILE)
        new_file = not path.exists()

        with path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)

            if new_file:
                writer.writerow([
                    "logged_at",
                    "pair",
                    "final_signal",
                    "score",
                    "strength",
                    "trend",
                    "action",
                ])

            writer.writerow([
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                row["pair"],
                row["final_signal"],
                row["score"],
                strength,
                row["dominant_trend"],
                action,
            ])

    def tick(self):
        if not self.running:
            return

        if self.busy:
            self.next_label.config(text="Scanning...", foreground="orange")
        elif self.countdown > 0:
            self.next_label.config(text=f"{self.countdown} seconds", foreground="darkblue")
            self.countdown -= 1
        else:
            self.refresh()

        self.root.after(1000, self.tick)

    def close(self):
        self.running = False
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = Scanner(root)
    root.mainloop()
