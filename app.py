from __future__ import annotations

import asyncio
import queue
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from signal_engine import PullbackEngine, IST

st.set_page_config(page_title="Fib Pullback Signal Scanner", page_icon="📐", layout="wide")

INDEXES = [
    {"label": "NIFTY", "feed_exchange": "nse_cm", "feed_name": "Nifty 50", "fo_exchange": "nse_fo", "underlying": "NIFTY"},
    {"label": "BANKNIFTY", "feed_exchange": "nse_cm", "feed_name": "Nifty Bank", "fo_exchange": "nse_fo", "underlying": "BANKNIFTY"},
    {"label": "FINNIFTY", "feed_exchange": "nse_cm", "feed_name": "Nifty Fin Service", "fo_exchange": "nse_fo", "underlying": "FINNIFTY"},
    {"label": "MIDCPNIFTY", "feed_exchange": "nse_cm", "feed_name": "Nifty Midcap Select", "fo_exchange": "nse_fo", "underlying": "MIDCPNIFTY"},
    {"label": "SENSEX", "feed_exchange": "bse_cm", "feed_name": "SENSEX", "fo_exchange": "bse_fo", "underlying": "SENSEX"},
]


def safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def choose_atm_contract(items):
    if not items:
        return None
    atms = [x for x in items if str(x.get("instrument", {}).get("moneyness", "")).upper() == "ATM"]
    return (atms or items)[0]


def kotak_worker(credentials, selected_indexes, output_queue, stop_event):
    """Runs Kotak login + SFeed in a background thread. No order methods are called."""
    client = None
    try:
        from neo_api_client import NeoAPI
        from neo_api_client.websocket.feed import WsToken, SFeedIndex, SFeedScrip

        client = NeoAPI(consumer_key=credentials["consumer_key"], environment="prod")
        client.totp_login(mobile_number=credentials["mobile"], ucc=credentials["ucc"], totp=credentials["totp"])
        output_queue.put({"kind": "status", "message": "Kotak TOTP login submitted."})
        client.totp_validate(mpin=credentials["mpin"])
        output_queue.put({"kind": "status", "message": "Kotak session validated. Connecting live feed..."})

        option_tokens = []
        option_lookup = {}
        for item in selected_indexes:
            try:
                chain = client.option_chain(exchange=item["fo_exchange"], underlying=item["underlying"], instrument_type="option", count=20)
                data = (chain or {}).get("data", {})
                for side in ("call", "put"):
                    contract = choose_atm_contract(data.get(side, []))
                    if not contract:
                        continue
                    inst = contract.get("instrument", {})
                    neo_symbol = str(inst.get("neoSymbol", ""))
                    if "|" not in neo_symbol:
                        continue
                    exchange, token = neo_symbol.split("|", 1)
                    token = token.strip()
                    if not token:
                        continue
                    option_tokens.append(WsToken(exchange, token))
                    option_lookup[token] = {"index": item["label"], "side": inst.get("optionType", "CE" if side == "call" else "PE"), "symbol": inst.get("symbol", ""), "strike": inst.get("strikePrice", ""), "expiry": data.get("common_data", {}).get("expiryDt", "nearest")}
            except Exception as exc:
                output_queue.put({"kind": "warning", "message": f"Option-chain lookup skipped for {item['label']}: {exc}"})

        async def stream():
            tokens = [WsToken(x["feed_exchange"], x["feed_name"]) for x in selected_indexes]
            async with client.create_websocket() as ws:
                await ws.subscribe_index(tokens)
                if option_tokens:
                    await ws.subscribe_scrips(option_tokens)
                output_queue.put({"kind": "status", "message": f"Live feed connected. Monitoring {len(tokens)} index(es) and {len(option_tokens)} ATM option(s)."})
                async for message in ws:
                    if stop_event.is_set():
                        break
                    now = datetime.now(IST)
                    if isinstance(message, SFeedIndex):
                        name = str(getattr(message, "name", "") or getattr(message, "trading_symbol", "")).strip().lower()
                        matched = next((x for x in selected_indexes if x["feed_name"].lower() == name), None)
                        if matched is None:
                            matched = next((x for x in selected_indexes if x["label"].lower() in name.replace(" ", "")), None)
                        if matched is None:
                            continue
                        ltp = safe_float(getattr(message, "last_traded_price", None))
                        if ltp is None:
                            continue
                        change = safe_float(getattr(message, "change", None))
                        prev_close = safe_float(getattr(message, "close_price", None))
                        if prev_close is None and change is not None:
                            prev_close = ltp - change
                        day_open = safe_float(getattr(message, "open_price", None))
                        output_queue.put({"kind": "index_tick", "symbol": matched["label"], "ltp": ltp, "day_open": day_open, "prev_close": prev_close, "time": now.isoformat()})
                    elif isinstance(message, SFeedScrip):
                        token = str(getattr(message, "instrument_token", ""))
                        if token not in option_lookup:
                            continue
                        output_queue.put({"kind": "option_tick", **option_lookup[token], "ltp": safe_float(getattr(message, "last_traded_price", None)), "time": now.isoformat()})
        asyncio.run(stream())
    except Exception as exc:
        output_queue.put({"kind": "error", "message": f"{type(exc).__name__}: {exc}"})
    finally:
        try:
            if client is not None:
                client.logout()
        except Exception:
            pass
        output_queue.put({"kind": "status", "message": "Kotak feed worker stopped."})


