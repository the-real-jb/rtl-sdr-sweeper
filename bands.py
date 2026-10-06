"""Map center frequencies to band labels and listen modes.

Owns named sweep bands, Palmetto 800 exclusion from analog demod, and the
watch-frequency list used by the scanner hunt reason. Pure functions only;
no dongle or thread concerns.
"""

from __future__ import annotations

BANDS: dict[str, tuple[float, float]] = {
    "fm": (88e6, 108e6),
    "air": (118e6, 137e6),
    "ham2m": (144e6, 148e6),
    "ham70cm": (420e6, 450e6),
    "activity": (118e6, 174e6),
}

# Watch frequencies from the service spec (Hz).
HUNT_HZ: tuple[float, ...] = (
    121.500e6,
    144.390e6,
    145.800e6,
    146.520e6,
    156.800e6,
    162.400e6,
    162.425e6,
    162.450e6,
    162.475e6,
    162.500e6,
    162.525e6,
    162.550e6,
    433.920e6,
    446.000e6,
    1090.000e6,
)

_PALMETTO_LOW = (769e6, 775e6)
_PALMETTO_HIGH = (851e6, 861e6)
_MARINE = (156e6, 162e6)
_ISM_433 = (433.05e6, 434.79e6)

# SE-US analog voice rotation for burst-chatter monitor (name, start_hz, stop_hz).
# Entries named *_no_autolisten are for identification only (Palmetto P25); classify
# already returns palmetto800 and listen_mode refuses demod.
CHATTER_BANDS: list[tuple[str, float, float]] = [
    ("air", 118e6, 137e6),
    ("murs", 151.82e6, 154.60e6),
    ("marine_rail", 156e6, 162e6),
    ("ham2m", 144e6, 148e6),
    ("ism433", 433.05e6, 434.79e6),
    ("ham70cm", 420e6, 450e6),
    ("gmrs", 462.55e6, 467.725e6),
    ("palmetto800_low_no_autolisten", *_PALMETTO_LOW),
    ("palmetto800_high_no_autolisten", *_PALMETTO_HIGH),
]


def _in_range(freq_hz: float, low_hz: float, high_hz: float) -> bool:
    return low_hz <= freq_hz <= high_hz


def classify(freq_hz: float) -> str:
    if _in_range(freq_hz, *_PALMETTO_LOW) or _in_range(freq_hz, *_PALMETTO_HIGH):
        return "palmetto800"
    low, high = BANDS["fm"]
    if _in_range(freq_hz, low, high):
        return "fm_broadcast"
    low, high = BANDS["air"]
    if _in_range(freq_hz, low, high):
        return "airband_am"
    if _in_range(freq_hz, *_MARINE):
        return "nfm_voice"
    low, high = BANDS["ham2m"]
    if _in_range(freq_hz, low, high):
        return "nfm_voice"
    if _in_range(freq_hz, *_ISM_433):
        return "unknown"
    low, high = BANDS["ham70cm"]
    if _in_range(freq_hz, low, high):
        return "nfm_voice"
    return "unknown"


def listen_mode(label: str) -> str | None:
    if label == "fm_broadcast":
        return "wfm"
    if label == "airband_am":
        return "am"
    if label == "palmetto800":
        return None
    return "nfm"


def hunt_match(freq_hz: float, tolerance_hz: float = 25_000.0) -> bool:
    return any(abs(freq_hz - watch_hz) <= tolerance_hz for watch_hz in HUNT_HZ)
