from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import pandas as pd


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
            raise RuntimeError('Kotak rejected the market-data request.')
        response = result.json()
    except Exception:
        raise RuntimeError('Kotak market data is unavailable; verify private configuration.') from None
    if not isinstance(response, dict):
        raise RuntimeError('Kotak returned an unexpected response.')
    data = response.get('data')
    candles = data.get('candles', []) if isinstance(data, dict) else []
    if not candles:
        raise RuntimeError('Kotak returned no candles. Check instrument, key and entitlement.')
    df = pd.DataFrame(candles)
    if df.shape[1] < 6:
        raise RuntimeError('Unexpected candle columns from Kotak.')
    df = df.iloc[:, :6].copy()
    df.columns = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
    df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True, errors='coerce').dt.tz_convert('Asia/Kolkata')
    for c in ['open','high','low','close','volume']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df = df.replace([float('inf'), -float('inf')], float('nan'))
    df = df.dropna(subset=['timestamp','open','high','low','close']).drop_duplicates('timestamp').sort_values('timestamp')
    if df.empty:
        raise RuntimeError('No valid OHLC candles after parsing.')
    if ((df.high < df[['open','close','low']].max(axis=1)) | (df.low > df[['open','close','high']].min(axis=1)) | (df[['open','high','low','close']] <= 0).any(axis=1)).any():
        raise RuntimeError('Invalid OHLC data')
    return df.reset_index(drop=True)
