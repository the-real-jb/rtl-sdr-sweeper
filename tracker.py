"""Track hit occupancy across sweep passes for burst-chatter ranking.

Pure module: no dongle or network. Reuses hits.Hit for sweep input.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from hits import Hit

_PREFERRED_LABELS = frozenset({"nfm_voice", "airband_am"})
_SKIP_LABELS = frozenset({"palmetto800"})


@dataclass
class OccupancyEvent:
    freq_hz: float
    power_db: float
    bandwidth_hz: float
    label: str
    reason: str
    is_new: bool
    duty: float
    kind: str  # "burst" | "persistent" | "unknown"


@dataclass
class _Track:
    freq_hz: float
    label: str
    reason: str
    power_db: float
    bandwidth_hz: float
    # Per-pass presence inside the sliding window (True = seen that pass).
    seen: deque[bool]
    # Was this track present on the previous completed pass?
    present_last_pass: bool = False


class OccupancyTracker:
    """Accumulate sweep hits and classify burst vs persistent occupancy."""

    def __init__(
        self,
        window: int = 10,
        match_hz: float = 50_000.0,
        persistent_min_duty: float = 0.8,
        burst_max_duty: float = 0.5,
        min_history: int = 3,
    ) -> None:
        if window < 1:
            raise ValueError("window must be >= 1")
        self.window = window
        self.match_hz = match_hz
        self.persistent_min_duty = persistent_min_duty
        self.burst_max_duty = burst_max_duty
        self.min_history = min_history
        self._tracks: list[_Track] = []
        self._last_events: list[OccupancyEvent] = []

    def update(self, hits: list[Hit]) -> list[OccupancyEvent]:
        """Record one sweep pass. Returns events for hits present this pass."""
        matched: set[int] = set()
        events: list[OccupancyEvent] = []

        for hit in hits:
            track_idx = self._match_track_index(hit.freq_hz, matched)
            if track_idx is None:
                track = _Track(
                    freq_hz=hit.freq_hz,
                    label=hit.label,
                    reason=hit.reason,
                    power_db=hit.power_db,
                    bandwidth_hz=hit.bandwidth_hz,
                    seen=deque(maxlen=self.window),
                    present_last_pass=False,
                )
                self._tracks.append(track)
                track_idx = len(self._tracks) - 1
            else:
                track = self._tracks[track_idx]
                track.freq_hz = hit.freq_hz
                track.label = hit.label
                track.reason = hit.reason
                track.power_db = hit.power_db
                track.bandwidth_hz = hit.bandwidth_hz
            matched.add(track_idx)

            is_new = not track.present_last_pass
            track.seen.append(True)
            duty = self._duty(track)
            kind = self._kind(track, duty)
            events.append(
                OccupancyEvent(
                    freq_hz=track.freq_hz,
                    power_db=track.power_db,
                    bandwidth_hz=track.bandwidth_hz,
                    label=track.label,
                    reason=track.reason,
                    is_new=is_new,
                    duty=duty,
                    kind=kind,
                )
            )

        # Misses: append False for unmatched tracks still in window history.
        for i, track in enumerate(self._tracks):
            if i in matched:
                track.present_last_pass = True
            else:
                track.seen.append(False)
                track.present_last_pass = False

        # Drop tracks that have fallen entirely out of the window.
        self._tracks = [t for t in self._tracks if any(t.seen)]
        self._last_events = events
        return events

    def rank(self, events: list[OccupancyEvent] | None = None) -> list[OccupancyEvent]:
        """Rank candidates for auto-listen; skips palmetto800."""
        candidates = events if events is not None else self._last_events
        eligible = [e for e in candidates if e.label not in _SKIP_LABELS]
        return sorted(eligible, key=self._rank_key)

    def _match_track_index(self, freq_hz: float, matched: set[int]) -> int | None:
        best_i: int | None = None
        best_dist = self.match_hz
        for i, track in enumerate(self._tracks):
            if i in matched:
                continue
            dist = abs(track.freq_hz - freq_hz)
            if dist < best_dist:
                best_dist = dist
                best_i = i
        return best_i

    def _duty(self, track: _Track) -> float:
        if not track.seen:
            return 0.0
        return sum(track.seen) / len(track.seen)

    def _kind(self, track: _Track, duty: float) -> str:
        if len(track.seen) < self.min_history:
            return "unknown"
        if duty >= self.persistent_min_duty:
            return "persistent"
        if duty <= self.burst_max_duty:
            return "burst"
        return "unknown"

    @staticmethod
    def _rank_key(event: OccupancyEvent) -> tuple:
        # Lower sorts first.
        kind_rank = {"burst": 0, "unknown": 1, "persistent": 2}.get(event.kind, 3)
        preferred = 0 if (
            event.label in _PREFERRED_LABELS or event.reason == "hunt"
        ) else 1
        # Sticky FM broadcast: push to the back.
        sticky_fm = (
            1
            if event.label == "fm_broadcast" and event.kind == "persistent"
            else 0
        )
        # Hunt reason among preferred: slight boost (0 before 1).
        hunt_boost = 0 if event.reason == "hunt" else 1
        return (sticky_fm, kind_rank, preferred, hunt_boost, -event.power_db)
