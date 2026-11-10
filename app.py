import os
from datetime import datetime, time as dtime
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from market import get_candles, MarketDataError
from strategy import enrich, backtest

st.set_page_config(page_title='NIFTY • Kotak Neo Research', page_icon='📈', layout='wide')
st.title('NIFTY • Kotak Neo Research Terminal')
st.caption('Paper trading and research only • No order placement code • Historical candle refresh, not tick streaming')

def secret(name, default=''):
    try:
        value = st.secrets.get(name, default)
        if name == 'KOTAK_CONSUMER_KEY' and not value:
            value = st.secrets.get('kotak', {}).get('consumer_key', default)
        return str(value)
    except Exception: return os.getenv(name, default)

with st.sidebar:
    st.header('Kotak Neo data')
    key=secret('KOTAK_CONSUMER_KEY')
    st.caption('API key configured privately' if key else 'Owner must configure KOTAK_CONSUMER_KEY in Streamlit Secrets.')
    symbol=st.text_input('NIFTY Neo symbol', value=secret('NIFTY_NEOSYMBOL','nse_cm|Nifty 50'), help='Verify the actual index token/neosymbol in Kotak; some historical endpoints may require a numeric token.')
    interval=st.selectbox('Candle interval', ['5min','15min','10min','30min','60min'], index=1)
    days=st.slider('Calendar days of history',3,30 if interval=='5min' else 60,14)
    fast=st.number_input('Fast EMA',2,100,9)
    slow=st.number_input('Slow EMA',3,200,20)
    risk=st.number_input('Illustrative risk per trade (₹)',100,10000,1000,100)
    rr=st.number_input('Reward/risk target',1.0,5.0,2.0,0.25)
    refresh=st.button('Fetch / refresh candles',type='primary',width='stretch')
    st.caption('Data comes from Kotak on each refresh. Do not share your key or publish secrets.')

if refresh:
    try:
        with st.spinner('Fetching from Kotak Neo…'):
            st.session_state['candles']=get_candles(key,symbol,interval,days)
            st.session_state['request']=(symbol,interval,days)
            st.session_state['fetched_at']=datetime.now().astimezone().isoformat(timespec='seconds')
    except MarketDataError as exc:
        st.error(str(exc))
        st.stop()
    except Exception:
        st.error('Kotak data request failed. Check the private API key, instrument token, data entitlement and service availability.')
        st.stop()

if 'candles' not in st.session_state:
    st.info('Configure the private Kotak key in Streamlit Secrets and verify the NIFTY instrument, then click **Fetch / refresh candles**. No simulated or fabricated market data is shown.')
    st.stop()

if st.session_state.get('request') != (symbol,interval,days):
    st.warning('The symbol, interval or date range has changed. Refresh to use the new selection.')
    st.stop()

raw=st.session_state['candles']
if raw.attrs.get('rejected_candles', 0):
    st.warning(f"Excluded {raw.attrs['rejected_candles']} invalid Kotak candles. The chart and backtest use valid rows only; missing intervals can affect results.")
# The final candle may be in progress. Remove it unless its scheduled interval has elapsed.
minutes={'5min':5,'10min':10,'15min':15,'30min':30,'60min':60}[interval]
now=pd.Timestamp.now(tz='Asia/Kolkata')
closed=raw[raw.timestamp + pd.Timedelta(minutes=minutes) <= now].copy()
if len(closed)<35:
    st.warning('Not enough completed candles for meaningful signals. Select more history or check data access.')
    st.stop()
if fast >= slow:
    st.warning('Fast EMA must be shorter than slow EMA.')
    st.stop()
x=enrich(closed,fast,slow)
if x.vwap.isna().any():
    st.warning('Index volume is unavailable for some candles. VWAP-confirmed signals are suppressed on those candles.')
