from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from collections import deque
from typing import Optional

IST = ZoneInfo("Asia/Kolkata")


@dataclass
class Candle:
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass
class Pivot:
    kind: str  # "H" or "L"
    price: float
    time: datetime


@dataclass
class FibZone:
    label: str
    direction: str  # BULLISH / BEARISH
    anchor: float
    endpoint: float
    level_50: float
    level_618: float
    lower: float
    upper: float
    impulse_id: str


class CandleBuilder:
    def __init__(self, minutes: int):
        self.minutes = minutes
        self.current: Optional[Candle] = None

    def update(self, price: float, timestamp: datetime) -> Optional[Candle]:
        timestamp = timestamp.astimezone(IST)
        minute_bucket = (timestamp.minute // self.minutes) * self.minutes
        bucket = timestamp.replace(minute=minute_bucket, second=0, microsecond=0)

        if self.current is None:
            self.current = Candle(bucket, price, price, price, price)
            return None

        if bucket == self.current.time:
            self.current.high = max(self.current.high, price)
            self.current.low = min(self.current.low, price)
            self.current.close = price
            return None

        closed = self.current
        self.current = Candle(bucket, price, price, price, price)
        return closed


def calculate_atr(candles: list[Candle], period: int = 14) -> Optional[float]:
    if len(candles) < period + 1:
        return None
    trs = []
    recent = candles[-(period + 1):]
    for i in range(1, len(recent)):
        bar = recent[i]
        prev_close = recent[i - 1].close
        tr = max(
            bar.high - bar.low,
            abs(bar.high - prev_close),
            abs(bar.low - prev_close),
        )
        trs.append(tr)
    return sum(trs[-period:]) / period if trs else None


class ZigZagDetector:
    """ATR-filtered, close-confirmed swing detector. Pivots are confirmed only
    after price reverses by the configured ATR multiple, so signals are delayed
    by design rather than repainting an unconfirmed swing.
    """
    def __init__(self, atr_multiple: float = 1.5):
        self.atr_multiple = atr_multiple
        self.trend = 0
        self.candidate_high: Optional[Pivot] = None
        self.candidate_low: Optional[Pivot] = None
        self.last_pivot: Optional[Pivot] = None

    def update(self, bar: Candle, atr: float) -> Optional[tuple[Pivot, Pivot]]:
        threshold = atr * self.atr_multiple

        if self.candidate_high is None or bar.high > self.candidate_high.price:
            self.candidate_high = Pivot("H", bar.high, bar.time)
        if self.candidate_low is None or bar.low < self.candidate_low.price:
            self.candidate_low = Pivot("L", bar.low, bar.time)

        if self.trend == 0:
            # Establish the first confirmed pivot after a threshold reversal.
            if self.candidate_low and bar.close - self.candidate_low.price >= threshold:
                self.last_pivot = self.candidate_low
                self.trend = 1
                self.candidate_high = Pivot("H", bar.high, bar.time)
                return None
            if self.candidate_high and self.candidate_high.price - bar.close >= threshold:
                self.last_pivot = self.candidate_high
                self.trend = -1
                self.candidate_low = Pivot("L", bar.low, bar.time)
                return None
            return None

        if self.trend == 1:
            if self.candidate_high and self.candidate_high.price - bar.close >= threshold:
                confirmed = self.candidate_high
                previous = self.last_pivot
                self.last_pivot = confirmed
                self.trend = -1
                self.candidate_low = Pivot("L", bar.low, bar.time)
                if previous and previous.kind == "L":
                    return previous, confirmed
            return None

        if self.trend == -1:
            if self.candidate_low and bar.close - self.candidate_low.price >= threshold:
                confirmed = self.candidate_low
                previous = self.last_pivot
                self.last_pivot = confirmed
                self.trend = 1
                self.candidate_high = Pivot("H", bar.high, bar.time)
                if previous and previous.kind == "H":
                    return previous, confirmed
            return None

        return None


class PullbackEngine:
    def __init__(self, symbol: str, atr_period: int = 14, atr_multiple: float = 1.5):
        self.symbol = symbol
        self.atr_period = atr_period
        self.atr_multiple = atr_multiple
        self.b5 = CandleBuilder(5)
        self.b15 = CandleBuilder(15)
        self.candles5: deque[Candle] = deque(maxlen=500)
        self.candles15: deque[Candle] = deque(maxlen=300)
        self.zigzag = ZigZagDetector(atr_multiple)
        self.prev_close: Optional[float] = None
        self.day_open: Optional[float] = None
        self.last_price: Optional[float] = None
        self.active_zones: list[FibZone] = []
        self.impulse_id = 0
        self.alerted_zone_keys: set[str] = set()
        self.confirmed_key: Optional[str] = None
        self.last_update: Optional[datetime] = None
        self.status = "Waiting for live candles / ATR warm-up"

    def _new_impulse(self, start: Pivot, end: Pivot, atr: float) -> list[dict]:
        direction = "BULLISH" if start.kind == "L" and end.kind == "H" else "BEARISH"
        if direction == "BULLISH":
            low, high = start.price, end.price
            if high <= low:
                return []
        else:
            high, low = start.price, end.price
            if high <= low:
                return []

        move = high - low
        if move < self.atr_multiple * atr:
            return []

        self.impulse_id += 1
        iid = f"{self.symbol}-{self.impulse_id}"
        anchors: list[tuple[str, float]] = []

        if direction == "BULLISH":
            if self.prev_close is not None and self.prev_close < high:
                anchors.append(("Previous close", self.prev_close))
            if self.day_open is not None and self.day_open < high:
                anchors.append(("Today's open", self.day_open))
            anchors.append(("Intraday swing base", low))
        else:
            if self.prev_close is not None and self.prev_close > low:
                anchors.append(("Previous close", self.prev_close))
            if self.day_open is not None and self.day_open > low:
                anchors.append(("Today's open", self.day_open))
            anchors.append(("Intraday swing base", high))

        zones = []
        seen = set()
        for label, anchor in anchors:
            # The ATR filter validates the actual swing base -> endpoint move.
            # Previous-close and today's-open references remain independent even
            # when their individual measured range is shorter than that swing.
            key = (label, round(anchor, 4))
            if key in seen:
                continue
            seen.add(key)

            if direction == "BULLISH":
                level50 = high - 0.50 * (high - anchor)
                level618 = high - 0.618 * (high - anchor)
            else:
                level50 = low + 0.50 * (anchor - low)
                level618 = low + 0.618 * (anchor - low)

            zone = FibZone(
                label=label,
                direction=direction,
                anchor=anchor,
                endpoint=end.price,
                level_50=level50,
                level_618=level618,
                lower=min(level50, level618),
                upper=max(level50, level618),
                impulse_id=iid,
            )
            zones.append(zone)

        if zones:
            self.active_zones = zones
            self.alerted_zone_keys.clear()
            self.confirmed_key = None
            self.status = f"{direction.title()} impulse confirmed"
            return [{
                "type": "IMPULSE",
                "symbol": self.symbol,
                "direction": direction,
                "message": f"{direction.title()} impulse detected; {len(zones)} Fibonacci reference(s) created.",
                "impulse_id": iid,
                "zones": [asdict(z) for z in zones],
                "time": end.time.isoformat(),
            }]
        return []

    def _zone_alerts(self, price: float, timestamp: datetime) -> list[dict]:
        events = []
        touched = []
        for z in self.active_zones:
            if z.lower <= price <= z.upper:
                touched.append(z)
                key = f"{z.impulse_id}|{z.label}"
                if key not in self.alerted_zone_keys:
                    self.alerted_zone_keys.add(key)
                    events.append({
                        "type": "ZONE",
                        "symbol": self.symbol,
                        "direction": z.direction,
                        "zone": z.label,
                        "lower": z.lower,
                        "upper": z.upper,
                        "message": f"{z.direction.title()} pullback entered {z.label} 50–61.8% zone.",
                        "time": timestamp.isoformat(),
                        "impulse_id": z.impulse_id,
                    })
        if touched:
            self.status = f"Inside {len(touched)} Fibonacci zone(s)"
        return events

    def _is_rejection(self, bar: Candle, direction: str) -> bool:
        candle_range = max(bar.high - bar.low, 1e-9)
        body = abs(bar.close - bar.open)
        if direction == "BULLISH":
            lower_wick = min(bar.open, bar.close) - bar.low
            close_position = (bar.close - bar.low) / candle_range
            return bar.close > bar.open and lower_wick >= body and close_position >= 0.60
        upper_wick = bar.high - max(bar.open, bar.close)
        close_position = (bar.close - bar.low) / candle_range
        return bar.close < bar.open and upper_wick >= body and close_position <= 0.40

    def _check_15m_rejection(self, bar: Candle) -> list[dict]:
        if not self.active_zones:
            return []
        directions = {z.direction for z in self.active_zones}
        for direction in directions:
            eligible = [
                z for z in self.active_zones
                if z.direction == direction
                and bar.low <= z.upper
                and bar.high >= z.lower
            ]
            if eligible and self._is_rejection(bar, direction):
                key = f"{eligible[0].impulse_id}|{direction}"
                # One confirmed entry alert per impulse/direction; avoid repeating
                # the same setup on every subsequent 15-minute candle.
                if key == self.confirmed_key:
                    return []
                self.confirmed_key = key
                names = ", ".join(z.label for z in eligible)
                self.status = f"{direction.title()} 15-minute rejection confirmed"
                return [{
                    "type": "CONFIRMED",
                    "symbol": self.symbol,
                    "direction": direction,
                    "zones": names,
                    "entry_reference": bar.close,
                    "candle_time": bar.time.isoformat(),
                    "message": f"{direction.title()} 15-minute rejection confirmed at {bar.close:.2f}; touched: {names}.",
                    "time": bar.time.isoformat(),
                    "impulse_id": eligible[0].impulse_id,
                }]
        return []

    def update_tick(
        self,
        price: float,
        timestamp: Optional[datetime] = None,
        day_open: Optional[float] = None,
        prev_close: Optional[float] = None,
    ) -> list[dict]:
        timestamp = timestamp or datetime.now(IST)
        timestamp = timestamp.astimezone(IST)
        events: list[dict] = []

        # Reset the intraday engine at a new IST trading date.
        if self.last_update and timestamp.date() != self.last_update.date():
            self.__init__(self.symbol, self.atr_period, self.atr_multiple)

        self.last_update = timestamp
        self.last_price = float(price)
        if day_open and day_open > 0:
            self.day_open = float(day_open)
        if prev_close and prev_close > 0:
            self.prev_close = float(prev_close)

        closed5 = self.b5.update(float(price), timestamp)
        closed15 = self.b15.update(float(price), timestamp)

        if closed5:
            self.candles5.append(closed5)
            atr = calculate_atr(list(self.candles5), self.atr_period)
            if atr is None:
                self.status = f"ATR warm-up: {len(self.candles5)}/{self.atr_period + 1} completed 5m candles"
            else:
                pivots = self.zigzag.update(closed5, atr)
                if pivots:
                    events.extend(self._new_impulse(pivots[0], pivots[1], atr))

        if closed15:
            self.candles15.append(closed15)
            events.extend(self._check_15m_rejection(closed15))

        events.extend(self._zone_alerts(float(price), timestamp))
        return events

    def snapshot(self) -> dict:
        return {
            "symbol": self.symbol,
            "last_price": self.last_price,
            "day_open": self.day_open,
            "prev_close": self.prev_close,
            "status": self.status,
            "zones": [asdict(z) for z in self.active_zones],
            "candles5": [asdict(c) for c in list(self.candles5)[-120:]],
            "candles15": [asdict(c) for c in list(self.candles15)[-60:]],
            "last_update": self.last_update.isoformat() if self.last_update else None,
        }
