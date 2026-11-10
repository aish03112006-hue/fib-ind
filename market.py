from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import pandas as pd


class MarketDataError(RuntimeError):
    """Only fixed messages and numeric HTTP/API codes may reach the UI."""


def get_candles(consumer_key: str, neosymbol: str, interval: str, days: int) -> pd.DataFrame:
    if not consumer_key.strip():
        raise ValueError('Enter your Kotak Neo consumer key in the sidebar or Streamlit secrets.')
    if '|' not in neosymbol:
        raise ValueError('Enter a valid Neo symbol, such as nse_cm|<instrument-token>.')
    if interval not in ('5min','10min','15min','30min','60min'):
        raise ValueError('Unsupported interval')
    if not 1 <= days <= (30 if interval=='5min' else 60):
        raise ValueError('Date range exceeds supported limit')
    import requests
    end = datetime.now(ZoneInfo('Asia/Kolkata')).date()
    start = end - timedelta(days=days)
    # Fixed market-data GET only. No trading SDK, login session or order endpoint.
    try:
        result = requests.get(
            'https://mis.kotaksecurities.com/market-data/1.0/historical/details',
            headers={'Authorization': consumer_key},
            params={'neosymbol': neosymbol, 'interval': interval,
                    'fromdate': start.isoformat(), 'todate': end.isoformat()},
            timeout=25, allow_redirects=False)
        if result.status_code != 200:
            raise MarketDataError(f'Kotak market-data request returned HTTP {int(result.status_code)}. Verify the consumer token and market-data access.')
        response = result.json()
    except MarketDataError:
        raise
    except Exception:
        raise MarketDataError('Kotak market data could not be reached or returned invalid JSON.') from None
    if not isinstance(response, dict):
        raise MarketDataError('Kotak returned an unexpected response shape.')
    data = response.get('data')
    candles = data.get('candles', []) if isinstance(data, dict) else []
    if not candles:
        fault = response.get('fault')
        code = fault.get('code') if isinstance(fault, dict) else None
        suffix = f' API code {code}.' if type(code) is int else ''
        raise MarketDataError('Kotak returned HTTP 200 without candles. Verify the instrument token and historical-data entitlement.' + suffix)
    df = pd.DataFrame(candles)
    if df.shape[1] == 5:
        df[5] = float('nan')
    if df.shape[1] < 6:
        raise MarketDataError('Kotak returned an unsupported candle layout.')
    df = df.iloc[:, :6].copy()
    df.columns = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
    df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True, errors='coerce').dt.tz_convert('Asia/Kolkata')
    for c in ['open','high','low','close','volume']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df = df.replace([float('inf'), -float('inf')], float('nan'))
    df = df.dropna(subset=['timestamp','open','high','low','close']).drop_duplicates('timestamp').sort_values('timestamp')
    if df.empty:
        raise MarketDataError('Kotak returned no valid dated OHLC candles.')
    invalid = ((df.high < df[['open','close','low']].max(axis=1)) |
               (df.low > df[['open','close','high']].min(axis=1)) |
               (df[['open','high','low','close']] <= 0).any(axis=1))
    rejected = int(invalid.sum())
    df = df.loc[~invalid].reset_index(drop=True)
    if df.empty:
        raise MarketDataError(f'Kotak returned {rejected} candles, all with invalid OHLC prices. Verify the instrument token.')
    df.attrs['rejected_candles'] = rejected
    return df
