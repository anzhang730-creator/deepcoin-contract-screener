"""Python port of tradingview_key_levels_v2.pine."""
from __future__ import annotations

from dataclasses import dataclass
from bisect import bisect_right
from math import isclose, isfinite
from typing import Any, Iterable


DEFAULT_SETTINGS = {
    "pivotLeft": 7,
    "pivotRight": 7,
    "atrLength": 14,
    "minSwingAtr": 1.25,
    "zoneAtrMult": 0.28,
    "maxZonePct": 0.60,
    "mergeAtrMult": 0.40,
    "minCreateScore": 62.0,
    "maxZonesPerSide": 2,
    "maxZoneAge": 1200,
    "touchCooldown": 8,
    "reactionAtr": 0.70,
    "reactionBars": 10,
    "maxUsefulTouches": 3,
    "breakBufferAtr": 0.15,
    "breakClosesNeed": 2,
    "hammerMinRangeAtr": 0.30,
    "hammerMinBodyRange": 0.02,
    "hammerMaxBodyRange": 0.50,
    "hammerMinLowerWickBody": 1.25,
    "hammerMaxUpperWickRange": 0.28,
    "hammerMinCloseLocation": 0.55,
    "hammerMaxSupportPenetrationAtr": 0.60,
    "invertedMinRangeAtr": 0.30,
    "invertedMinBodyRange": 0.02,
    "invertedMaxBodyRange": 0.50,
    "invertedMinUpperWickBody": 1.25,
    "invertedMaxLowerWickRange": 0.28,
    "invertedMaxCloseLocation": 0.48,
    "invertedMaxResistancePenetrationAtr": 0.60,
    "enableRoleFlip": False,
    "useHtf": True,
    "htf": "4h",
    "htfPivotLeft": 5,
    "htfPivotRight": 5,
}

REFERENCE_FEATURES = {
    "kl_previous_day_high", "kl_previous_day_low",
    "kl_previous_week_high", "kl_previous_week_low",
    "kl_day_open", "kl_session_vwap",
}
DAY_MS = 86_400_000


@dataclass
class Zone:
    top: float
    bottom: float
    kind: int
    score: float
    touches: int
    born_bar: int
    origin_bar: int
    origin_price: float | None
    last_touch_bar: int
    pending_touch_bar: int
    outside_closes: int
    confluence: int
    pending_reaction: bool
    awaiting_retest: bool
    contains_htf: bool

    @property
    def center(self) -> float:
        return (self.top + self.bottom) * 0.5


class KeyLevelError(ValueError):
    pass


