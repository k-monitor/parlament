"""Per-speech clip URL derivation (VIE-9)."""

from app.media import per_speech_clip

DAY_URI = ("https://sgis.parlament.hu:446/vod/"
           "smil:20260509.092628.2143172.24318900.smil/playlist.m3u8")
DAY_PLAYSEQ = ("https://sgis.parlament.hu/archive/playseq.php?"
               "date1=20260509&time1=092628&offset1=003543.172"
               "&date2=20260509&time2=092628&offset2=064518.9&type=real")


def test_clip_shifts_smil_offsets_by_speech_window():
    # Speech 43001-2: day-relative [344.029, 491.962]; day starts at 2143172 ms.
    clip = per_speech_clip(DAY_URI, DAY_PLAYSEQ, 344.029, 491.962)
    assert clip["video_uri"] == (
        "https://sgis.parlament.hu:446/vod/"
        "smil:20260509.092628.2487201.2635134.smil/playlist.m3u8"
    )
    # The activation URL keeps date/time/type, only the offsets move.
    assert "offset1=004127.201" in clip["video_playseq"]
    assert "offset2=004355.134" in clip["video_playseq"]
    assert "type=real" in clip["video_playseq"]
    assert "date1=20260509" in clip["video_playseq"]


def test_zero_based_speech_maps_to_day_start():
    clip = per_speech_clip(DAY_URI, DAY_PLAYSEQ, 0.0, 100.0)
    assert "smil:20260509.092628.2143172.2243172.smil" in clip["video_uri"]


def test_missing_or_degenerate_inputs_return_none():
    assert per_speech_clip(None, DAY_PLAYSEQ, 1.0, 2.0) is None
    assert per_speech_clip(DAY_URI, DAY_PLAYSEQ, None, 2.0) is None
    assert per_speech_clip(DAY_URI, DAY_PLAYSEQ, 5.0, 5.0) is None      # zero-length
    assert per_speech_clip(DAY_URI, DAY_PLAYSEQ, 9.0, 2.0) is None      # inverted


def test_unrecognised_day_uri_returns_none():
    assert per_speech_clip("https://example.com/whatever.m3u8",
                           DAY_PLAYSEQ, 1.0, 2.0) is None


def test_clip_without_playseq_still_yields_video_uri():
    clip = per_speech_clip(DAY_URI, None, 344.029, 491.962)
    assert clip["video_playseq"] is None
    assert "2487201.2635134" in clip["video_uri"]