def send_telegram(token, chat_id, text):
    if not token or not chat_id:
        return
    try:
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={"chat_id": chat_id, "text": text}, timeout=8)
    except Exception:
        pass


def make_chart(snapshot):
    rows = snapshot.get("candles5", [])
    if not rows:
        return None
    df = pd.DataFrame(rows)
    if df.empty:
        return None
    fig = go.Figure(data=[go.Candlestick(x=pd.to_datetime(df["time"]), open=df["open"], high=df["high"], low=df["low"], close=df["close"], name="5-minute")])
    for z in snapshot.get("zones", []):
        fig.add_hrect(y0=z["lower"], y1=z["upper"], fillcolor="rgba(59,130,246,0.13)" if z["direction"] == "BULLISH" else "rgba(239,68,68,0.13)", line_width=0, annotation_text=f'{z["label"]} 50–61.8%', annotation_position="top left")
    fig.update_layout(height=430, margin=dict(l=10, r=10, t=25, b=10), xaxis_rangeslider_visible=False, template="plotly_dark", yaxis_title="Index level")
    return fig


def get_saved_kotak_details():
    """Read Consumer Key, mobile and UCC from Streamlit app secrets when configured."""
    try:
        saved = st.secrets.get("kotak", {})
        return {"consumer_key": str(saved.get("consumer_key", "")), "mobile": str(saved.get("mobile", "")), "ucc": str(saved.get("ucc", ""))}
    except Exception:
        return {"consumer_key": "", "mobile": "", "ucc": ""}


def init_state():
    defaults = {"monitor_queue": queue.Queue(), "stop_event": threading.Event(), "worker": None, "running": False, "engines": {}, "prices": {}, "options": {}, "alerts": [], "messages": [], "selected": [x["label"] for x in INDEXES[:4]], "telegram_token": "", "telegram_chat": ""}
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


init_state()
st_autorefresh(interval=1200, key="refresh_scanner")
st.title("📐 Fibonacci Pullback Signal Scanner")
st.caption("Kotak Neo live index feed • 5-minute swing detection • 15-minute rejection confirmation • signal-only")

