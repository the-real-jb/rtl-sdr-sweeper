from bands import BANDS, CHATTER_BANDS, classify, hunt_match, listen_mode

# R820T tuner usable range (approx).
_R820T_LOW_HZ = 24e6
_R820T_HIGH_HZ = 1766e6

_EXPECTED_CHATTER_NAMES = {
    "air",
    "marine_rail",
    "ham2m",
    "ham70cm",
    "ism433",
    "gmrs",
    "murs",
}


def test_classify_known_ranges():
    assert classify(88.5e6) == "fm_broadcast"
    assert classify(121.5e6) == "airband_am"
    assert classify(146.52e6) == "nfm_voice"
    assert classify(852.35e6) == "palmetto800"
    assert classify(773.66e6) == "palmetto800"
    assert classify(433.92e6) == "unknown"
    assert classify(433.05e6) == "unknown"
    assert classify(434.79e6) == "unknown"
    assert classify(435.0e6) == "nfm_voice"


def test_listen_mode():
    assert listen_mode("fm_broadcast") == "wfm"
    assert listen_mode("airband_am") == "am"
    assert listen_mode("nfm_voice") == "nfm"
    assert listen_mode("unknown") == "nfm"
    assert listen_mode("palmetto800") is None


def test_hunt_and_default_band():
    assert hunt_match(144.390e6)
    assert hunt_match(144.390e6 + 20_000)
    assert not hunt_match(144.390e6 + 40_000)
    assert BANDS["activity"] == (118e6, 174e6)


def test_chatter_bands_nonempty_and_named():
    assert CHATTER_BANDS
    names = {name for name, _, _ in CHATTER_BANDS}
    assert _EXPECTED_CHATTER_NAMES <= names


def test_chatter_bands_ranges_sensible_within_r820t():
    by_name = {name: (start, stop) for name, start, stop in CHATTER_BANDS}
    for name, start, stop in CHATTER_BANDS:
        assert start < stop, name
        assert _R820T_LOW_HZ <= start <= _R820T_HIGH_HZ, name
        assert _R820T_LOW_HZ <= stop <= _R820T_HIGH_HZ, name

    assert by_name["air"] == (118e6, 137e6)
    assert by_name["ham2m"] == (144e6, 148e6)
    assert by_name["ham70cm"] == (420e6, 450e6)
    assert by_name["ism433"] == (433.05e6, 434.79e6)
    # Marine VHF + railroad AAR share mid-VHF; MURS / GMRS are discrete allotments.
    assert by_name["marine_rail"][0] <= 156e6
    assert by_name["marine_rail"][1] >= 162e6
    assert by_name["murs"][0] <= 151.82e6
    assert by_name["murs"][1] >= 154.60e6
    assert by_name["gmrs"][0] <= 462.55e6
    assert by_name["gmrs"][1] >= 467.725e6


def test_chatter_palmetto_id_only_not_autolisten():
    """Palmetto may appear for ID; names must mark no auto-listen."""
    palmetto = [
        (name, start, stop)
        for name, start, stop in CHATTER_BANDS
        if "palmetto" in name.lower()
    ]
    assert palmetto
    for name, start, stop in palmetto:
        assert "no_autolisten" in name
        assert start < stop
        assert classify((start + stop) / 2) == "palmetto800"
