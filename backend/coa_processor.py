"""
COA (Chart of Accuracy) Processor -- reverse-engineered from the LTP Calculator screenshot.

Computes:
  - ATM strike detection
  - S-Reversal levels (support / resistance per strike)
  - WTT/WTB percentage (Weakness To Top / Weakness To Bottom)
  - Max Pain & Max Gain
  - 6 Magical SPOT Lines (top OI-based support/resistance)
  - Risky / Moderate / Safe classification
"""
from __future__ import annotations

import time
from typing import Optional

from backend.config import LOT_SIZE, STRIKES_ABOVE_BELOW_ATM
from backend.models import StrikeData, OptionChainSnapshot


def _nearest_strike(spot: float, strikes: list[float], step: float = 50.0) -> float:
    """Round spot to the nearest valid strike price."""
    if not strikes:
        return round(spot / step) * step
    return min(strikes, key=lambda s: abs(s - spot))


def compute_max_pain(records: list[dict], all_strikes: list[float]) -> float:
    """
    Max Pain = the strike at which total intrinsic value loss for ALL option
    writers (CE + PE) is minimized.
    """
    if not records or not all_strikes:
        return 0.0

    oi_map_ce = {}
    oi_map_pe = {}
    for r in records:
        sp = r["strike_price"]
        if r.get("ce"):
            oi_map_ce[sp] = r["ce"].get("oi", 0)
        if r.get("pe"):
            oi_map_pe[sp] = r["pe"].get("oi", 0)

    min_pain = float("inf")
    pain_strike = all_strikes[0]

    for expiry_price in all_strikes:
        total_pain = 0.0
        for sp, oi in oi_map_ce.items():
            if expiry_price > sp:
                total_pain += (expiry_price - sp) * oi
        for sp, oi in oi_map_pe.items():
            if expiry_price < sp:
                total_pain += (sp - expiry_price) * oi
        if total_pain < min_pain:
            min_pain = total_pain
            pain_strike = expiry_price

    return pain_strike


def compute_max_gain(records: list[dict], all_strikes: list[float]) -> float:
    """
    Max Gain = strike where total premium collected by writers is highest,
    approximated as the strike with the highest combined CE+PE OI (writers
    collected the most premium there).
    """
    if not records:
        return 0.0

    best_strike = 0.0
    best_oi = 0
    for r in records:
        combined = 0
        if r.get("ce"):
            combined += r["ce"].get("oi", 0)
        if r.get("pe"):
            combined += r["pe"].get("oi", 0)
        if combined > best_oi:
            best_oi = combined
            best_strike = r["strike_price"]
    return best_strike


def compute_magical_lines(records: list[dict]) -> list[float]:
    """
    6 Magical SPOT Lines = top 3 strikes by CE OI + top 3 by PE OI.
    These represent the strongest resistance (CE) and support (PE) levels.
    """
    ce_oi_list = []
    pe_oi_list = []
    for r in records:
        sp = r["strike_price"]
        if r.get("ce"):
            ce_oi_list.append((sp, r["ce"].get("oi", 0)))
        if r.get("pe"):
            pe_oi_list.append((sp, r["pe"].get("oi", 0)))

    ce_oi_list.sort(key=lambda x: x[1], reverse=True)
    pe_oi_list.sort(key=lambda x: x[1], reverse=True)

    resistance = [s for s, _ in ce_oi_list[:3]]
    support = [s for s, _ in pe_oi_list[:3]]

    lines = sorted(set(support + resistance))
    return lines[:6]


def compute_s_reversal(
    strike: float,
    ce_oi: int,
    pe_oi: int,
    ce_oi_change: int,
    pe_oi_change: int,
    spot: float,
) -> tuple[str, float, str, float]:
    """
    S-Reversal per strike:
      - CE side: If PE OI >> CE OI at this strike, it acts as support ("Break Even").
                 If CE OI >> PE OI, it acts as resistance ("Break Down").
      - PE side: Mirror logic.

    The reversal *value* is the approximate price level where the reversal triggers,
    derived from the OI ratio weighted by distance from spot.

    Returns: (ce_label, ce_value, pe_label, pe_value)
    """
    total_oi = ce_oi + pe_oi
    if total_oi == 0:
        return ("", 0.0, "", 0.0)

    ce_ratio = ce_oi / total_oi
    pe_ratio = pe_oi / total_oi

    # CE-side reversal
    if pe_ratio > 0.6:
        ce_label = "Break Even"
        ce_value = strike - (strike - spot) * pe_ratio * 0.5
    elif ce_ratio > 0.6:
        ce_label = "Break Down"
        ce_value = strike + (spot - strike) * ce_ratio * 0.5
    else:
        ce_label = ""
        ce_value = strike

    # PE-side reversal
    if ce_ratio > 0.6:
        pe_label = "Break Even"
        pe_value = strike + (spot - strike) * ce_ratio * 0.5
    elif pe_ratio > 0.6:
        pe_label = "Break Down"
        pe_value = strike - (strike - spot) * pe_ratio * 0.5
    else:
        pe_label = ""
        pe_value = strike

    return (ce_label, round(ce_value, 2), pe_label, round(pe_value, 2))


