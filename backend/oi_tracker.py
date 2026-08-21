"""
OI (Open Interest) Change Tracker.

Maintains a rolling history of OI snapshots so we can compute:
  1. OI Change       = current OI - previous OI  (already from NSE)
  2. OI Change Diff  = current OI_change - previous OI_change  (second derivative)

The client specifically asked for "OI changing difference and changing OI ka difference"
at two consecutive timestamps (e.g., 12:30 and 12:31).

This module also builds a timestamped history table of aggregated OI changes
that the frontend displays as a scrollable log.
"""
from __future__ import annotations

import time
from datetime import datetime

from backend.config import MAX_OI_SNAPSHOTS
from backend.models import OptionChainSnapshot, OIHistoryEntry, StrikeDiffEntry


class OISnapshot:
    """One point-in-time capture of OI change and volume values per strike."""

    __slots__ = ("timestamp", "epoch", "ce_oi_change", "pe_oi_change",
                 "total_ce_oi_change", "total_pe_oi_change",
                 "ce_volume", "pe_volume")

    def __init__(self, timestamp: str, epoch: float):
        self.timestamp = timestamp
        self.epoch = epoch
        self.ce_oi_change: dict[float, int] = {}
        self.pe_oi_change: dict[float, int] = {}
        self.total_ce_oi_change: int = 0
        self.total_pe_oi_change: int = 0
        self.ce_volume: dict[float, int] = {}
        self.pe_volume: dict[float, int] = {}


