import streamlit as st
import requests
import pandas as pd
import ccxt
import time

st.set_page_config(page_title="AI Crypto Auto-Trader", page_icon="🤖", layout="wide")
st.title("🤖 Full-Market Crypto Scanner & Auto-Executor (Spot & Futures)")

# --- SIDEBAR SETTINGS ---
st.sidebar.header("1️⃣ Exchange & Mode")
exec_mode = st.sidebar.selectbox("Execution Mode", [
    "🔍 Scan & Signals Only (No Trade)",
    "🧪 Binance Testnet (Demo Money Auto-Trade)",
    "💰 Binance Real Account (Live Auto-Trade)"
])
market_type = st.sidebar.radio("Market Type", ["futures", "spot"], index=0)
timeframe = st.sidebar.selectbox("Candle Timeframe", ["1m", "5m", "15m", "1h", "4h"], index=1)

st.sidebar.header("2️⃣ Risk & Auto-Trade Rules")
trade_usdt = st.sidebar.number_input("Margin Per Trade ($ USDT)", min_value=10, max_value=5000, value=15, step=5)
leverage = st.sidebar.slider("Futures Leverage (Isolated)", min_value=1, max_value=5, value=3)
max_open_trades = st.sidebar.slider("Max Open Trades at Once", min_value=1, max_value=5, value=2)
auto_run = st.sidebar.checkbox("🔄 Auto-Scan & Execute Every 30 Seconds", value=False)

api_key = st.sidebar.text_input("Binance API Key", type="password")
api_secret = st.sidebar.text_input("Binance API Secret", type="password")

WATCHLIST = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "BNB/USDT",
    "DOGE/USDT", "PEPE/USDT", "WIF/USDT", "BONK/USDT", "SHIB/USDT",
    "SUI/USDT", "AVAX/USDT", "NEAR/USDT", "LINK/USDT", "ARB/USDT"
]

def get_exchange():
    if not api_key or not api_secret:
        return None
    is_testnet = "Testnet" in exec_mode
    config = {
        "apiKey": api_key.strip(),
        "secret": api_secret.strip(),
        "enableRateLimit": True,
        "options": {
            "defaultType": "future" if market_type == "futures" else "spot",
            "adjustForTimeDifference": True,
            "fetchCurrencies": False
        }
    }
    exchange = ccxt.binance(config)
    if is_testnet:
        exchange.set_sandbox_mode(True)
    return exchange

def fetch_candles_safe(clean_sym, tf):
    urls = [
        f"https://data-api.binance.vision/api/v3/klines?symbol={clean_sym}&interval={tf}&limit=80",
        f"https://api.binance.us/api/v3/klines?symbol={clean_sym}&interval={tf}&limit=80"
    ]
    for u in urls:
        try:
            r = requests.get(u, timeout=6).json()
            if isinstance(r, list) and len(r) >= 30:
                return r
        except Exception:
            continue
    return None

