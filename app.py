import streamlit as st
import requests
import pandas as pd
import ccxt
import time
from concurrent.futures import ThreadPoolExecutor

st.set_page_config(page_title="AI Crypto Auto-Trader (All Binance Coins)", page_icon="🤖", layout="wide")
st.title("🤖 Full-Market Binance Scanner & Auto-Executor (All Coins)")

# --- SIDEBAR SETTINGS ---
st.sidebar.header("1️⃣ Exchange & Mode")
exec_mode = st.sidebar.selectbox("Execution Mode", [
    "🔍 Scan & Signals Only (No Trade)",
    "🧪 Binance Demo Trading (Virtual $ Auto-Trade)",
    "💰 Binance Real Account (Live Auto-Trade)"
])
market_type = st.sidebar.radio("Market Type", ["futures", "spot"], index=0)
margin_mode = st.sidebar.radio("Margin Mode", ["isolated", "cross"], index=0)
timeframe = st.sidebar.selectbox("Candle Timeframe", ["1m", "5m", "15m", "1h", "4h"], index=1)

st.sidebar.header("2️⃣ Full-Market Filter Settings")
min_vol_m = st.sidebar.slider("Min 24h Volume ($ Millions)", min_value=5, max_value=200, value=20, step=5)
scan_top_n = st.sidebar.slider("Top Active Coins to Deep-Scan", min_value=20, max_value=100, value=50, step=10)

st.sidebar.header("3️⃣ Risk & Auto-Trade Rules")
trade_usdt = st.sidebar.number_input("Margin Per Trade ($ USDT)", min_value=10, max_value=5000, value=15, step=5)
leverage = st.sidebar.slider("Futures Leverage", min_value=1, max_value=10, value=3)
max_open_trades = st.sidebar.slider("Max Open Trades at Once", min_value=1, max_value=5, value=2)
auto_run = st.sidebar.checkbox("🔄 Auto-Scan & Execute Every 30 Seconds", value=False)

api_key = st.sidebar.text_input("Binance API Key", type="password")
api_secret = st.sidebar.text_input("Binance API Secret", type="password")

def get_exchange():
    if not api_key or not api_secret:
        return None
    is_demo = "Demo" in exec_mode
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
    if is_demo:
        if hasattr(exchange, "enable_demo_trading"):
            exchange.enable_demo_trading(True)
        else:
            exchange.urls["api"]["fapiPublic"] = "https://demo-fapi.binance.com/fapi/v1"
            exchange.urls["api"]["fapiPrivate"] = "https://demo-fapi.binance.com/fapi/v1"
            exchange.urls["api"]["fapiPrivateV2"] = "https://demo-fapi.binance.com/fapi/v2"
            exchange.urls["api"]["public"] = "https://demo-api.binance.com/api/v3"
            exchange.urls["api"]["private"] = "https://demo-api.binance.com/api/v3"
    return exchange