class KeyLevelCalculator:
    """Replay closed candles in Pine's per-bar mutation order."""

    def __init__(self, settings: dict[str, Any] | None = None, tick_size: float = 0.0):
        self.settings = dict(DEFAULT_SETTINGS)
        if settings:
            self.settings.update(settings)
        self.tick_size = max(0.0, float(tick_size or 0.0))
        self.zones: list[Zone] = []
        self.events: dict[str, bool] = {}
        self._bars: list[list[float]] = []
        self._atrs: list[float | None] = []
        self._atr_seed: list[float] = []
        self._atr_value: float | None = None
        self._support_hammer_history: list[dict[str, Any]] = []
        self._resistance_inverted_hammer_history: list[dict[str, Any]] = []

    @staticmethod
    def _clamp(value: float, low: float, high: float) -> float:
        return max(low, min(high, value))

    def _atr(self, bar: list[float], previous_close: float | None) -> float | None:
        high, low = bar[2], bar[3]
        tr = high - low if previous_close is None else max(
            high - low, abs(high - previous_close), abs(low - previous_close)
        )
        length = int(self.settings["atrLength"])
        if self._atr_value is None:
            self._atr_seed.append(tr)
            if len(self._atr_seed) == length:
                self._atr_value = sum(self._atr_seed) / length
        else:
            self._atr_value = (self._atr_value * (length - 1) + tr) / length
        return self._atr_value

    def _distance(self, zone: Zone, price: float) -> float:
        return price - zone.top if price > zone.top else zone.bottom - price if price < zone.bottom else 0.0

    def _rank(self, zone: Zone, price: float, atr: float | None, bar_index: int) -> float:
        distance_atr = self._distance(zone, price) / atr if atr and atr > 0 else 0.0
        return zone.score - min(25.0, distance_atr * 2.0) - min(12.0, (bar_index - zone.born_bar) / 200.0)

    def _count_kind(self, kind: int) -> int:
        return sum(zone.kind == kind for zone in self.zones)

    def _prune_kind(self, kind: int, price: float, atr: float | None, bar_index: int) -> None:
        overflow = max(0, self._count_kind(kind) - int(self.settings["maxZonesPerSide"]))
        for _ in range(overflow):
            candidates = [(self._rank(zone, price, atr, bar_index), index)
                          for index, zone in enumerate(self.zones) if zone.kind == kind]
            if candidates:
                _, weakest = min(candidates)
                self.zones.pop(weakest)

    def _add_candidate(self, center: float | None, kind: int, source_atr: float | None,
                       swing_strength: float | None, from_htf: bool, source_bar: int,
                       current_price: float, current_atr: float | None,
                       current_bar_index: int, origin_bar: int | None = None) -> None:
        if center is None or source_atr is None or source_atr <= 0 or swing_strength is None:
            return
        if swing_strength < float(self.settings["minSwingAtr"]):
            return
        origin_bar = source_bar if origin_bar is None else int(origin_bar)
        width_floor = self.tick_size * 5.0
        width_cap = max(width_floor, abs(center) * float(self.settings["maxZonePct"]) * 0.01)
        width = max(width_floor, min(source_atr * float(self.settings["zoneAtrMult"]), width_cap))
        candidate_top = center + width * (0.65 if kind == 1 else 0.35)
        candidate_bottom = center - width * (0.35 if kind == 1 else 0.65)
        strength_bonus = min(15.0, max(0.0, swing_strength - float(self.settings["minSwingAtr"])) * 8.0)
        candidate_score = self._clamp((70.0 if from_htf else 58.0) + strength_bonus, 0.0, 100.0)

        merge_index = -1
        closest_distance = float("inf")
        for index, zone in enumerate(self.zones):
            distance = abs(zone.center - center)
            overlaps = candidate_bottom <= zone.top and candidate_top >= zone.bottom
            close_enough = distance <= source_atr * float(self.settings["mergeAtrMult"])
            if zone.kind == kind and (overlaps or close_enough) and distance < closest_distance:
                closest_distance, merge_index = distance, index

        if merge_index >= 0:
            zone = self.zones[merge_index]
            old_center = zone.center
            old_width = zone.top - zone.bottom
            merged_center = (
                old_center * max(1.0, zone.score) + center * candidate_score
            ) / (max(1.0, zone.score) + candidate_score)
            union_width = max(zone.top, candidate_top) - min(zone.bottom, candidate_bottom)
            merged_width = min(union_width, max(old_width, width) * 1.30)
            zone.top = merged_center + merged_width * 0.5
            zone.bottom = merged_center - merged_width * 0.5
            if origin_bar >= 0 and (
                zone.origin_bar < 0 or zone.origin_price is None
                or abs(center - merged_center) < abs(zone.origin_price - merged_center)
            ):
                zone.origin_bar = origin_bar
                zone.origin_price = center
            zone.score = self._clamp(max(zone.score, candidate_score) + 8.0, 0.0, 100.0)
            zone.confluence += 1
            zone.contains_htf = zone.contains_htf or from_htf
            zone.born_bar = min(zone.born_bar, source_bar)
        elif candidate_score >= float(self.settings["minCreateScore"]):
            self.zones.append(Zone(
                top=candidate_top, bottom=candidate_bottom, kind=kind, score=candidate_score,
                touches=0, born_bar=source_bar, origin_bar=origin_bar,
                origin_price=center if origin_bar >= 0 else None, last_touch_bar=-100000,
                pending_touch_bar=-100000, outside_closes=0, confluence=1,
                pending_reaction=False, awaiting_retest=False, contains_htf=from_htf,
            ))
            self._prune_kind(kind, current_price, current_atr, current_bar_index)

    @staticmethod
    def _pivot(window: list[list[float]], left: int, right: int, high_side: bool) -> float | None:
        center = window[left]
        value = center[2] if high_side else center[3]
        if high_side:
            # On ties, use the right-most equal extreme, matching highestbars' offset.
            valid = all(value >= item for item in (bar[2] for bar in window[:left])) and all(
                value > item for item in (bar[2] for bar in window[left + 1:])
            )
        else:
            valid = all(value <= item for item in (bar[3] for bar in window[:left])) and all(
                value < item for item in (bar[3] for bar in window[left + 1:])
            )
        return value if valid else None

    def _pivot_candidate(self, high_side: bool, left: int, right: int,
                         min_swing_atr: float | None = None) -> tuple[float | None, float | None, float | None]:
        window_size = left + right + 1
        if len(self._bars) < window_size:
            return None, None, None
        window = self._bars[-window_size:]
        point = self._pivot(window, left, right, high_side)
        pivot_atr = self._atrs[-right - 1]
        if point is None or pivot_atr is None or pivot_atr <= 0:
            return point, pivot_atr, None
        if high_side:
            swing = (point - min(bar[3] for bar in window)) / pivot_atr
        else:
            swing = (max(bar[2] for bar in window) - point) / pivot_atr
        return point, pivot_atr, swing

    def _select_nearest(self, price: float, atr: float | None) -> tuple[Zone | None, Zone | None]:
        tolerance = (atr or 0.0) * float(self.settings["breakBufferAtr"])
        supports = [zone for zone in self.zones if zone.kind == 1 and zone.center <= price + tolerance]
        resistances = [zone for zone in self.zones if zone.kind == -1 and zone.center >= price - tolerance]
        support = min(supports, key=lambda zone: self._distance(zone, price), default=None)
        resistance = min(resistances, key=lambda zone: self._distance(zone, price), default=None)
        return support, resistance

    def _support_hammer_match(self, bar: list[float], supports: Iterable[Zone],
                              atr: float | None) -> Zone | None:
        if atr is None or atr <= 0 or len(bar) < 5:
            return None
        open_price, high, low, close = bar[1:5]
        candle_range = high - low
        if candle_range <= 0 or candle_range < atr * float(self.settings["hammerMinRangeAtr"]):
            return None
        body = abs(close - open_price)
        body_ratio = body / candle_range
        lower_wick = min(open_price, close) - low
        upper_wick = high - max(open_price, close)
        close_location = (close - low) / candle_range
        lower_wick_minimum = body * float(self.settings["hammerMinLowerWickBody"])
        if not (
            float(self.settings["hammerMinBodyRange"]) <= body_ratio <= float(self.settings["hammerMaxBodyRange"])
            and (lower_wick >= lower_wick_minimum or isclose(
                lower_wick, lower_wick_minimum, rel_tol=1e-12, abs_tol=1e-12,
            ))
            and upper_wick / candle_range <= float(self.settings["hammerMaxUpperWickRange"])
            and close_location >= float(self.settings["hammerMinCloseLocation"])
        ):
            return None
        matches = [
            support for support in supports
            if low <= support.top
            and high >= support.bottom
            and close >= support.center
            and max(0.0, support.bottom - low)
            <= atr * float(self.settings["hammerMaxSupportPenetrationAtr"])
        ]
        return max(matches, key=lambda zone: (zone.score, -self._distance(zone, close)), default=None)

    def _is_support_hammer(self, bar: list[float], support: Zone | Iterable[Zone] | None,
                           atr: float | None) -> bool:
        supports = (support,) if isinstance(support, Zone) else support or ()
        return self._support_hammer_match(bar, supports, atr) is not None

    def _resistance_long_upper_wick_match(self, bar: list[float], resistances: Iterable[Zone],
                                          atr: float | None) -> Zone | None:
        if atr is None or atr <= 0 or len(bar) < 5:
            return None
        open_price, high, low, close = bar[1:5]
        candle_range = high - low
        if candle_range <= 0 or candle_range < atr * float(self.settings["invertedMinRangeAtr"]):
            return None
        body = abs(close - open_price)
        body_ratio = body / candle_range
        lower_wick = min(open_price, close) - low
        upper_wick = high - max(open_price, close)
        close_location = (close - low) / candle_range
        upper_wick_minimum = body * float(self.settings["invertedMinUpperWickBody"])
        if not (
            float(self.settings["invertedMinBodyRange"]) <= body_ratio <= float(self.settings["invertedMaxBodyRange"])
            and upper_wick >= upper_wick_minimum
            and lower_wick / candle_range <= float(self.settings["invertedMaxLowerWickRange"])
            and close_location <= float(self.settings["invertedMaxCloseLocation"])
        ):
            return None
        matches = [
            resistance for resistance in resistances
            if high >= resistance.bottom
            and low <= resistance.top
            and close <= resistance.center
            and max(0.0, high - resistance.top)
            <= atr * float(self.settings["invertedMaxResistancePenetrationAtr"])
        ]
        return max(matches, key=lambda zone: (zone.score, -self._distance(zone, close)), default=None)

    def _is_resistance_inverted_hammer(self, bar: list[float], resistance: Zone | Iterable[Zone] | None,
                                       atr: float | None) -> bool:
        resistances = (resistance,) if isinstance(resistance, Zone) else resistance or ()
        return self._resistance_long_upper_wick_match(bar, resistances, atr) is not None

    @staticmethod
    def _zone_dict(zone: Zone | None) -> dict[str, Any] | None:
        if zone is None:
            return None
        return {
            "top": zone.top, "bottom": zone.bottom, "kind": zone.kind,
            "score": zone.score, "touches": zone.touches, "bornBar": zone.born_bar,
            "originBar": zone.origin_bar, "originPrice": zone.origin_price,
            "confluence": zone.confluence, "containsHtf": zone.contains_htf,
            "awaitingRetest": zone.awaiting_retest,
            "pendingReaction": zone.pending_reaction,
        }

    def process(self, bars: Iterable[list[float]], htf_events: dict[int, list[tuple[int, float, float, float]]] | None = None) -> dict[str, Any] | None:
        """Process bars in ascending order; htf_events maps chart bar index to (kind, point, atr, strength)."""
        result = None
        htf_events = htf_events or {}
        left, right = int(self.settings["pivotLeft"]), int(self.settings["pivotRight"])
        for bar in bars:
            if len(bar) < 6 or not all(isfinite(float(value)) for value in bar[:6]):
                continue
            normalized = [float(value) for value in bar[:6]]
            bar_index = len(self._bars)
            previous_close = self._bars[-1][4] if self._bars else None
            atr = self._atr(normalized, previous_close)
            self._bars.append(normalized)
            self._atrs.append(atr)
            high, low, close = normalized[2], normalized[3], normalized[4]
            self.events = {
                "supportTouch": False, "resistanceTouch": False,
                "supportBroken": False, "resistanceBroken": False,
                "supportHammer": False, "resistanceInvertedHammer": False,
                "retestConfirmed": False, "retestDirection": 0,
            }

            pivot_high, pivot_high_atr, high_swing = self._pivot_candidate(True, left, right)
            pivot_low, pivot_low_atr, low_swing = self._pivot_candidate(False, left, right)
            if pivot_high is not None:
                self._add_candidate(pivot_high, -1, pivot_high_atr, high_swing, False,
                                    bar_index - right, close, atr, bar_index)
            if pivot_low is not None:
                self._add_candidate(pivot_low, 1, pivot_low_atr, low_swing, False,
                                    bar_index - right, close, atr, bar_index)

            for htf_event in htf_events.get(bar_index, []):
                kind, point, source_atr, swing = htf_event[:4]
                origin_bar = int(htf_event[4]) if len(htf_event) > 4 else bar_index
                self._add_candidate(point, kind, source_atr, swing, True, bar_index, close, atr,
                                    bar_index, origin_bar)

            index = 0
            while index < len(self.zones):
                zone = self.zones[index]
                if bar_index - zone.born_bar > int(self.settings["maxZoneAge"]):
                    self.zones.pop(index)
                    continue

                breakout_buffer = (atr or 0.0) * float(self.settings["breakBufferAtr"])
                outside = close < zone.bottom - breakout_buffer if zone.kind == 1 else close > zone.top + breakout_buffer
                zone.outside_closes = zone.outside_closes + 1 if outside else 0
                confirmed_break = zone.outside_closes >= int(self.settings["breakClosesNeed"])
                removed_by_break = False
                if confirmed_break:
                    if zone.kind == 1:
                        self.events["supportBroken"] = True
                    else:
                        self.events["resistanceBroken"] = True
                    if self.settings["enableRoleFlip"]:
                        zone.kind = -zone.kind
                        zone.score = self._clamp(zone.score - 15.0, float(self.settings["minCreateScore"]), 100.0)
                        zone.touches = 0
                        zone.born_bar = bar_index
                        zone.origin_bar = bar_index
                        zone.origin_price = close
                        zone.last_touch_bar = bar_index
                        zone.pending_touch_bar = -100000
                        zone.pending_reaction = False
                        zone.outside_closes = 0
                        zone.awaiting_retest = True
                    else:
                        self.zones.pop(index)
                        removed_by_break = True

                if not removed_by_break and index < len(self.zones):
                    current = self.zones[index]
                    in_zone = low <= current.top and high >= current.bottom
                    touch_allowed = bar_index > current.born_bar + 1 and bar_index - current.last_touch_bar >= int(self.settings["touchCooldown"])
                    if in_zone and touch_allowed:
                        current.touches += 1
                        current.last_touch_bar = bar_index
                        current.pending_touch_bar = bar_index
                        current.pending_reaction = True
                        if current.touches > int(self.settings["maxUsefulTouches"]):
                            current.score = self._clamp(current.score - 6.0, 0.0, 100.0)
                        if current.kind == 1:
                            self.events["supportTouch"] = True
                        else:
                            self.events["resistanceTouch"] = True
                    if current.pending_reaction:
                        elapsed = bar_index - current.pending_touch_bar
                        reacted = close >= current.top + (atr or 0.0) * float(self.settings["reactionAtr"]) if current.kind == 1 else close <= current.bottom - (atr or 0.0) * float(self.settings["reactionAtr"])
                        if reacted and elapsed <= int(self.settings["reactionBars"]):
                            bonus = 10.0 if current.touches <= 1 else 8.0 if current.touches == 2 else 4.0 if current.touches == 3 else -2.0
                            if current.awaiting_retest:
                                bonus += 10.0
                                current.awaiting_retest = False
                                self.events["retestConfirmed"] = True
                                self.events["retestDirection"] = current.kind
                            current.score = self._clamp(current.score + bonus, 0.0, 100.0)
                            current.pending_reaction = False
                        elif elapsed > int(self.settings["reactionBars"]):
                            current.score = self._clamp(current.score - 6.0, 0.0, 100.0)
                            current.pending_reaction = False
                if not removed_by_break:
                    index += 1

            self._prune_kind(1, close, atr, bar_index)
            self._prune_kind(-1, close, atr, bar_index)
            support, resistance = self._select_nearest(close, atr)
            support_hammer_zone = self._support_hammer_match(
                normalized, (zone for zone in self.zones if zone.kind == 1), atr,
            )
            resistance_long_upper_wick_zone = self._resistance_long_upper_wick_match(
                normalized, (zone for zone in self.zones if zone.kind == -1), atr,
            )
            self.events["supportHammer"] = support_hammer_zone is not None
            self.events["resistanceInvertedHammer"] = resistance_long_upper_wick_zone is not None
            self._support_hammer_history.append({
                "event": bool(self.events["supportHammer"]),
                "supportScore": float(support_hammer_zone.score) if support_hammer_zone else None,
            })
            self._resistance_inverted_hammer_history.append({
                "event": bool(self.events["resistanceInvertedHammer"]),
                "resistanceScore": float(resistance_long_upper_wick_zone.score)
                if resistance_long_upper_wick_zone else None,
            })
            if len(self._support_hammer_history) > 100:
                del self._support_hammer_history[:-100]
            if len(self._resistance_inverted_hammer_history) > 100:
                del self._resistance_inverted_hammer_history[:-100]
            result = {
                "timestamp": int(normalized[0]), "barIndex": bar_index,
                "price": close, "atr": atr,
                "support": self._zone_dict(support),
                "resistance": self._zone_dict(resistance),
                "supportHammerScore": float(support_hammer_zone.score) if support_hammer_zone else None,
                "resistanceLongUpperWickScore": float(resistance_long_upper_wick_zone.score)
                if resistance_long_upper_wick_zone else None,
                "zones": [self._zone_dict(zone) for zone in self.zones],
                "events": dict(self.events),
            }
        return result