def analyze_pair(symbol, tf):
    try:
        clean_sym = symbol.replace("/", "")
        data = fetch_candles_safe(clean_sym, tf)
        if not data:
            return None

        closes = pd.Series([float(x[4]) for x in data])
        highs = pd.Series([float(x[2]) for x in data])
        lows = pd.Series([float(x[3]) for x in data])
        vols = pd.Series([float(x[5]) for x in data])

        price = closes.iloc[-1]
        ema9 = closes.ewm(span=9).mean().iloc[-1]
        ema21 = closes.ewm(span=21).mean().iloc[-1]

        delta = closes.diff()
        gain = delta.clip(lower=0).rolling(14).mean().iloc[-1]
        loss = (-delta.clip(upper=0)).rolling(14).mean().iloc[-1]
        rsi = 100 - (100 / (1 + (gain / loss if loss != 0 else 0)))

        vol_spike = vols.iloc[-1] > (vols.rolling(20).mean().iloc[-1] * 1.2)
        atr = (highs - lows).rolling(14).mean().iloc[-1]

        signal = "WAIT"
        sl, tp = 0.0, 0.0

        if (price > ema9 > ema21 and 50 <= rsi <= 68 and vol_spike) or (rsi < 32 and price > closes.iloc[-2]):
            signal = "LONG"
            sl = price - (1.8 * atr)
            tp = price + (3.2 * atr)
        elif market_type == "futures" and ((price < ema9 < ema21 and 32 <= rsi <= 50 and vol_spike) or (rsi > 72 and price < closes.iloc[-2])):
            signal = "SHORT"
            sl = price + (1.8 * atr)
            tp = price - (3.2 * atr)

        return {
            "Pair": symbol,
            "Price": price,
            "Signal": signal,
            "RSI": round(rsi, 1),
            "Vol Spike": "✅" if vol_spike else "—",
            "Entry": round(price, 6),
            "Stop-Loss": round(sl, 6) if sl else 0,
            "Take-Profit": round(tp, 6) if tp else 0
        }
    except Exception:
        return None

def execute_auto_trade(exchange, row):
    symbol = row["Pair"]
    side = "buy" if row["Signal"] == "LONG" else "sell"
    close_side = "sell" if side == "buy" else "buy"
    price = row["Price"]

    # Load markets first so precision & symbol info work
    markets = exchange.load_markets()
    market_sym = symbol if symbol in markets else f"{symbol}:USDT"

    if market_type == "futures":
        try:
            exchange.set_margin_mode("isolated", market_sym)
        except Exception:
            pass
        try:
            exchange.set_leverage(leverage, market_sym)
        except Exception:
            pass
        amount = (trade_usdt * leverage) / price
    else:
        amount = trade_usdt / price

    amount = float(exchange.amount_to_precision(market_sym, amount))
    sl_price = float(exchange.price_to_precision(market_sym, row["Stop-Loss"]))
    tp_price = float(exchange.price_to_precision(market_sym, row["Take-Profit"]))

    order = exchange.create_market_order(market_sym, side, amount)
    if market_type == "futures":
        try:
            exchange.create_order(market_sym, "STOP_MARKET", close_side, amount, params={"stopPrice": sl_price, "reduceOnly": True})
            exchange.create_order(market_sym, "TAKE_PROFIT_MARKET", close_side, amount, params={"stopPrice": tp_price, "reduceOnly": True})
        except Exception as e:
            st.warning(f"Main order placed, SL/TP note: {e}")
    return order["id"]

# --- MAIN EXECUTION ---
rows = [analyze_pair(sym, timeframe) for sym in WATCHLIST]
rows = [r for r in rows if r is not None]

if not rows:
    st.warning("⏳ Market data load ho raha hai. Thori der baad page refresh karein.")
else:
    df = pd.DataFrame(rows)
    opportunities = df[df["Signal"] != "WAIT"]
    st.subheader(f"🎯 Live Opportunities Found: {len(opportunities)} (Timeframe: {timeframe} | {market_type.upper()})")
    st.dataframe(df, use_container_width=True, hide_index=True)

    if "Scan & Signals Only" not in exec_mode:
        ex = get_exchange()
        if not ex:
            st.warning("⚠️ Left sidebar mein Binance API Key aur Secret daalein.")
        elif not opportunities.empty:
            for _, opp in opportunities.head(max_open_trades).iterrows():
                if st.button(f"⚡ Execute {opp['Signal']} on {opp['Pair']}") or auto_run:
                    try:
                        oid = execute_auto_trade(ex, opp)
                        st.success(f"✅ Order Placed: {opp['Signal']} {opp['Pair']} | Entry: {opp['Entry']} | SL: {opp['Stop-Loss']} | TP: {opp['Take-Profit']} (ID: {oid})")
                    except Exception as e:
                        st.error(f"❌ {opp['Pair']} Error: {e}")

if auto_run:
    time.sleep(30)
    st.rerun()
