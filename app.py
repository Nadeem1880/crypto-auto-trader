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
    exchange = ccxt.binance({
        "apiKey": api_key.strip(),
        "secret": api_secret.strip(),
        "enableRateLimit": True,
        "options": {
            "defaultType": "future" if market_type == "futures" else "spot",
            "adjustForTimeDifference": True,
            "fetchCurrencies": False,
        }
    })
    if is_testnet:
        exchange.set_sandbox_mode(True)
        # Override blocked spot testnet URL with Futures Testnet / Demo endpoints
        if market_type == "futures":
            exchange.urls["api"]["fapiPublic"] = "https://testnet.binancefuture.com/fapi/v1"
            exchange.urls["api"]["fapiPrivate"] = "https://testnet.binancefuture.com/fapi/v1"
            exchange.urls["api"]["fapiPrivateV2"] = "https://testnet.binancefuture.com/fapi/v2"
            exchange.urls["api"]["public"] = "https://testnet.binancefuture.com/fapi/v1"
            exchange.urls["api"]["private"] = "https://testnet.binancefuture.com/fapi/v1"
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
    clean_sym = symbol.replace("/", "")
    side = "BUY" if row["Signal"] == "LONG" else "SELL"
    close_side = "SELL" if side == "BUY" else "BUY"
    price = row["Price"]

    if market_type == "futures":
        # Direct Futures API call (bypasses blocked spot exchangeInfo endpoint)
        try:
            exchange.fapiPrivatePostMarginType({"symbol": clean_sym, "marginType": "ISOLATED"})
        except Exception:
            pass
        try:
            exchange.fapiPrivatePostLeverage({"symbol": clean_sym, "leverage": leverage})
        except Exception:
            pass

        raw_qty = (trade_usdt * leverage) / price
        qty = round(raw_qty, 3) if price < 10 else (round(raw_qty, 2) if price < 500 else round(raw_qty, 3))
        if qty <= 0:
            qty = 1

        order = exchange.fapiPrivatePostOrder({
            "symbol": clean_sym,
            "side": side,
            "type": "MARKET",
            "quantity": qty
        })
        sl_price = round(row["Stop-Loss"], 4 if price < 10 else 2)
        tp_price = round(row["Take-Profit"], 4 if price < 10 else 2)
        try:
            exchange.fapiPrivatePostOrder({
                "symbol": clean_sym, "side": close_side, "type": "STOP_MARKET",
                "stopPrice": sl_price, "closePosition": "true"
            })
            exchange.fapiPrivatePostOrder({
                "symbol": clean_sym, "side": close_side, "type": "TAKE_PROFIT_MARKET",
                "stopPrice": tp_price, "closePosition": "true"
            })
        except Exception as e:
            st.warning(f"Trade opened, SL/TP note: {e}")
        return order.get("orderId", "OK")
    else:
        markets = exchange.load_markets()
        amount = float(exchange.amount_to_precision(symbol, trade_usdt / price))
        order = exchange.create_market_order(symbol, side.lower(), amount)
        return order["id"]

# --- 1. LIVE ACCOUNT & PROFIT/LOSS DASHBOARD ---
ex = get_exchange()
if ex and market_type == "futures":
    st.subheader("💰 Live Wallet, Open Positions & Profit / Loss (PnL)")
    try:
        acc = ex.fapiPrivateV2GetAccount()
        total_bal = float(acc.get("totalWalletBalance", 0))
        unrealized_pnl = float(acc.get("totalUnrealizedProfit", 0))
        avail_bal = float(acc.get("availableBalance", 0))

        positions = [p for p in acc.get("positions", []) if float(p.get("positionAmt", 0)) != 0]

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Wallet Balance", f"${total_bal:,.2f} USDT")
        c2.metric("Live Profit / Loss (PnL)", f"${unrealized_pnl:+,.2f} USDT", delta=f"{unrealized_pnl:+.2f}$")
        c3.metric("Available Margin", f"${avail_bal:,.2f} USDT")
        c4.metric("Active Open Trades", f"{len(positions)} / {max_open_trades}")

        if positions:
            pos_list = []
            for p in positions:
                amt = float(p.get("positionAmt", 0))
                pos_list.append({
                    "Coin": p.get("symbol"),
                    "Side": "🟢 LONG" if amt > 0 else "🔴 SHORT",
                    "Size": abs(amt),
                    "Entry Price": round(float(p.get("entryPrice", 0)), 5),
                    "Live PnL ($)": round(float(p.get("unrealizedProfit", 0)), 3),
                    "Leverage": f"{p.get('leverage')}x"
                })
            st.dataframe(pd.DataFrame(pos_list), use_container_width=True, hide_index=True)
        else:
            st.info("Abhi koi active trade open nahi hai.")
    except Exception as e:
        st.error(f"Account Balance Fetch Error: {e}")
    st.divider()

# --- 2. MARKET SCANNER & AUTO-EXECUTION ---
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