def _atr_values(bars: list[list[float]], length: int) -> list[float | None]:
    seed: list[float] = []
    value: float | None = None
    values: list[float | None] = []
    previous_close = None
    for bar in bars:
        tr = bar[2] - bar[3] if previous_close is None else max(
            bar[2] - bar[3], abs(bar[2] - previous_close), abs(bar[3] - previous_close)
        )
        if value is None:
            seed.append(tr)
            if len(seed) == length:
                value = sum(seed) / length
        else:
            value = (value * (length - 1) + tr) / length
        values.append(value)
        previous_close = bar[4]
    return values


def confirmed_htf_events(htf_bars: list[list[float]], base_bars: list[list[float]],
                         base_duration_ms: int, htf_duration_ms: int,
                         settings: dict[str, Any] | None = None) -> dict[int, list[tuple[int, float, float, float, int]]]:
    """Return confirmed 4h pivot events aligned to chart-bar closes.

    A source index of -1 means the pivot candle is outside the supplied chart
    history. Callers must not substitute the later confirmation candle.
    """
    cfg = dict(DEFAULT_SETTINGS)
    if settings:
        cfg.update(settings)
    if not cfg["useHtf"] or not htf_bars or not base_bars:
        return {}
    left, right = int(cfg["htfPivotLeft"]), int(cfg["htfPivotRight"])
    atrs = _atr_values(htf_bars, int(cfg["atrLength"]))
    events_by_close: dict[int, list[tuple[int, float, float, float, int]]] = {}
    size = left + right + 1
    for current_index in range(size - 1, len(htf_bars)):
        window = htf_bars[current_index - size + 1:current_index + 1]
        atr_index = current_index - right
        pivot_atr = atrs[atr_index]
        if pivot_atr is None or pivot_atr <= 0:
            continue
        high = KeyLevelCalculator._pivot(window, left, right, True)
        if high is not None:
            strength = (high - min(bar[3] for bar in window)) / pivot_atr
            pivot_time = int(htf_bars[atr_index][0])
            events_by_close.setdefault(int(htf_bars[current_index][0] + htf_duration_ms), []).append(
                (-1, high, pivot_atr, strength, pivot_time)
            )
        low = KeyLevelCalculator._pivot(window, left, right, False)
        if low is not None:
            strength = (max(bar[2] for bar in window) - low) / pivot_atr
            pivot_time = int(htf_bars[atr_index][0])
            events_by_close.setdefault(int(htf_bars[current_index][0] + htf_duration_ms), []).append(
                (1, low, pivot_atr, strength, pivot_time)
            )

    result: dict[int, list[tuple[int, float, float, float, int]]] = {}
    base_open_times = [int(bar[0]) for bar in base_bars]
    # request.security(..., gaps_on, lookahead_off) exposes HTF values at the
    # chart bar closing the HTF confirmation bar; on larger charts only the
    # final lower-timeframe bar inside the chart bar is sampled.
    for base_index, bar in enumerate(base_bars):
        close_time = int(bar[0] + base_duration_ms)
        matching = events_by_close.get(close_time)
        if matching:
            aligned_events = []
            for kind, point, source_atr, strength, pivot_time in matching:
                source_index = bisect_right(base_open_times, pivot_time) - 1
                if source_index < 0 or pivot_time >= base_open_times[source_index] + base_duration_ms:
                    source_index = -1
                elif base_duration_ms <= htf_duration_ms:
                    first_child = bisect_right(base_open_times, pivot_time - 1)
                    after_children = bisect_right(base_open_times, pivot_time + htf_duration_ms - 1)
                    extreme_column = 2 if kind == -1 else 3
                    source_index = next((index for index in range(first_child, after_children)
                                         if isclose(float(base_bars[index][extreme_column]), point,
                                                    rel_tol=1e-9, abs_tol=1e-10)), -1)
                elif not (float(base_bars[source_index][3]) <= point <= float(base_bars[source_index][2])):
                    source_index = -1
                aligned_events.append((kind, point, source_atr, strength, source_index))
            result[base_index] = aligned_events
    return result


