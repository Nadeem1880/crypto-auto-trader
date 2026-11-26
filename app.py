import streamlit as st
import requests
import pandas as pd
import time
from concurrent.futures import ThreadPoolExecutor

st.set_page_config(page_title="AI Crypto Pro Signal & Setup Scanner", page_icon="📡", layout="wide")
st.title("📡 Pro Crypto Trade Setup & Signal Scanner (No API Key Needed)")
st.caption("Poore Binance market ko scan karke exact Entry, Stop-Loss, Take-Profit, Leverage aur Position Size batata hai — taake aap Binance ya MEXC par safe manual trade le sakein.")

# --- SIDEBAR: YOUR ACCOUNT & RISK CALCULATOR ---
st.sidebar.header("1️⃣ Scanner Settings")
timeframe = st.sidebar.selectbox("Chart Timeframe", ["5m", "15m", "1h", "4h"], index=1)
min_vol_m = st.sidebar.slider("Min 24h Volume ($ Millions)", min_value=5, max_value=200, value=15, step=5)
scan_top_n = st.sidebar.slider("Top Active Coins to Scan", min_value=20, max_value=120, value=60, step=10)
auto_refresh = st.sidebar.checkbox("🔄 Auto-Refresh Signals Every 60s", value=False)

st.sidebar.header("2️⃣ Aapka Risk & Capital Calculator")
wallet_balance = st.sidebar.number_input("Aapka Total Trading Balance ($ USDT)", min_value=10, max_value=100000, value=100, step=10)
risk_pct = st.sidebar.slider("Per Trade Risk (% of Balance)", min_value=1, max_value=10, value=2)
default_lev = st.sidebar.slider("Preferred Futures Leverage", min_value=1, max_value=10, value=3)

def get_all_active_coins(min_volume_usd, limit_n):
    urls = [
        "https://data-api.binance.vision/api/v3/ticker/24hr",
        "https://api.binance.us/api/v3/ticker/24hr"
    ]
    exclude_bases = ("USDC", "FDUSD", "TUSD", "USDP", "DAI", "EUR", "BUSD", "AEUR", "USTC")
    for u in urls:
        try:
            tickers = requests.get(u, timeout=10).json()
            if not isinstance(tickers, list):
                continue
            valid = []
            for t in tickers:
                sym = t.get("symbol", "")
                if not sym.endswith("USDT"):
                    continue
                base = sym[:-4]
                if base in exclude_bases or base.endswith("UP") or base.endswith("DOWN"):
                    continue
                q_vol = float(t.get("quoteVolume", 0))
                pct_chg = float(t.get("priceChangePercent", 0))
                if q_vol >= min_volume_usd:
                    valid.append((base, q_vol, abs(pct_chg), pct_chg))
            valid.sort(key=lambda x: (x[2] * 0.7 + (x[1] / 1e8) * 0.3), reverse=True)
            return valid[:limit_n], len(valid)
        except Exception:
            continue
    return [], 0

def fetch_candles(coin, tf):
    urls = [
        f"https://data-api.binance.vision/api/v3/klines?symbol={coin}USDT&interval={tf}&limit=80",
        f"https://api.binance.us/api/v3/klines?symbol={coin}USDT&interval={tf}&limit=80"
    ]
    for u in urls:
        try:
            r = requests.get(u, timeout=6).json()
            if isinstance(r, list) and len(r) >= 40:
                return r
        except Exception:
            continue
    return None