with st.sidebar:
    st.header("Connection")
    saved_kotak = get_saved_kotak_details()
    if all(saved_kotak.values()):
        st.success("Consumer Key, mobile and UCC loaded securely from app secrets.")
    else:
        st.caption("Consumer Key, mobile and UCC can be remembered securely using Streamlit app secrets. They are never stored in GitHub.")
    consumer_key = st.text_input("Kotak Neo Consumer Key", value=saved_kotak["consumer_key"], type="password", autocomplete="off")
    mobile = st.text_input("Registered mobile (+91...)", value=saved_kotak["mobile"], autocomplete="off")
    ucc = st.text_input("UCC", value=saved_kotak["ucc"], autocomplete="off")
    totp = st.text_input("Current 6-digit TOTP (enter each login)", max_chars=6, type="password", autocomplete="off")
    mpin = st.text_input("Trading MPIN (enter each login)", type="password", autocomplete="off")
    st.divider()
    st.subheader("Indices")
    selected_labels = st.multiselect("Choose indices", [x["label"] for x in INDEXES], default=st.session_state["selected"])
    st.caption("SENSEX is included if your Kotak account/feed supports its index and option contracts.")
    st.divider()
    st.subheader("Telegram alerts (optional)")
    tg_token = st.text_input("Bot token", type="password", value=st.session_state["telegram_token"])
    tg_chat = st.text_input("Chat ID", value=st.session_state["telegram_chat"])
    st.session_state["telegram_token"] = tg_token
    st.session_state["telegram_chat"] = tg_chat
    start_col, stop_col = st.columns(2)
    start_clicked = start_col.button("Start", type="primary", use_container_width=True, disabled=st.session_state["running"])
    stop_clicked = stop_col.button("Stop", use_container_width=True, disabled=not st.session_state["running"])
    if start_clicked:
        if not all([consumer_key, mobile, ucc, totp, mpin]):
            st.error("Enter all five Kotak credentials to connect.")
        elif not selected_labels:
            st.error("Select at least one index.")
        else:
            st.session_state["selected"] = selected_labels
            st.session_state["engines"] = {label: PullbackEngine(label) for label in selected_labels}
            st.session_state["prices"] = {}
            st.session_state["options"] = {}
            st.session_state["alerts"] = []
            st.session_state["messages"] = []
            st.session_state["stop_event"] = threading.Event()
            selected = [x for x in INDEXES if x["label"] in selected_labels]
            creds = {"consumer_key": consumer_key, "mobile": mobile, "ucc": ucc, "totp": totp, "mpin": mpin}
            thread = threading.Thread(target=kotak_worker, args=(creds, selected, st.session_state["monitor_queue"], st.session_state["stop_event"]), daemon=True)
            st.session_state["worker"] = thread
            thread.start()
            st.session_state["running"] = True
            st.rerun()
    if stop_clicked:
        st.session_state["stop_event"].set()
        st.session_state["running"] = False
        st.info("Stop requested. The feed loop will close after its next update.")

q = st.session_state["monitor_queue"]
while True:
    try:
        item = q.get_nowait()
    except queue.Empty:
        break
    kind = item.get("kind")
    if kind == "index_tick":
        symbol = item["symbol"]
        st.session_state["prices"][symbol] = item
        engine = st.session_state["engines"].get(symbol)
        if engine:
            events = engine.update_tick(item["ltp"], datetime.fromisoformat(item["time"]), item.get("day_open"), item.get("prev_close"))
            for event in events:
                st.session_state["alerts"].insert(0, event)
                st.session_state["alerts"] = st.session_state["alerts"][:100]
                send_telegram(st.session_state["telegram_token"], st.session_state["telegram_chat"], f'{event["symbol"]} | {event["type"]} | {event["message"]}')
    elif kind == "option_tick":
        st.session_state["options"][f'{item["index"]} {item["side"]}'] = item
    elif kind in ("status", "warning", "error"):
        st.session_state["messages"].insert(0, item)
        st.session_state["messages"] = st.session_state["messages"][:30]
        if kind == "error":
            st.session_state["running"] = False

if not st.session_state["running"] and st.session_state["worker"] is None:
    st.info("Enter your Kotak Neo API details in the sidebar and press **Start**. The app does not place orders.")

if st.session_state["messages"]:
    with st.expander("Connection log", expanded=True):
        for message in st.session_state["messages"][:8]:
            if message["kind"] == "error": st.error(message["message"])
            elif message["kind"] == "warning": st.warning(message["message"])
            else: st.caption(message["message"])

