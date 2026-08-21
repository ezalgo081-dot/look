"""
Pydantic models for structured option chain data flowing through the system.
"""
from __future__ import annotations
from pydantic import BaseModel
from typing import Optional


class StrikeData(BaseModel):
    strike_price: float

    # Call side
    ce_ltp: float = 0.0
    ce_oi: int = 0
    ce_oi_change: int = 0
    ce_volume: int = 0
    ce_iv: float = 0.0
    ce_bid: float = 0.0
    ce_ask: float = 0.0

    # Put side
    pe_ltp: float = 0.0
    pe_oi: int = 0
    pe_oi_change: int = 0
    pe_volume: int = 0
    pe_iv: float = 0.0
    pe_bid: float = 0.0
    pe_ask: float = 0.0

    # COA computed fields
    s_reversal_ce: Optional[str] = None     # "Break Even" / "Break Down" / price
    s_reversal_pe: Optional[str] = None
    s_reversal_ce_value: float = 0.0
    s_reversal_pe_value: float = 0.0
    wtt_wtb_pct: float = 50.0              # 0-100, >50 = weakness to top
    is_atm: bool = False

    # OI tracker computed fields
    ce_oi_change_diff: int = 0              # Change-of-change for CE OI
    pe_oi_change_diff: int = 0              # Change-of-change for PE OI

    # Volume tracker computed fields
    ce_vol_change: int = 0                  # Volume change from previous snapshot
    pe_vol_change: int = 0
    ce_vol_change_diff: int = 0             # Change-of-change for CE Volume
    pe_vol_change_diff: int = 0             # Change-of-change for PE Volume


class OIHistoryEntry(BaseModel):
    """One timestamped row of aggregated OI change data."""
    time: str = ""
    total_ce_oi_change: int = 0
    total_pe_oi_change: int = 0
    ce_oi_change_diff: int = 0              # Difference from previous entry
    pe_oi_change_diff: int = 0


class StrikeDiffEntry(BaseModel):
    """Per-strike difference of difference entry for detailed log."""
    strike_price: float
    call_oi_change: int = 0
    put_oi_change: int = 0
    call_diff: int = 0                      # Call OI change difference from previous
    put_diff: int = 0                       # Put OI change difference from previous
    difference: int = 0                     # call_oi_change - put_oi_change
    diff_of_diff: int = 0                  # difference - previous difference
    reversal: str = ""                      # "Break Even" / "Break Down" / strike price


class OptionChainSnapshot(BaseModel):
    symbol: str
    spot_price: float = 0.0
    future_price: float = 0.0
    timestamp: str = ""
    expiry: str = ""
    atm_strike: float = 0.0
    max_pain: float = 0.0
    max_gain: float = 0.0
    lot_size: int = 25
    total_ce_oi: int = 0
    total_pe_oi: int = 0
    pcr: float = 0.0                        # Put-Call Ratio
    magical_lines: list[float] = []          # 6 key S/R levels
    strikes: list[StrikeData] = []
    fetch_latency_ms: float = 0.0
    process_latency_ms: float = 0.0
    risky: str = "Moderate"                  # "Risky" / "Moderate" / "Safe"
    oi_history: list[OIHistoryEntry] = []    # Timestamped OI change log
    strike_diff_log: list[dict] = []         # Detailed per-strike diff-of-diff log: [{time, strikes: [StrikeDiffEntry]}]