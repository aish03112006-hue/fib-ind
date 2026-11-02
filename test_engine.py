from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from signal_engine import Candle, calculate_atr, PullbackEngine

IST = ZoneInfo("Asia/Kolkata")


def test_atr_returns_value_after_period_plus_one_bars():
    start = datetime(2026, 1, 1, 9, 15, tzinfo=IST)
    bars = []
    for i in range(15):
        p = 100 + i
        bars.append(Candle(start + timedelta(minutes=5*i), p, p+2, p-1, p+1))
    assert calculate_atr(bars, 14) is not None


def test_fibonacci_levels_bullish():
    engine = PullbackEngine("TEST")
    engine.prev_close = 100
    engine.day_open = 102
    start = type("P", (), {"kind": "L", "price": 100, "time": datetime.now(IST)})()
    end = type("P", (), {"kind": "H", "price": 120, "time": datetime.now(IST)})()
    events = engine._new_impulse(start, end, atr=5)
    assert events
    zones = engine.active_zones
    base_zone = next(z for z in zones if z.label == "Intraday swing base")
    assert round(base_zone.level_50, 2) == 110.00
    assert round(base_zone.level_618, 2) == 107.64


if __name__ == "__main__":
    test_atr_returns_value_after_period_plus_one_bars()
    test_fibonacci_levels_bullish()
    print("Engine smoke tests passed.")