class OITracker:
    """
    Accumulates OI snapshots over the trading session and enriches each
    OptionChainSnapshot with the change-of-change (diff) values.
    """

    def __init__(self) -> None:
        self._snapshots: list[OISnapshot] = []
        self._history: list[OIHistoryEntry] = []
        self._strike_diff_log: list[dict] = []  # [{time, strikes: [StrikeDiffEntry]}]
        self._prev_strike_diffs: dict[float, int] = {}  # strike -> previous difference value

    def record_and_enrich(self, snapshot: OptionChainSnapshot) -> OptionChainSnapshot:
        """
        1. Record current OI change values.
        2. Compute diff against the previous snapshot.
        3. Write the diff back into each StrikeData.
        4. Build the timestamped history table.
        """
        now = time.time()
        current = OISnapshot(snapshot.timestamp, now)

        total_ce_chg = 0
        total_pe_chg = 0

        for s in snapshot.strikes:
            current.ce_oi_change[s.strike_price] = s.ce_oi_change
            current.pe_oi_change[s.strike_price] = s.pe_oi_change
            current.ce_volume[s.strike_price] = s.ce_volume
            current.pe_volume[s.strike_price] = s.pe_volume
            total_ce_chg += s.ce_oi_change
            total_pe_chg += s.pe_oi_change

        current.total_ce_oi_change = total_ce_chg
        current.total_pe_oi_change = total_pe_chg

        prev = self._snapshots[-1] if self._snapshots else None

        # Per-strike second derivative (OI + Volume)
        # Initialize to 0 if no previous snapshot
        for s in snapshot.strikes:
            if prev is not None:
                sp = s.strike_price
                prev_ce = prev.ce_oi_change.get(sp, 0)
                prev_pe = prev.pe_oi_change.get(sp, 0)
                s.ce_oi_change_diff = s.ce_oi_change - prev_ce
                s.pe_oi_change_diff = s.pe_oi_change - prev_pe

                prev_ce_vol = prev.ce_volume.get(sp, 0)
                prev_pe_vol = prev.pe_volume.get(sp, 0)
                s.ce_vol_change = s.ce_volume - prev_ce_vol
                s.pe_vol_change = s.pe_volume - prev_pe_vol
            else:
                # First snapshot - no previous data, so diff is 0
                s.ce_oi_change_diff = 0
                s.pe_oi_change_diff = 0
                s.ce_vol_change = 0
                s.pe_vol_change = 0

        # Volume diff-of-diff (third snapshot needed)
        if len(self._snapshots) >= 2:
            prev_prev = self._snapshots[-2] if len(self._snapshots) >= 2 else None
            if prev is not None and prev_prev is not None:
                for s in snapshot.strikes:
                    sp = s.strike_price
                    prev_ce_vol_chg = prev.ce_volume.get(sp, 0) - prev_prev.ce_volume.get(sp, 0)
                    prev_pe_vol_chg = prev.pe_volume.get(sp, 0) - prev_prev.pe_volume.get(sp, 0)
                    s.ce_vol_change_diff = s.ce_vol_change - prev_ce_vol_chg
                    s.pe_vol_change_diff = s.pe_vol_change - prev_pe_vol_chg

        # Build history entry
        ce_diff = total_ce_chg - prev.total_ce_oi_change if prev else 0
        pe_diff = total_pe_chg - prev.total_pe_oi_change if prev else 0

        time_str = datetime.now().strftime("%H:%M:%S")

        entry = OIHistoryEntry(
            time=time_str,
            total_ce_oi_change=total_ce_chg,
            total_pe_oi_change=total_pe_chg,
            ce_oi_change_diff=ce_diff,
            pe_oi_change_diff=pe_diff,
        )
        self._history.append(entry)

        # Keep last 60 entries (~90 seconds of data at 1.5s interval)
        if len(self._history) > 60:
            self._history = self._history[-60:]

        self._snapshots.append(current)
        if len(self._snapshots) > MAX_OI_SNAPSHOTS:
            self._snapshots.pop(0)

        # Attach history to snapshot for frontend
        snapshot.oi_history = list(self._history)
        
        # Build detailed per-strike diff-of-diff log
        time_str = datetime.now().strftime("%H:%M:%S")
        strike_entries = []
        
        for s in snapshot.strikes:
            sp = s.strike_price
            call_chg = s.ce_oi_change
            put_chg = s.pe_oi_change
            call_diff = s.ce_oi_change_diff
            put_diff = s.pe_oi_change_diff
            difference = call_chg - put_chg
            
            # Calculate diff of diff
            prev_diff = self._prev_strike_diffs.get(sp, 0)
            diff_of_diff = difference - prev_diff
            self._prev_strike_diffs[sp] = difference
            
            # Determine reversal based on S-Reversal
            reversal = ""
            if s.s_reversal_ce == "Break Even" or s.s_reversal_pe == "Break Even":
                reversal = "Break Even"
            elif s.s_reversal_ce == "Break Down" or s.s_reversal_pe == "Break Down":
                reversal = "Break Down"
            elif s.s_reversal_ce_value > 0:
                reversal = str(s.s_reversal_ce_value)
            elif s.s_reversal_pe_value > 0:
                reversal = str(s.s_reversal_pe_value)
            else:
                reversal = str(sp)
            
            strike_entries.append(StrikeDiffEntry(
                strike_price=sp,
                call_oi_change=call_chg,
                put_oi_change=put_chg,
                call_diff=call_diff,
                put_diff=put_diff,
                difference=difference,
                diff_of_diff=diff_of_diff,
                reversal=reversal,
            ))
        
        # Add to log (keep last 100 entries)
        self._strike_diff_log.append({
            "time": time_str,
            "strikes": [e.model_dump() for e in strike_entries],
        })
        if len(self._strike_diff_log) > 100:
            self._strike_diff_log = self._strike_diff_log[-100:]
        
        snapshot.strike_diff_log = list(self._strike_diff_log)

        return snapshot

    def get_history(self, strike: float) -> list[dict]:
        """Return the OI change history for a specific strike (for charting)."""
        history = []
        for snap in self._snapshots:
            history.append({
                "timestamp": snap.timestamp,
                "epoch": snap.epoch,
                "ce_oi_change": snap.ce_oi_change.get(strike, 0),
                "pe_oi_change": snap.pe_oi_change.get(strike, 0),
            })
        return history

    @property
    def snapshot_count(self) -> int:
        return len(self._snapshots)