last=x.iloc[-1]
a,b,c,d=st.columns(4)
a.metric('Last completed close',f'{last.close:,.2f}')
b.metric('Latest setup',last.signal)
c.metric('Supertrend','Bullish' if last.supertrend_dir==1 else 'Bearish')
d.metric('ATR (index points)',f'{last.atr:,.2f}')
st.caption(f'Last completed candle: {last.timestamp} • Last data fetch: {st.session_state["fetched_at"]} • Source: Kotak Neo historical candles')
if last.signal in ('BUY','SELL'):
    side=1 if last.signal=='BUY' else -1
    stop=last.close-side*1.5*last.atr
    target=last.close+side*1.5*last.atr*rr
    st.info(f'Index reference only: {last.signal} near {last.close:.2f}, SL {stop:.2f}, target {target:.2f}. Not option premium levels or executable orders.')
else:
    st.info('No fresh signal on the latest completed candle. WAIT is not a prediction.')

fig=go.Figure()
fig.add_trace(go.Candlestick(x=x.timestamp,open=x.open,high=x.high,low=x.low,close=x.close,name='NIFTY'))
for col,name,color in [('ema_fast',f'EMA {fast}','orange'),('ema_slow',f'EMA {slow}','deepskyblue'),('vwap','VWAP','violet'),('supertrend','Supertrend','limegreen')]:
    fig.add_trace(go.Scatter(x=x.timestamp,y=x[col],mode='lines',name=name,line={'width':1.5,'color':color}))
fig.update_layout(height=550,xaxis_rangeslider_visible=False,template='plotly_dark',legend={'orientation':'h'})
st.plotly_chart(fig,width='stretch')

tab1,tab2,tab3=st.tabs(['Backtest (index proxy)','Paper journal','Signals'])
with tab1:
    st.warning('This is an **index-point proxy backtest**, NOT an options backtest. It excludes option premium changes, Greeks, spread, slippage, brokerage and taxes. Illustrative ₹ P&L scales R by your selected risk amount.')
    trades=backtest(x,rr=rr,risk_rupees=risk)
    if trades.empty: st.info('No completed trades in the loaded candles.')
    else:
        m1,m2,m3=st.columns(3)
        m1.metric('Completed trades',len(trades))
        m2.metric('Positive R trades',f'{(trades.R>0).mean():.1%}')
        m3.metric('Total R',f'{trades.R.sum():.2f}')
        st.dataframe(trades,width='stretch',hide_index=True)
        st.download_button('Download backtest CSV',trades.to_csv(index=False),'nifty_proxy_backtest.csv','text/csv')
with tab2:
    st.caption('Manual paper trades are stored in this browser session only. Download the CSV to keep them; a shared cloud database is not configured.')
    if 'journal' not in st.session_state: st.session_state.journal=[]
    with st.form('paper'):
        col1,col2,col3=st.columns(3)
        side=col1.selectbox('Paper direction',['BUY','SELL'])
        entry=col2.number_input('Paper entry (index points)',min_value=0.01,value=float(last.close),step=0.05)
        exit_=col3.number_input('Paper exit (index points)',min_value=0.01,value=float(last.close),step=0.05)
        note=st.text_input('Trade notes')
        if st.form_submit_button('Record paper trade'):
            pts=(exit_-entry)*(1 if side=='BUY' else -1)
            st.session_state.journal.append({'recorded_at':datetime.now().astimezone().isoformat(timespec='seconds'),'side':side,'entry':entry,'exit':exit_,'points':round(pts,2),'note':note})
            st.success('Paper trade recorded in this session.')
    if st.session_state.journal:
        journal=pd.DataFrame(st.session_state.journal)
        st.dataframe(journal,width='stretch',hide_index=True)
        st.download_button('Download journal CSV',journal.to_csv(index=False),'nifty_paper_journal.csv','text/csv')
with tab3:
    st.dataframe(x.loc[x.signal!='WAIT',['timestamp','close','signal','atr','ema_fast','ema_slow','vwap']].tail(100),width='stretch',hide_index=True)
