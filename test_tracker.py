"""OccupancyTracker: cross-pass is_new, duty, kind, and auto-listen ranking."""

from hits import Hit
from tracker import OccupancyTracker


def _hit(
    freq_hz: float,
    *,
    power_db: float = -70.0,
    bandwidth_hz: float = 12_500.0,
    label: str = "nfm_voice",
    reason: str = "voice",
) -> Hit:
    return Hit(
        freq_hz=freq_hz,
        power_db=power_db,
        bandwidth_hz=bandwidth_hz,
        label=label,
        reason=reason,
        is_new=False,
    )


def test_first_pass_marks_new_unknown_duty():
    tracker = OccupancyTracker(window=5)
    events = tracker.update([_hit(146.52e6)])
    assert len(events) == 1
    ev = events[0]
    assert ev.is_new is True
    assert ev.duty == 1.0
    assert ev.kind == "unknown"
    assert ev.freq_hz == 146.52e6
    assert ev.label == "nfm_voice"


def test_second_pass_same_freq_not_new():
    tracker = OccupancyTracker(window=5)
    tracker.update([_hit(146.52e6)])
    events = tracker.update([_hit(146.52e6, power_db=-65.0)])
    assert events[0].is_new is False
    assert events[0].duty == 1.0


def test_reappear_after_miss_is_new():
    tracker = OccupancyTracker(window=5)
    tracker.update([_hit(146.52e6)])
    tracker.update([])  # miss
    events = tracker.update([_hit(146.52e6)])
    assert events[0].is_new is True


def test_nearby_freqs_match_within_tolerance():
    tracker = OccupancyTracker(window=5, match_hz=50_000.0)
    tracker.update([_hit(146.520e6)])
    events = tracker.update([_hit(146.530e6)])  # 10 kHz away
    assert events[0].is_new is False


def test_duty_and_persistent_kind():
    tracker = OccupancyTracker(window=5, persistent_min_duty=0.8, min_history=3)
    for _ in range(5):
        events = tracker.update([_hit(100.1e6, label="fm_broadcast", reason="active")])
    assert events[0].duty == 1.0
    assert events[0].kind == "persistent"
    assert events[0].is_new is False


def test_burst_kind_from_low_duty():
    tracker = OccupancyTracker(
        window=5, burst_max_duty=0.5, persistent_min_duty=0.8, min_history=3
    )
    # Seen on pass 1 and 5 only → duty 2/5 = 0.4 → burst
    tracker.update([_hit(156.8e6)])
    tracker.update([])
    tracker.update([])
    tracker.update([])
    events = tracker.update([_hit(156.8e6)])
    assert events[0].duty == 0.4
    assert events[0].kind == "burst"
    assert events[0].is_new is True


def test_unknown_kind_until_enough_history():
    tracker = OccupancyTracker(window=10, min_history=5, burst_max_duty=0.5)
    # Low duty but only 2 passes of history
    tracker.update([_hit(156.8e6)])
    events = tracker.update([])
    assert events == []
    events = tracker.update([_hit(156.8e6)])
    assert events[0].kind == "unknown"


def test_rank_skips_palmetto800():
    tracker = OccupancyTracker(window=5, min_history=3)
    for _ in range(3):
        tracker.update(
            [
                _hit(770e6, label="palmetto800", reason="active"),
                _hit(146.52e6, label="nfm_voice", reason="voice"),
            ]
        )
    ranked = tracker.rank()
    assert all(e.label != "palmetto800" for e in ranked)
    assert ranked[0].label == "nfm_voice"


def test_rank_prefers_burst_voice_over_persistent_fm():
    tracker = OccupancyTracker(
        window=5, burst_max_duty=0.5, persistent_min_duty=0.8, min_history=3
    )
    # FM always on
    # Voice: pass 1 and 5 only
    tracker.update(
        [
            _hit(100.1e6, label="fm_broadcast", reason="active", power_db=-50.0),
            _hit(156.8e6, label="nfm_voice", reason="voice", power_db=-80.0),
        ]
    )
    for _ in range(3):
        tracker.update(
            [_hit(100.1e6, label="fm_broadcast", reason="active", power_db=-50.0)]
        )
    events = tracker.update(
        [
            _hit(100.1e6, label="fm_broadcast", reason="active", power_db=-50.0),
            _hit(156.8e6, label="nfm_voice", reason="voice", power_db=-80.0),
        ]
    )
    by_label = {e.label: e for e in events}
    assert by_label["fm_broadcast"].kind == "persistent"
    assert by_label["nfm_voice"].kind == "burst"

    ranked = tracker.rank()
    assert ranked[0].label == "nfm_voice"
    assert ranked[0].kind == "burst"


def test_rank_prefers_hunt_burst():
    tracker = OccupancyTracker(window=5, burst_max_duty=0.5, min_history=3)
    tracker.update([_hit(144.39e6, label="nfm_voice", reason="hunt")])
    tracker.update([])
    tracker.update([])
    tracker.update([])
    tracker.update(
        [
            _hit(144.39e6, label="nfm_voice", reason="hunt"),
            _hit(147.0e6, label="nfm_voice", reason="voice", power_db=-60.0),
        ]
    )
    # Make 147 persistent by also feeding it every pass... already only on last.
    # Feed another always-on voice so hunt burst wins.
    tracker2 = OccupancyTracker(window=5, burst_max_duty=0.5, min_history=3)
    for i in range(5):
        hits = [_hit(147.0e6, label="nfm_voice", reason="voice", power_db=-60.0)]
        if i in (0, 4):
            hits.append(_hit(144.39e6, label="nfm_voice", reason="hunt"))
        tracker2.update(hits)
    ranked = tracker2.rank()
    assert ranked[0].reason == "hunt"
    assert ranked[0].kind == "burst"


def test_empty_sweep_advances_window():
    tracker = OccupancyTracker(window=3)
    tracker.update([_hit(146.52e6)])
    tracker.update([])
    tracker.update([])
    events = tracker.update([_hit(146.52e6)])
    # window=3: seen on last pass only among last 3 → duty 1/3
    assert abs(events[0].duty - 1 / 3) < 1e-9