def analyze_setup(item):
    coin, q_vol, _, pct_24h = item
    data = fetch_candles(coin, timeframe)
    if not data:
        return None

    closes = pd.Series([float(x[4]) for x in data])
    highs = pd.Series([float(x[2]) for x in data])
    lows = pd.Series([float(x[3]) for x in data])
    vols = pd.Series([float(x[5]) for x in data])

    price = closes.iloc[-1]
    ema9 = closes.ewm(span=9).mean().iloc[-1]
    ema21 = closes.ewm(span=21).mean().iloc[-1]
    ema50 = closes.ewm(span=50).mean().iloc[-1]

    # RSI Calculation
    delta = closes.diff()
    gain = delta.clip(lower=0).rolling(14).mean().iloc[-1]
    loss = (-delta.clip(upper=0)).rolling(14).mean().iloc[-1]
    rsi = 100 - (100 / (1 + (gain / loss if loss != 0 else 0)))

    # Volume & Volatility (ATR)
    avg_vol = vols.rolling(20).mean().iloc[-1]
    vol_ratio = vols.iloc[-1] / avg_vol if avg_vol > 0 else 1.0
    atr = (highs - lows).rolling(14).mean().iloc[-1]

    signal = "WAIT"
    reason = ""
    score = 0
    entry_low, entry_high, sl, tp1, tp2 = price, price, 0.0, 0.0, 0.0

    # --- LONG SETUP LOGIC ---
    if price > ema9 > ema21 and ema21 > ema50 and 48 <= rsi <= 66 and vol_ratio >= 1.15:
        signal = "🟢 LONG"
        reason = "Strong Uptrend (EMA 9>21>50) + Volume Breakout"
        score = min(95, int(65 + (vol_ratio * 8) + (66 - abs(56 - rsi))))
        entry_low = price * 0.9985
        entry_high = price * 1.0015
        sl = price - (1.8 * atr)
        tp1 = price + (2.2 * atr)
        tp2 = price + (4.0 * atr)
    elif rsi < 30 and price > closes.iloc[-2]:
        signal = "🟢 LONG"
        reason = "Oversold Bounce (RSI < 30 Reversal)"
        score = min(90, int(60 + (32 - rsi) * 2))
        entry_low = price * 0.998
        entry_high = price * 1.002
        sl = price - (1.6 * atr)
        tp1 = price + (2.0 * atr)
        tp2 = price + (3.5 * atr)

    # --- SHORT SETUP LOGIC ---
    elif price < ema9 < ema21 and ema21 < ema50 and 34 <= rsi <= 52 and vol_ratio >= 1.15:
        signal = "🔴 SHORT"
        reason = "Strong Downtrend (EMA 9<21<50) + Selling Volume"
        score = min(95, int(65 + (vol_ratio * 8) + (52 - abs(44 - rsi))))
        entry_low = price * 0.9985
        entry_high = price * 1.0015
        sl = price + (1.8 * atr)
        tp1 = price - (2.2 * atr)
        tp2 = price - (4.0 * atr)
    elif rsi > 74 and price < closes.iloc[-2]:
        signal = "🔴 SHORT"
        reason = "Overbought Rejection (RSI > 74 Drop)"
        score = min(90, int(60 + (rsi - 70) * 2))
        entry_low = price * 0.998
        entry_high = price * 1.002
        sl = price + (1.6 * atr)
        tp1 = price - (2.0 * atr)
        tp2 = price - (3.5 * atr)

    # Smart Position Sizing based on user's risk %
    risk_usd = wallet_balance * (risk_pct / 100.0)
    sl_dist_pct = abs(price - sl) / price if sl > 0 else 0.015
    rec_position_usd = risk_usd / sl_dist_pct if sl_dist_pct > 0 else (wallet_balance * 0.2)
    rec_margin_usd = min(wallet_balance * 0.4, rec_position_usd / default_lev)
    rec_qty = (rec_margin_usd * default_lev) / price

    dec = 6 if price < 0.01 else (5 if price < 1 else (4 if price < 10 else 2))

    return {
        "Coin": f"{coin}/USDT",
        "Signal": signal,
        "Confidence": score,
        "Current Price": round(price, dec),
        "Entry Zone": f"{round(entry_low, dec)} - {round(entry_high, dec)}",
        "Stop-Loss (SL)": round(sl, dec) if sl else 0,
        "Take-Profit 1": round(tp1, dec) if tp1 else 0,
        "Take-Profit 2": round(tp2, dec) if tp2 else 0,
        "Margin ($)": round(rec_margin_usd, 1),
        "Buy/Sell Qty": round(rec_qty, 2) if price >= 1 else int(rec_qty),
        "24h %": round(pct_24h, 2),
        "Vol ($M)": round(q_vol / 1e6, 1),
        "RSI": round(rsi, 1),
        "Setup Reason": reason if reason else "Consolidating / No Clear Trend"
    }

# --- RUN FULL MARKET SCAN ---
coins, total_eligible = get_all_active_coins(min_vol_m * 1_000_000, scan_top_n)

if not coins:
    st.warning("⏳ Market data load ho raha hai. Thori der baad page refresh karein.")
else:
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(analyze_setup, coins))
    results = [r for r in results if r is not None]

    df = pd.DataFrame(results).sort_values(by=["Confidence", "Vol ($M)"], ascending=False)
    active_setups = df[df["Signal"] != "WAIT"]

    # Top Summary Metrics
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Coins Scanned", f"{len(results)} / {total_eligible}")
    m2.metric("Ready Trade Setups", f"{len(active_setups)}")
    m3.metric("Max Risk Per Trade", f"${wallet_balance * (risk_pct/100):.2f} ({risk_pct}%)")
    m4.metric("Mode & Leverage", f"ISOLATED {default_lev}x")

    st.divider()

    # --- SECTION 1: READY-TO-COPY MANUAL TRADE CARDS ---
    st.subheader(f"🔥 Top Ready-To-Trade Setups ({len(active_setups)} Signals Found)")
    if active_setups.empty:
        st.info("Abhi market side-ways hai — koi high-confidence setup nahi mila. 5–10 minute baad refresh karein.")
    else:
        for _, row in active_setups.head(6).iterrows():
            with st.container(border=True):
                c1, c2, c3, c4, c5 = st.columns([1.4, 1.5, 1.5, 1.5, 1.8])
                c1.markdown(f"### {row['Coin']}\n**{row['Signal']}** | Score: **{row['Confidence']}%**")
                c2.markdown(f"**🎯 Entry Zone:**\n`{row['Entry Zone']}`\n\n*Current:* `{row['Current Price']}`")
                c3.markdown(f"**🛑 Stop-Loss:**\n`{row['Stop-Loss (SL)']}`\n\n**✅ TP 1:** `{row['Take-Profit 1']}`\n\n**🚀 TP 2:** `{row['Take-Profit 2']}`")
                c4.markdown(f"**💵 Kitna Lagayein:**\nMargin: **${row['Margin ($)'] } USDT**\n\nMode: **Isolated {default_lev}x**\n\nQty: `{row['Buy/Sell Qty']}`")
                c5.markdown(f"**📊 Technical Reason:**\n{row['Setup Reason']}\n\nRSI: `{row['RSI']}` | 24h: `{row['24h %']}%`")

    st.divider()

    # --- SECTION 2: FULL MARKET SCAN TABLE ---
    st.subheader("📋 Complete Market Scan Table")
    st.dataframe(df, use_container_width=True, hide_index=True)

if auto_refresh:
    time.sleep(60)
    st.rerun()
