# NIFTY • Kotak Neo Research Terminal

**Paper trading and research only. No broker order endpoints are called.**

This is a safety-focused Streamlit adaptation of the architecture described in `chandrunit/nifty-options-backtester`, with a new Kotak Neo historical candle adapter and independently implemented EMA/VWAP/Supertrend research engine. It is not a verbatim copy of that repository.

## Requirements

- Python 3.12 and a Kotak Neo API consumer key with historical market-data access
- Valid NIFTY index Neo symbol for your account; the default `nse_cm|Nifty 50` is illustrative and may need replacing with the numeric instrument token returned by Kotak
- Read-only HTTPS adapter matching the official Kotak historical API; no trading SDK installed

## Run on Windows

```powershell
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

Open the local URL printed by Streamlit (usually `http://localhost:8501`). Configure `.streamlit/secrets.toml` privately, enter the valid NIFTY Neo symbol, and click **Fetch / refresh candles**.

## Deploy with Streamlit Community Cloud

1. Create a **private** GitHub repository and upload `app.py`, `market.py`, `strategy.py`, `requirements.txt`, `.gitignore`, and `README.md` at the repository root.
2. Open https://share.streamlit.io/ and sign in with GitHub. Select **Create app**, your repository, branch `main`, and entry point `app.py`.
3. Add the following to Streamlit app settings → **Secrets** (not GitHub):

```toml
KOTAK_CONSUMER_KEY = "YOUR_CONSUMER_KEY"
NIFTY_NEOSYMBOL = "YOUR_VALID_NEO_SYMBOL"
```

4. Deploy, then use the actual `*.streamlit.app` URL issued by Streamlit. The app is not automatically authenticated for website visitors; **keep access private and do not share a URL exposing your consumer key**. Check whether your Streamlit plan supports the privacy you need.
5. If the historical endpoint returns no data, verify the index symbol, date range, candle interval and market-data entitlement with Kotak.

## Limitations and checks

- This app fetches historical candles **only when you press Refresh**; it does not run a continuously connected WebSocket or automatic 5-minute scheduler.
- It does not trade options, fetch an option chain, calculate option Greeks, or simulate option premium P&L. All stop/target numbers are **NIFTY index points**.
- The backtest enters at the next candle open, treats stop/target collisions conservatively as stop-first, and exits at session end; it remains a simplified index proxy with no fees or slippage.
- The manual paper journal exists only in Streamlit session state. Download it before restarting.
- The current code has not been tested against **your live Kotak account**. SDK/API changes, invalid tokens, and market-data permissions can cause errors.
- Kotak API usage, cloud outbound IP restrictions, and account entitlements are subject to Kotak's current rules.
- Never use any source code that includes someone else's leaked broker tokens.

## Sources

- Kotak Neo official SDK: https://github.com/Kotak-Neo/kotak-neo-python
- Historical data interface: https://github.com/Kotak-Neo/kotak-neo-python/blob/main/docs/functions/market_data/historical_data.md
- Architectural reference: https://github.com/chandrunit/nifty-options-backtester

## Deployment validation

Run `python -m unittest discover -s tests -v`. Tests use clearly synthetic candles and mocked HTTP only; live account validation is still required. Set Python 3.12 in Streamlit deployment settings. Keep website access private because the API token is used server-side. Index feeds without volume suppress VWAP-confirmed signals; no substitute volume is invented.
