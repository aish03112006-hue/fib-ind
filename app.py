from __future__ import annotations

import asyncio
import queue
import threading
from datetime import datetime, timedelta

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
    {"label": "MIDCPNIFTY", "feed_exchange": "nse_cm", "feed_name": "NIFTY MID SELECT", "fo_exchange": "nse_fo", "underlying": "MIDCPNIFTY"},
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


def parse_history(response):
    candles = ((response or {}).get("data") or {}).get("candles", [])
    rows = []
    for row in candles:
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        try:
            ts = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00")).astimezone(IST)
            rows.append({"time": ts.isoformat(), "open": float(row[1]), "high": float(row[2]), "low": float(row[3]), "close": float(row[4])})
        except (TypeError, ValueError):
            continue
    rows.sort(key=lambda x: x["time"])
    return rows


def kotak_worker(credentials, selected_indexes, output_queue, stop_event):
    """Kotak historical warm-up + live SFeed. This app never calls order APIs."""
    client = None
    try:
        from neo_api_client import NeoAPI
        from neo_api_client.websocket.feed import WsToken, SFeedIndex, SFeedScrip

        client = NeoAPI(consumer_key=credentials["consumer_key"], environment="prod")

        # Historical market-data API only needs the Consumer Key. Backfill today's
        # completed 5-minute index candles before starting the websocket feed.
        today = datetime.now(IST).date().isoformat()
        for item in selected_indexes:
            try:
                neo_symbol = f'{item["feed_exchange"]}|{item["feed_name"]}'
                history = client.historical_data(neosymbol=neo_symbol, interval="5min", from_date=today, to_date=today)
                rows = parse_history(history)
                now = datetime.now(IST)
                current_bucket = now.replace(minute=(now.minute // 5) * 5, second=0, microsecond=0)
                rows = [r for r in rows if datetime.fromisoformat(r["time"]) < current_bucket]
                if rows:
                    output_queue.put({"kind": "history", "symbol": item["label"], "candles": rows})
                    output_queue.put({"kind": "status", "message": f'{item["label"]}: loaded {len(rows)} completed 5-minute candles for ATR warm-up.'})
                else:
                    output_queue.put({"kind": "warning", "message": f'{item["label"]}: Kotak returned no completed 5-minute historical candles; live warm-up will continue.'})
            except Exception as exc:
                output_queue.put({"kind": "warning", "message": f'{item["label"]}: historical backfill unavailable ({exc}); live feed will still start.'})

        client.totp_login(mobile_number=credentials["mobile"], ucc=credentials["ucc"], totp=credentials["totp"])
        output_queue.put({"kind": "status", "message": "Kotak TOTP login submitted."})
        client.totp_validate(mpin=credentials["mpin"])
        output_queue.put({"kind": "status", "message": "Kotak session validated. Connecting live feed..."})

        option_tokens, option_lookup = [], {}
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
                    option_tokens.append(WsToken(exchange, token.strip()))
                    option_lookup[token.strip()] = {"index": item["label"], "side": inst.get("optionType", "CE" if side == "call" else "PE"), "symbol": inst.get("symbol", ""), "strike": inst.get("strikePrice", ""), "expiry": data.get("common_data", {}).get("expiryDt", "nearest")}
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
                        output_queue.put({"kind": "index_tick", "symbol": matched["label"], "ltp": ltp, "day_open": safe_float(getattr(message, "open_price", None)), "prev_close": prev_close, "time": now.isoformat()})
                    elif isinstance(message, SFeedScrip):
                        token = str(getattr(message, "instrument_token", ""))
                        if token in option_lookup:
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


def replay_history(engine, rows):
    """Replay OHLC into the existing tick engine while preserving each 5m OHLC bar."""
    events = []
    for row in rows:
        t = datetime.fromisoformat(row["time"]).astimezone(IST)
        # Four synthetic ticks preserve open/high/low/close exactly. The next bar's
        # first tick closes the previous CandleBuilder bucket.
        for offset, price in ((0, row["open"]), (1, row["high"]), (2, row["low"]), (4, row["close"])):
            events.extend(engine.update_tick(float(price), t + timedelta(minutes=offset)))
    # Force the final historical 5m candle to close without contaminating it.
    if rows:
        last = rows[-1]
        t = datetime.fromisoformat(last["time"]).astimezone(IST) + timedelta(minutes=5)
        events.extend(engine.update_tick(float(last["close"]), t))
    return events


def send_telegram(token, chat_id, text):
    if token and chat_id:
        try:
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={"chat_id": chat_id, "text": text}, timeout=8)
        except Exception:
            pass


def make_chart(snapshot):
    rows = snapshot.get("candles5", [])
    if not rows:
        return None
    df = pd.DataFrame(rows)
    fig = go.Figure(data=[go.Candlestick(x=pd.to_datetime(df["time"]), open=df["open"], high=df["high"], low=df["low"], close=df["close"], name="5-minute")])
    for z in snapshot.get("zones", []):
        fig.add_hrect(y0=z["lower"], y1=z["upper"], fillcolor="rgba(59,130,246,0.13)" if z["direction"] == "BULLISH" else "rgba(239,68,68,0.13)", line_width=0, annotation_text=f'{z["label"]} 50–61.8%', annotation_position="top left")
    fig.update_layout(height=430, margin=dict(l=10, r=10, t=25, b=10), xaxis_rangeslider_visible=False, template="plotly_dark", yaxis_title="Index level")
    return fig


def get_saved_kotak_details():
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
st.caption("Kotak Neo live index feed • historical 5m warm-up • 15-minute rejection confirmation • signal-only")

with st.sidebar:
    st.header("Connection")
    saved_kotak = get_saved_kotak_details()
    consumer_key = st.text_input("Kotak Neo Consumer Key", value=saved_kotak["consumer_key"], type="password", autocomplete="off")
    mobile = st.text_input("Registered mobile (+91...)", value=saved_kotak["mobile"], autocomplete="off")
    ucc = st.text_input("UCC", value=saved_kotak["ucc"], autocomplete="off")
    totp = st.text_input("Current 6-digit TOTP (enter each login)", max_chars=6, type="password", autocomplete="off")
    mpin = st.text_input("Trading MPIN (enter each login)", type="password", autocomplete="off")
    st.divider()
    selected_labels = st.multiselect("Choose indices", [x["label"] for x in INDEXES], default=st.session_state["selected"])
    st.divider()
    st.subheader("Telegram alerts (optional)")
    st.session_state["telegram_token"] = st.text_input("Bot token", type="password", value=st.session_state["telegram_token"])
    st.session_state["telegram_chat"] = st.text_input("Chat ID", value=st.session_state["telegram_chat"])
    a, b = st.columns(2)
    start_clicked = a.button("Start", type="primary", use_container_width=True, disabled=st.session_state["running"])
    stop_clicked = b.button("Stop", use_container_width=True, disabled=not st.session_state["running"])
    if start_clicked:
        if not all([consumer_key, mobile, ucc, totp, mpin]):
            st.error("Enter all five Kotak credentials to connect.")
        elif not selected_labels:
            st.error("Select at least one index.")
        else:
            st.session_state["selected"] = selected_labels
            st.session_state["engines"] = {label: PullbackEngine(label) for label in selected_labels}
            st.session_state["prices"], st.session_state["options"], st.session_state["alerts"], st.session_state["messages"] = {}, {}, [], []
            st.session_state["stop_event"] = threading.Event()
            selected_indexes = [x for x in INDEXES if x["label"] in selected_labels]
            creds = {"consumer_key": consumer_key, "mobile": mobile, "ucc": ucc, "totp": totp, "mpin": mpin}
            thread = threading.Thread(target=kotak_worker, args=(creds, selected_indexes, st.session_state["monitor_queue"], st.session_state["stop_event"]), daemon=True)
            st.session_state["worker"] = thread
            thread.start()
            st.session_state["running"] = True
            st.rerun()
    if stop_clicked:
        st.session_state["stop_event"].set()
        st.session_state["running"] = False

q = st.session_state["monitor_queue"]
while True:
    try:
        item = q.get_nowait()
    except queue.Empty:
        break
    kind = item.get("kind")
    if kind == "history":
        engine = st.session_state["engines"].get(item["symbol"])
        if engine:
            replay_history(engine, item["candles"])
    elif kind == "index_tick":
        symbol = item["symbol"]
        st.session_state["prices"][symbol] = item
        engine = st.session_state["engines"].get(symbol)
        if engine:
            events = engine.update_tick(item["ltp"], datetime.fromisoformat(item["time"]), item.get("day_open"), item.get("prev_close"))
            for event in events:
                st.session_state["alerts"].insert(0, event)
                send_telegram(st.session_state["telegram_token"], st.session_state["telegram_chat"], f'{event["symbol"]} | {event["type"]} | {event["message"]}')
            st.session_state["alerts"] = st.session_state["alerts"][:100]
    elif kind == "option_tick":
        st.session_state["options"][f'{item["index"]} {item["side"]}'] = item
    elif kind in ("status", "warning", "error"):
        st.session_state["messages"].insert(0, item)
        st.session_state["messages"] = st.session_state["messages"][:30]
        if kind == "error":
            st.session_state["running"] = False

if st.session_state["messages"]:
    with st.expander("Connection log", expanded=True):
        for m in st.session_state["messages"][:8]:
            if m["kind"] == "error": st.error(m["message"])
            elif m["kind"] == "warning": st.warning(m["message"])
            else: st.caption(m["message"])

selected = st.session_state["selected"]
cols = st.columns(min(5, max(1, len(selected))))
for i, label in enumerate(selected):
    quote = st.session_state["prices"].get(label)
    with cols[i % len(cols)]:
        st.metric(label, f'{quote["ltp"]:,.2f}' if quote else "Waiting for feed")

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
            if fig:
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("Loading 5-minute candles for ATR(14). Historical backfill is attempted automatically at Start.")
            if snap["zones"]:
                st.dataframe(pd.DataFrame([{"Reference": z["label"], "Direction": z["direction"], "50%": round(z["level_50"], 2), "61.8%": round(z["level_618"], 2), "Zone low": round(z["lower"], 2), "Zone high": round(z["upper"], 2)} for z in snap["zones"]]), use_container_width=True, hide_index=True)
            else:
                st.caption("No confirmed impulse / Fibonacci zones yet.")

with right:
    st.subheader("Signal alerts")
    if st.session_state["alerts"]:
        for event in st.session_state["alerts"][:15]:
            text = f'**{event["symbol"]} — {event["type"]}**\n\n{event["message"]}'
            if event["type"] == "CONFIRMED": st.success(text)
            elif event["type"] == "ZONE": st.warning(text)
            else: st.info(text)
    else:
        st.caption("No signals yet. Entries require a completed 15-minute rejection candle.")

st.divider()
st.subheader("ATM options (nearest expiry)")
if st.session_state["options"]:
    st.dataframe(pd.DataFrame([{"Index / side": k, "Contract": v.get("symbol"), "Strike": v.get("strike"), "Expiry": v.get("expiry"), "Premium": v.get("ltp")} for k, v in st.session_state["options"].items()]), use_container_width=True, hide_index=True)
else:
    st.caption("ATM CE/PE premiums will appear if Kotak returns option-chain contracts for the selected indices.")

with st.expander("Strategy rules and limitations"):
    st.markdown("""
- **5-minute chart:** today's completed 5-minute candles are requested from Kotak at Start, then live ticks continue the chart.
- **ATR:** ATR(14) can become ready immediately when at least 15 completed candles are available.
- **Impulse detection:** ATR(14), 1.5 × ATR reversal threshold, confirmed pivots only.
- **Fibonacci anchors:** previous close, today's open, and confirmed intraday swing base.
- **Entry confirmation:** completed 15-minute rejection candle intersecting the 50–61.8% zone.
- **No order placement:** this app only generates signals and displays ATM option premiums.
- If Kotak does not provide historical candles for a selected index, the connection log will say so and that index falls back to live warm-up.
""")

st.caption("Historical data is used only to warm up the scanner; live signals continue from Kotak's websocket feed.")