def calculate_key_levels(base_bars: list[list[float]], htf_bars: list[list[float]] | None = None,
                         base_duration_ms: int = 3_600_000, htf_duration_ms: int = 14_400_000,
                         settings: dict[str, Any] | None = None, tick_size: float = 0.0) -> dict[str, Any] | None:
    cfg = dict(DEFAULT_SETTINGS)
    if settings:
        cfg.update(settings)
    events = confirmed_htf_events(htf_bars or [], base_bars, base_duration_ms, htf_duration_ms, cfg)
    calculator = KeyLevelCalculator(cfg, tick_size)
    snapshot = calculator.process(base_bars, events)
    if snapshot is not None:
        snapshot["supportHammerHistory"] = list(calculator._support_hammer_history)
        snapshot["resistanceInvertedHammerHistory"] = list(
            calculator._resistance_inverted_hammer_history
        )
    return snapshot


def calculate_reference_levels(base_bars: list[list[float]], daily_bars: list[list[float]],
                               weekly_bars: list[list[float]], base_duration_ms: int) -> dict[str, float | None]:
    """Calculate the Pine script's previous-period levels and chart-timeframe VWAP."""
    if not base_bars:
        return {name: None for name in REFERENCE_FEATURES}
    base_bars = sorted(base_bars, key=lambda bar: int(bar[0]))
    daily_bars = sorted(daily_bars, key=lambda bar: int(bar[0]))
    weekly_bars = sorted(weekly_bars, key=lambda bar: int(bar[0]))
    chart_bar = base_bars[-1]
    chart_time = int(chart_bar[0])
    # request.security() on a chart above 1D exposes its final lower-timeframe bar.
    daily_sample_time = chart_time + base_duration_ms - DAY_MS if base_duration_ms > DAY_MS else chart_time

    def context_index(bars: list[list[float]], timestamp: int) -> int | None:
        selected = None
        for index, bar in enumerate(bars):
            if int(bar[0]) > timestamp:
                break
            selected = index
        return selected

    day_index = context_index(daily_bars, daily_sample_time)
    week_index = context_index(weekly_bars, chart_time)

    def field(bars: list[list[float]], index: int | None, offset: int, column: int) -> float | None:
        target = index + offset if index is not None else -1
        if target < 0 or target >= len(bars):
            return None
        return float(bars[target][column])

    vwap = None
    if day_index is not None:
        session_start = int(daily_bars[day_index][0])
        if base_duration_ms >= DAY_MS:
            vwap_bars = [chart_bar]
        else:
            vwap_bars = [bar for bar in base_bars if session_start <= int(bar[0]) <= chart_time]
        volume = sum(max(0.0, float(bar[5])) for bar in vwap_bars if len(bar) >= 6)
        if volume > 0:
            vwap = sum(
                ((float(bar[2]) + float(bar[3]) + float(bar[4])) / 3.0) * max(0.0, float(bar[5]))
                for bar in vwap_bars if len(bar) >= 6
            ) / volume

    return {
        "kl_previous_day_high": field(daily_bars, day_index, -1, 2),
        "kl_previous_day_low": field(daily_bars, day_index, -1, 3),
        "kl_previous_week_high": field(weekly_bars, week_index, -1, 2),
        "kl_previous_week_low": field(weekly_bars, week_index, -1, 3),
        "kl_day_open": field(daily_bars, day_index, 0, 1),
        "kl_session_vwap": vwap,
    }