selected = st.session_state["selected"]
cols = st.columns(min(5, max(1, len(selected))))
for i, label in enumerate(selected):
    quote = st.session_state["prices"].get(label)
    with cols[i % len(cols)]:
        if quote:
            st.metric(label, f'{quote["ltp"]:,.2f}')
            if quote.get("prev_close"):
                change = quote["ltp"] - quote["prev_close"]
                pct = change / quote["prev_close"] * 100 if quote["prev_close"] else 0
                st.caption(f"{change:+.2f} ({pct:+.2f}%)")
        else:
            st.metric(label, "Waiting for feed")

st.divider()
left, right = st.columns([1.35, 1])
with left:
    st.subheader("Live setup monitor")
    if selected:
        active_symbol = st.selectbox("Select index chart", selected, key="chart_symbol")
        engine = st.session_state["engines"].get(active_symbol)
        if engine:
            snap = engine.snapshot()
            st.caption(snap["status"])
            fig = make_chart(snap)
            if fig: st.plotly_chart(fig, use_container_width=True)
            else: st.info("Waiting for enough 5-minute candles to calculate ATR(14). The initial warm-up needs 15 completed 5-minute candles.")
            if snap["zones"]:
                zone_df = pd.DataFrame([{"Reference": z["label"], "Direction": z["direction"], "50%": round(z["level_50"], 2), "61.8%": round(z["level_618"], 2), "Zone low": round(z["lower"], 2), "Zone high": round(z["upper"], 2)} for z in snap["zones"]])
                st.dataframe(zone_df, use_container_width=True, hide_index=True)
            else: st.caption("No confirmed impulse / Fibonacci zones yet.")
    else:
        st.info("Select an index to view its chart.")

with right:
    st.subheader("Signal alerts")
    if st.session_state["alerts"]:
        for event in st.session_state["alerts"][:15]:
            if event["type"] == "CONFIRMED": st.success(f'**{event["symbol"]} — {event["direction"]} CONFIRMED**\n\n{event["message"]}')
            elif event["type"] == "ZONE": st.warning(f'**{event["symbol"]} — WATCH ZONE**\n\n{event["message"]}')
            else: st.info(f'**{event["symbol"]} — IMPULSE**\n\n{event["message"]}')
    else:
        st.caption("No signals yet. Zone alerts are provisional; entries require a completed 15-minute rejection candle.")

st.divider()
st.subheader("ATM options (nearest expiry)")
if st.session_state["options"]:
    option_rows = []
    for key, value in st.session_state["options"].items():
        option_rows.append({"Index / side": key, "Contract": value.get("symbol"), "Strike": value.get("strike"), "Expiry": value.get("expiry"), "Premium": value.get("ltp")})
    st.dataframe(pd.DataFrame(option_rows), use_container_width=True, hide_index=True)
else:
    st.caption("ATM CE/PE premiums will appear if Kotak returns option-chain contracts for the selected indices.")

with st.expander("Strategy rules and limitations"):
    st.markdown("""
    - **5-minute chart:** live ticks are aggregated into 5-minute candles.
    - **Impulse detection:** ATR(14) with a provisional 1.5 × ATR reversal threshold. The pivot is confirmed only after the reversal threshold is met.
    - **Independent Fibonacci anchors:** previous session close, today's open, and the actual confirmed intraday swing base. An anchor is used only when it lies on the correct side of the impulse endpoint and the move is large enough.
    - **Bullish zone:** 50%–61.8% retracement down from the impulse high. **Bearish zone:** 50%–61.8% bounce up from the impulse low.
    - **Entry confirmation:** a completed 15-minute candle must intersect the Fibonacci zone, close in the trade direction, have a rejection wick at least as large as its body, and close in the outer 40% of its range.
    - **Important:** this first version builds candles from received live ticks. It needs 15 completed 5-minute candles to warm up ATR; no historical candle backfill is included yet.
    - **Options:** nearest-expiry ATM CE and PE are discovered once at startup and their live premiums are displayed. Signals are generated from the index, not option premium. If a contract cannot be discovered, the index scanner continues.
    - This is an experimental signal tool, not a validated profitable strategy. Paper-test and backtest before risking money. It does not place, modify, or cancel orders.
    """)

st.caption("Consumer Key, mobile and UCC may be loaded from Streamlit app secrets; TOTP and MPIN are always manual. Never put brokerage credentials in GitHub.")
