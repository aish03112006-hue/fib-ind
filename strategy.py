import numpy as np
import pandas as pd


def enrich(df: pd.DataFrame, fast=9, slow=20, atr_period=14, super_mult=3.0):
    x = df.copy().reset_index(drop=True)
    x['ema_fast'] = x.close.ewm(span=fast, adjust=False).mean()
    x['ema_slow'] = x.close.ewm(span=slow, adjust=False).mean()
    prev = x.close.shift(1)
    tr = pd.concat([(x.high-x.low), (x.high-prev).abs(), (x.low-prev).abs()],axis=1).max(axis=1)
    x['atr'] = tr.ewm(alpha=1/atr_period, adjust=False).mean()
    typical = (x.high+x.low+x.close)/3
    day = x.timestamp.dt.date
    vol = x.volume.clip(lower=0)
    cumvol = vol.groupby(day).cumsum()
    x['vwap'] = (typical*vol).groupby(day).cumsum().div(cumvol.replace(0,np.nan))
    mid = (x.high+x.low)/2
    upper = (mid+super_mult*x.atr).to_numpy(dtype=float)
    lower = (mid-super_mult*x.atr).to_numpy(dtype=float)
    close = x.close.to_numpy(dtype=float)
    final_upper = upper.copy(); final_lower = lower.copy()
    direction = np.ones(len(x), dtype=int)
    for i in range(1,len(x)):
        final_upper[i] = upper[i] if upper[i] < final_upper[i-1] or close[i-1] > final_upper[i-1] else final_upper[i-1]
        final_lower[i] = lower[i] if lower[i] > final_lower[i-1] or close[i-1] < final_lower[i-1] else final_lower[i-1]
        if close[i] > final_upper[i-1]: direction[i] = 1
        elif close[i] < final_lower[i-1]: direction[i] = -1
        else: direction[i] = direction[i-1]
    x['supertrend_dir'] = direction
    x['supertrend'] = np.where(direction==1, final_lower, final_upper)
    # A signal requires a fresh crossing on a fully completed candle, not merely an EMA alignment.
    above = (x.close > x.ema_fast) & (x.ema_fast > x.ema_slow)
    below = (x.close < x.ema_fast) & (x.ema_fast < x.ema_slow)
    x['long_setup'] = above & ~above.shift(1, fill_value=False) & (x.supertrend_dir==1) & (x.close > x.vwap)
    x['short_setup'] = below & ~below.shift(1, fill_value=False) & (x.supertrend_dir==-1) & (x.close < x.vwap)
    x['signal'] = np.select([x.long_setup, x.short_setup], ['BUY','SELL'], default='WAIT')
    return x


def backtest(x, rr=2.0, atr_mult=1.5, risk_rupees=1000.0):
    # Index-point proxy only: does not simulate options, Greeks, brokerage, or margin.
    trades=[]; i=1
    while i < len(x)-1:
        row=x.iloc[i]
        if row.signal not in ('BUY','SELL') or not np.isfinite(row.atr) or row.atr<=0:
            i+=1; continue
        if x.iloc[i+1].timestamp.date() != row.timestamp.date():
            i += 1
            continue
        # Enter at next bar open, never on the signal bar close.
        entry=float(x.iloc[i+1].open); dist=float(row.atr)*atr_mult
        side=1 if row.signal=='BUY' else -1
        stop=entry-side*dist; target=entry+side*rr*dist
        j=i+1; outcome=None; exit_price=None
        while j < len(x):
            candle=x.iloc[j]
            if j > i+1 and (candle.open <= stop if side==1 else candle.open >= stop):
                exit_price=float(candle.open); outcome='GAP STOP'; break
            stop_hit=(candle.low<=stop if side==1 else candle.high>=stop)
            target_hit=(candle.high>=target if side==1 else candle.low<=target)
            # Conservative tie handling: assume stop first.
            if stop_hit or target_hit:
                exit_price=stop if stop_hit else target
                outcome='STOP' if stop_hit else 'TARGET'
                break
            # Force exit at last candle of same IST session.
            if j==len(x)-1 or x.iloc[j+1].timestamp.date()!=candle.timestamp.date():
                exit_price=float(candle.close); outcome='SESSION CLOSE'; break
            j+=1
        pnl_r=side*(exit_price-entry)/dist
        trades.append({'time':str(x.iloc[i+1].timestamp),'side':row.signal,'entry':round(entry,2),
                       'stop':round(stop,2),'target':round(target,2),'exit':round(exit_price,2),
                       'outcome':outcome,'R':round(pnl_r,3),'proxy_pnl_inr':round(pnl_r*risk_rupees,2)})
        i=j+1
    return pd.DataFrame(trades)