def compute_wtt_wtb(ce_oi_change: int, pe_oi_change: int) -> float:
    """
    WTT/WTB percentage for a strike.
    = (CE OI Change / (CE OI Change + PE OI Change)) * 100

    >50% means more call writing => Weakness To Top (bearish).
    <50% means more put writing  => Weakness To Bottom (bullish).
    """
    total = abs(ce_oi_change) + abs(pe_oi_change)
    if total == 0:
        return 50.0
    return round((abs(ce_oi_change) / total) * 100, 2)


def classify_risk(pcr: float, spot: float, max_pain: float) -> str:
    """Simple risk classification based on PCR and spot-vs-max-pain distance."""
    distance_pct = abs(spot - max_pain) / spot * 100 if spot else 0

    if pcr > 1.3 or pcr < 0.7 or distance_pct > 2.0:
        return "Risky"
    elif 0.9 <= pcr <= 1.1 and distance_pct < 0.5:
        return "Safe"
    else:
        return "Moderate"


def process_option_chain(parsed: dict) -> OptionChainSnapshot:
    """
    Main processing pipeline: takes parsed NSE data and produces a full
    OptionChainSnapshot with all COA-computed fields.
    """
    start = time.perf_counter()

    symbol = parsed["symbol"]
    spot = parsed["spot_price"]
    future = parsed["future_price"]
    records = parsed["records"]
    timestamp = parsed["timestamp"]
    expiry = parsed["selected_expiry"]
    fetch_latency = parsed["fetch_latency_ms"]

    all_strikes = sorted(set(r["strike_price"] for r in records))
    atm = _nearest_strike(spot, all_strikes)

    # Filter to N strikes above/below ATM
    atm_idx = all_strikes.index(atm) if atm in all_strikes else len(all_strikes) // 2
    lo = max(0, atm_idx - STRIKES_ABOVE_BELOW_ATM)
    hi = min(len(all_strikes), atm_idx + STRIKES_ABOVE_BELOW_ATM + 1)
    visible_strikes = set(all_strikes[lo:hi])

    visible_records = [r for r in records if r["strike_price"] in visible_strikes]

    max_pain = compute_max_pain(records, all_strikes)
    max_gain = compute_max_gain(records, all_strikes)
    magical = compute_magical_lines(records)

    total_ce_oi = 0
    total_pe_oi = 0
    strike_models: list[StrikeData] = []

    for r in visible_records:
        sp = r["strike_price"]
        ce = r.get("ce") or {}
        pe = r.get("pe") or {}

        ce_oi = ce.get("oi", 0)
        pe_oi = pe.get("oi", 0)
        ce_oi_chg = ce.get("changeinOpenInterest", 0)
        pe_oi_chg = pe.get("changeinOpenInterest", 0)

        total_ce_oi += ce_oi
        total_pe_oi += pe_oi

        ce_label, ce_val, pe_label, pe_val = compute_s_reversal(
            sp, ce_oi, pe_oi, ce_oi_chg, pe_oi_chg, spot
        )
        wtt_pct = compute_wtt_wtb(ce_oi_chg, pe_oi_chg)

        strike_models.append(StrikeData(
            strike_price=sp,
            ce_ltp=ce.get("ltp", 0.0),
            ce_oi=ce_oi,
            ce_oi_change=ce_oi_chg,
            ce_volume=ce.get("totalTradedVolume", 0),
            ce_iv=ce.get("impliedVolatility", 0.0),
            ce_bid=ce.get("bidprice", 0.0),
            ce_ask=ce.get("askPrice", 0.0),
            pe_ltp=pe.get("ltp", 0.0),
            pe_oi=pe_oi,
            pe_oi_change=pe_oi_chg,
            pe_volume=pe.get("totalTradedVolume", 0),
            pe_iv=pe.get("impliedVolatility", 0.0),
            pe_bid=pe.get("bidprice", 0.0),
            pe_ask=pe.get("askPrice", 0.0),
            s_reversal_ce=ce_label,
            s_reversal_ce_value=ce_val,
            s_reversal_pe=pe_label,
            s_reversal_pe_value=pe_val,
            wtt_wtb_pct=wtt_pct,
            is_atm=(sp == atm),
        ))

    pcr = total_pe_oi / total_ce_oi if total_ce_oi else 0.0
    lot = LOT_SIZE.get(symbol, 25)
    risky = classify_risk(pcr, spot, max_pain)

    process_ms = (time.perf_counter() - start) * 1000

    return OptionChainSnapshot(
        symbol=symbol,
        spot_price=spot,
        future_price=future,
        timestamp=timestamp,
        expiry=expiry,
        atm_strike=atm,
        max_pain=max_pain,
        max_gain=max_gain,
        lot_size=lot,
        total_ce_oi=total_ce_oi,
        total_pe_oi=total_pe_oi,
        pcr=round(pcr, 4),
        magical_lines=magical,
        strikes=strike_models,
        fetch_latency_ms=round(fetch_latency, 1),
        process_latency_ms=round(process_ms, 1),
        risky=risky,
    )
