FIBONACCI PULLBACK SIGNAL SCANNER — WINDOWS QUICK START
=======================================================

WHAT THIS DOES
--------------
- Connects to Kotak Neo using the official kotakneoapi Python SDK (v3+).
- Monitors NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY and optionally SENSEX.
- Aggregates live index ticks into 5-minute and 15-minute candles.
- Detects confirmed swing impulses using a 1.5 x ATR(14) reversal filter.
- Calculates separate Fibonacci retracement zones from:
    1. Previous session close -> impulse extreme
    2. Today's open -> impulse extreme
    3. Actual intraday swing base -> impulse extreme
- Alerts when price enters a 50%-61.8% zone.
- Confirms only when a completed 15-minute rejection candle meets the rules.
- Attempts to discover nearest-expiry ATM CE/PE contracts and displays their live premiums.
- Optional Telegram alerts.
- SIGNAL ONLY: there is no order placement code.

INSTALLATION
------------
1. Unzip this folder somewhere easy, e.g. Desktop.
2. Install Python 3.12 (recommended) from https://www.python.org/downloads/
   During installation tick "Add Python to PATH".
3. Open the unzipped folder.
4. Double-click setup.bat and wait for installation to finish.
5. Double-click run.bat.
6. A browser tab opens with the scanner.

KOTAK DETAILS
-------------
In the left sidebar, enter:
- Consumer Key from Kotak Neo Trade API application
- Registered mobile number with +91
- UCC
- Current 6-digit TOTP from your authenticator app
- Trading MPIN

Press Start. TOTP changes every 30 seconds, so enter the current code and press Start promptly.

Do NOT send your Consumer Key, MPIN, TOTP, or other login secrets to anyone, including ChatGPT.
Credentials are passed to the local Python process and are not written to a project config file.

INDEX / OPTION CONTRACT NOTES
-----------------------------
The app subscribes to index feed names documented by Kotak:
- Nifty 50
- Nifty Bank
- Nifty Fin Service
- Nifty Midcap Select
- SENSEX

Some account environments may use different display aliases or may not return all symbols.
If one index does not stream, send the Connection log (with secrets hidden) and we can correct its feed name.

The app queries Kotak's option-chain API once at startup for each selected underlying and
tries to subscribe to the nearest-expiry ATM call and put. Contract availability varies by
exchange, expiry and account permissions. Index signal detection does not depend on option
chain availability.

TELEGRAM (OPTIONAL)
-------------------
1. In Telegram, message @BotFather and create a bot.
2. Copy the bot token into the Telegram Bot token field.
3. Open your new bot and press Start.
4. Get your chat ID (e.g. through a trusted Telegram get-id bot) and enter it.
5. Start the scanner again. Alerts will be sent to that chat.

Do not share the bot token publicly.

HOW TO USE ON YOUR PHONE (SAME WI-FI)
-------------------------------------
The app runs on your computer. For local Wi-Fi access only:
1. Open Command Prompt in this folder.
2. Run:
   .venv\Scripts\python.exe -m streamlit run app.py --server.address 0.0.0.0
3. Find your computer's local IPv4 address using "ipconfig".
4. On your phone connected to the same Wi-Fi, open:
   http://COMPUTER-IP:8501

Do not port-forward this app or expose it to the public internet. The dashboard handles
sensitive brokerage login credentials.

STRATEGY DEFINITIONS (INITIAL TEST VERSION)
-------------------------------------------
- 5-minute ATR period: 14.
- Minimum swing reversal: 1.5 x ATR(14).
- A pivot is confirmed only after a reversal of the threshold; the pivot may therefore
  be confirmed several candles after the actual extreme.
- Bullish impulse: confirmed swing low -> confirmed swing high.
- Bearish impulse: confirmed swing high -> confirmed swing low.
- Bullish retracement zone: high - 50% of impulse range through high - 61.8%.
- Bearish retracement zone: low + 50% through low + 61.8%.
- Three independent anchors: previous close, today's open, and actual intraday pivot base.
- 15-minute bullish rejection: candle touches the zone, closes green, lower wick >= body,
  and close is in the top 40% of its range.
- 15-minute bearish rejection: candle touches the zone, closes red, upper wick >= body,
  and close is in the bottom 40% of its range.

These are explicit first-pass mechanical rules. They must be checked against your intended
chart examples and backtested. The 1.5 ATR filter and rejection wick formula are not proven
optimal and are configurable in signal_engine.py / app.py.

ATR WARM-UP
-----------
This first version constructs candles from live ticks after you start the app. It needs
15 completed 5-minute candles before ATR(14) is available. That means it may take around
75 minutes after startup to start detecting new impulses. Historical candle warm-up/backfill
is the next improvement once the correct index instrument tokens are verified for your account.

TROUBLESHOOTING
---------------
- If pip installation fails: send the full error text.
- If login fails: check consumer key, UCC, mobile, MPIN and TOTP time sync.
- If no index data: check the Connection log; Kotak index aliases may need adjustment.
- If option rows are blank: option chain may not have returned ATM contracts for that underlying.
- If chart says ATR warm-up: leave it running until enough completed 5-minute bars have formed.
- Keep the PC awake and internet connected during market hours.

IMPORTANT RISK NOTE
-------------------
This is a research prototype. It is not evidence of a profitable trading edge, does not
guarantee real-time delivery, and does not execute orders. Use paper trading first.