def get_all_active_binance_coins(min_volume_usd, limit_n):
    """Fetches ALL Binance USDT coins in 1 fast call & filters high-volume active movers."""
    urls = [
        "https://fapi.binance.com/fapi/v1/ticker/24hr",
        "https://fapi.binance.com/fapi/v1/ticker/24hr"
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
                pct_chg = abs(float(t.get("priceChangePercent", 0)))
                if q_vol >= min_volume_usd:
                    valid.append((f"{base}/USDT", q_vol, pct_chg, float(t.get("priceChangePercent", 0))))
            # Sort by highest volatility + volume activity so best opportunities come first
            valid.sort(key=lambda x: (x[2] * 0.7 + (x[1] / 1e8) * 0.3), reverse=True)
            return valid[:limit_n], len(valid)
        except Exception:
            continue
    return [], 0

def fetch_candles_safe(clean_sym, tf):
    urls = [
        f"https://data-api.binance.vision/api/v3/klines?symbol={clean_sym}&interval={tf}&limit=60",
        f"https://api.binance.us/api/v3/klines?symbol={clean_sym}&interval={tf}&limit=60"
    ]
    for u in urls:
        try:
            r = requests.get(u, timeout=5).json()
            if isinstance(r, list) and len(r) >= 30:
                return r
        except Exception:
            continue
    return None

def analyze_pair(item):
    symbol, q_vol, _, pct_24h = item
    try:
        clean_sym = symbol.replace("/", "")
        data = fetch_candles_safe(clean_sym, timeframe)
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

        vol_spike = vols.iloc[-1] > (vols.rolling(20).mean().iloc[-1] * 1.25)
        atr = (highs - lows).rolling(14).mean().iloc[-1]

        signal = "WAIT"
        sl, tp = 0.0, 0.0
        strength = 0

        if (price > ema9 > ema21 and 50 <= rsi <= 68 and vol_spike) or (rsi < 30 and price > closes.iloc[-2]):
            signal = "LONG"
            sl = price - (1.8 * atr)
            tp = price + (3.2 * atr)
            strength = abs(50 - rsi) + (15 if vol_spike else 0)
        elif market_type == "futures" and ((price < ema9 < ema21 and 32 <= rsi <= 50 and vol_spike) or (rsi > 73 and price < closes.iloc[-2])):
            signal = "SHORT"
            sl = price + (1.8 * atr)
            tp = price - (3.2 * atr)
            strength = abs(rsi - 50) + (15 if vol_spike else 0)

        return {
            "Pair": symbol,
            "Price": price,
            "24h %": round(pct_24h, 2),
            "24h Vol ($M)": round(q_vol / 1e6, 1),
            "Signal": signal,
            "RSI": round(rsi, 1),
            "Vol Spike": "✅" if vol_spike else "—",
            "Entry": round(price, 6),
            "Stop-Loss": round(sl, 6) if sl else 0,
            "Take-Profit": round(tp, 6) if tp else 0,
            "Strength": round(strength, 1)
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
        try:
            exchange.fapiPrivatePostMarginType({"symbol": clean_sym, "marginType": margin_mode.upper()})
        except Exception:
            pass
        try:
            exchange.fapiPrivatePostLeverage({"symbol": clean_sym, "leverage": leverage})
        except Exception:
            pass

        raw_qty = (trade_usdt * leverage) / price
        if price < 0.001:
            qty = int(raw_qty)
        elif price < 1:
            qty = round(raw_qty, 0)
        elif price < 10:
            qty = round(raw_qty, 1)
        elif price < 500:
            qty = round(raw_qty, 2)
        else:
            qty = round(raw_qty, 3)
        if qty <= 0:
            qty = 1

        order = exchange.fapiPrivatePostOrder({
            "symbol": clean_sym,
            "side": side,
            "type": "MARKET",
            "quantity": qty
        })
        sl_price = round(row["Stop-Loss"], 6 if price < 0.01 else (5 if price < 1 else (4 if price < 10 else 2)))
        tp_price = round(row["Take-Profit"], 6 if price < 0.01 else (5 if price < 1 else (4 if price < 10 else 2)))
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
        exchange.load_markets()
        amount = float(exchange.amount_to_precision(symbol, trade_usdt / price))
        order = exchange.create_market_order(symbol, side.lower(), amount)
        return order["id"]

# --- 1. LIVE ACCOUNT, OPEN TRADES & PROFIT/LOSS DASHBOARD ---
ex = get_exchange()
active_symbols = []
if ex and market_type == "futures":
    st.subheader("💰 Live Portfolio, Open Positions & Profit / Loss (PnL)")
    try:
        acc = ex.fapiPrivateV2GetAccount()
        total_bal = float(acc.get("totalWalletBalance", 0))
        unrealized_pnl = float(acc.get("totalUnrealizedProfit", 0))
        avail_bal = float(acc.get("availableBalance", 0))

        pos_risk = ex.fapiPrivateV2GetPositionRisk()
        open_positions = [p for p in pos_risk if float(p.get("positionAmt", 0)) != 0]
        active_symbols = [p.get("symbol") for p in open_positions]

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Wallet Balance", f"${total_bal:,.2f} USDT")
        c2.metric("Live Profit / Loss (PnL)", f"${unrealized_pnl:+,.2f} USDT", delta=f"{unrealized_pnl:+.2f}$")
        c3.metric("Available Margin", f"${avail_bal:,.2f} USDT")
        c4.metric("Active Open Trades", f"{len(open_positions)} / {max_open_trades}")

        if open_positions:
            pos_list = []
            for p in open_positions:
                amt = float(p.get("positionAmt", 0))
                entry_p = float(p.get("entryPrice", 0))
                mark_p = float(p.get("markPrice", 0))
                lev = int(float(p.get("leverage", 1)))
                pnl = float(p.get("unRealizedProfit", 0))
                m_type = p.get("marginType", "isolated").upper()
                notional = abs(amt) * entry_p
                margin_used = notional / lev if lev > 0 else notional
                roe_pct = (pnl / margin_used * 100) if margin_used > 0 else 0

                pos_list.append({
                    "Coin": p.get("symbol"),
                    "Direction": "🟢 LONG" if amt > 0 else "🔴 SHORT",
                    "Mode & Lev": f"{m_type} {lev}x",
                    "Margin Invested ($)": round(margin_used, 2),
                    "Total Size ($)": round(notional, 2),
                    "Entry Price": round(entry_p, 6),
                    "Current Price": round(mark_p, 6),
                    "Liq. Price": round(float(p.get("liquidationPrice", 0)), 6),
                    "Profit / Loss ($)": f"{pnl:+.3f} ({roe_pct:+.1f}%)"
                })
            st.dataframe(pd.DataFrame(pos_list), use_container_width=True, hide_index=True)

            close_cols = st.columns(len(open_positions))
            for idx, p in enumerate(open_positions):
                sym_c = p.get("symbol")
                amt_c = float(p.get("positionAmt", 0))
                with close_cols[idx]:
                    if st.button(f"❌ Close {sym_c} Trade"):
                        c_side = "SELL" if amt_c > 0 else "BUY"
                        ex.fapiPrivatePostOrder({
                            "symbol": sym_c, "side": c_side, "type": "MARKET",
                            "quantity": abs(amt_c), "reduceOnly": "true"
                        })
                        st.success(f"Closed {sym_c}!")
                        st.rerun()
        else:
            st.info("Abhi koi active trade open nahi hai — neeche full-market scanner se signal milte hi trade open hogi.")
    except Exception as e:
        st.error(f"Account Balance Fetch Error: {e}")
    st.divider()

# --- 2. FULL BINANCE MARKET SCANNER & AUTO-EXECUTION ---
active_coins, total_eligible = get_all_active_binance_coins(min_vol_m * 1_000_000, scan_top_n)

if not active_coins:
    st.warning("⏳ Binance full market data load ho raha hai. Thori der baad refresh karein.")
else:
    # Parallel fast scan across top active coins
    with ThreadPoolExecutor(max_workers=12) as pool:
        rows = list(pool.map(analyze_pair, active_coins))
    rows = [r for r in rows if r is not None]

    df = pd.DataFrame(rows).sort_values(by=["Strength", "24h Vol ($M)"], ascending=False)
    opportunities = df[df["Signal"] != "WAIT"]

    st.subheader(f"🌐 Scanned {len(rows)} Active Coins (out of {total_eligible} Binance Coins >${min_vol_m}M Vol) | 🎯 Signals Found: {len(opportunities)}")
    st.dataframe(df.drop(columns=["Strength"]), use_container_width=True, hide_index=True)

    if "Scan & Signals Only" not in exec_mode:
        if not ex:
            st.warning("⚠️ Left sidebar mein Binance API Key aur Secret daalein.")
        elif not opportunities.empty:
            for _, opp in opportunities.head(max_open_trades).iterrows():
                clean_opp_sym = opp["Pair"].replace("/", "")
                already_open = clean_opp_sym in active_symbols
                can_open_more = len(active_symbols) < max_open_trades

                if st.button(f"⚡ Execute {opp['Signal']} on {opp['Pair']}") or (auto_run and not already_open and can_open_more):
                    try:
                        oid = execute_auto_trade(ex, opp)
                        st.success(f"✅ Order Placed: {opp['Signal']} {opp['Pair']} | Margin: ${trade_usdt} ({margin_mode.upper()} {leverage}x) | SL: {opp['Stop-Loss']} | TP: {opp['Take-Profit']} (ID: {oid})")
                        active_symbols.append(clean_opp_sym)
                    except Exception as e:
                        st.error(f"❌ {opp['Pair']} Error: {e}")

if auto_run:
    time.sleep(30)
    st.rerun()
