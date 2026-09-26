from bands import BANDS, classify, hunt_match, listen_mode


def test_classify_known_ranges():
    assert classify(88.5e6) == "fm_broadcast"
    assert classify(121.5e6) == "airband_am"
    assert classify(146.52e6) == "nfm_voice"
    assert classify(852.35e6) == "palmetto800"
    assert classify(773.66e6) == "palmetto800"
    assert classify(433.92e6) == "unknown"


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