def encode_conditions(snapshot: dict[str, Any] | None, price: float | None = None,
                      live: bool = False,
                      references: dict[str, float | None] | None = None) -> dict[str, float | None]:
    """Expose numeric features for the existing comparison-based condition builder."""
    if not snapshot:
        return dict(references or {})
    price = float(price if price is not None else snapshot.get("price", 0.0))
    atr = snapshot.get("atr")
    zones = snapshot.get("zones") or []
    tolerance = (atr or 0.0) * float(DEFAULT_SETTINGS["breakBufferAtr"])

    def distance_to_zone(zone: dict[str, Any]) -> float:
        return price - zone["top"] if price > zone["top"] else zone["bottom"] - price if price < zone["bottom"] else 0.0

    supports = [zone for zone in zones if zone.get("kind") == 1 and (zone["top"] + zone["bottom"]) * 0.5 <= price + tolerance]
    resistances = [zone for zone in zones if zone.get("kind") == -1 and (zone["top"] + zone["bottom"]) * 0.5 >= price - tolerance]
    fallback_support = None if "zones" in snapshot else snapshot.get("support")
    fallback_resistance = None if "zones" in snapshot else snapshot.get("resistance")
    support = min(supports, key=lambda zone: distance_to_zone(zone), default=fallback_support)
    resistance = min(resistances, key=lambda zone: distance_to_zone(zone), default=fallback_resistance)
    events = {} if live else (snapshot.get("events") or {})

    def value(zone: dict[str, Any] | None, field: str) -> float | None:
        item = zone.get(field) if zone else None
        return float(item) if item is not None else None

    def distance(zone: dict[str, Any] | None) -> float | None:
        if not zone:
            return None
        delta = zone["bottom"] - price if price < zone["bottom"] else price - zone["top"] if price > zone["top"] else 0.0
        return delta / atr if atr and atr > 0 else None

    return {
        "kl_support_exists": float(support is not None),
        "kl_support_top": value(support, "top"),
        "kl_support_bottom": value(support, "bottom"),
        "kl_support_score": value(support, "score"),
        "kl_support_touches": value(support, "touches"),
        "kl_support_confluence": value(support, "confluence"),
        "kl_support_htf": float(bool(support and support.get("containsHtf"))),
        "kl_support_retest_pending": float(bool(support and support.get("awaitingRetest"))),
        "kl_support_distance_atr": distance(support),
        "kl_resistance_exists": float(resistance is not None),
        "kl_resistance_top": value(resistance, "top"),
        "kl_resistance_bottom": value(resistance, "bottom"),
        "kl_resistance_score": value(resistance, "score"),
        "kl_resistance_touches": value(resistance, "touches"),
        "kl_resistance_confluence": value(resistance, "confluence"),
        "kl_resistance_htf": float(bool(resistance and resistance.get("containsHtf"))),
        "kl_resistance_retest_pending": float(bool(resistance and resistance.get("awaitingRetest"))),
        "kl_resistance_distance_atr": distance(resistance),
        "kl_event_support_touch": float(bool(events.get("supportTouch"))),
        "kl_event_support_hammer": float(bool(events.get("supportHammer"))),
        "kl_event_resistance_inverted_hammer": float(bool(events.get("resistanceInvertedHammer"))),
        "kl_event_resistance_touch": float(bool(events.get("resistanceTouch"))),
        "kl_event_support_break": float(bool(events.get("supportBroken"))),
        "kl_event_resistance_break": float(bool(events.get("resistanceBroken"))),
        "kl_event_support_retest": float(bool(events.get("retestConfirmed") and events.get("retestDirection") == 1)),
        "kl_event_resistance_retest": float(bool(events.get("retestConfirmed") and events.get("retestDirection") == -1)),
        **(references or {}),
    }


def feature_names() -> dict[str, str]:
    return {
        "kl_support_exists": "存在关键位支撑",
        "kl_support_top": "关键位支撑上沿", "kl_support_bottom": "关键位支撑下沿",
        "kl_support_score": "关键位支撑质量分", "kl_support_touches": "关键位支撑触碰次数",
        "kl_support_confluence": "关键位支撑合并次数", "kl_support_htf": "关键位支撑高周期共振",
        "kl_support_retest_pending": "关键位支撑等待回踩", "kl_support_distance_atr": "距关键位支撑 ATR",
        "kl_resistance_top": "关键位压力上沿", "kl_resistance_bottom": "关键位压力下沿",
        "kl_resistance_score": "关键位压力质量分", "kl_resistance_touches": "关键位压力触碰次数",
        "kl_resistance_confluence": "关键位压力合并次数", "kl_resistance_htf": "关键位压力高周期共振",
        "kl_resistance_retest_pending": "关键位压力等待回踩", "kl_resistance_distance_atr": "距关键位压力 ATR",
        "kl_event_support_touch": "事件：触碰支撑", "kl_event_resistance_touch": "事件：触碰压力",
        "kl_event_support_hammer": "事件：支撑锤型线拒绝",
        "kl_event_resistance_inverted_hammer": "事件：压力位长上影拒绝",
        "kl_event_support_break": "事件：跌破支撑", "kl_event_resistance_break": "事件：突破压力",
        "kl_event_support_retest": "事件：支撑回踩确认", "kl_event_resistance_retest": "事件：压力回踩确认",
        "kl_resistance_exists": "存在关键位压力",
        "kl_previous_day_high": "前一日高点", "kl_previous_day_low": "前一日低点",
        "kl_previous_week_high": "前一周高点", "kl_previous_week_low": "前一周低点",
        "kl_day_open": "当日开盘价", "kl_session_vwap": "会话 VWAP",
    }


def binary_features() -> set[str]:
    return {
        "kl_support_exists", "kl_support_htf", "kl_support_retest_pending",
        "kl_resistance_exists", "kl_resistance_htf", "kl_resistance_retest_pending",
        "kl_event_support_touch", "kl_event_resistance_touch",
        "kl_event_support_hammer",
        "kl_event_resistance_inverted_hammer",
        "kl_event_support_break", "kl_event_resistance_break",
        "kl_event_support_retest", "kl_event_resistance_retest",
    }
