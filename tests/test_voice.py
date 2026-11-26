"""wkvideo voice: ElevenLabs mocked, ffprobe mocked except in the end-to-end test."""

import base64
import io
import json
import subprocess
import urllib.error

import pytest

from wkvideo import voice
from wkvideo.durations import read_line_durations
from wkvideo.voice import (
    DEFAULT_MODEL, DEFAULT_VOICE, VoiceError, durations_json, line_timings, make_voiceover, read_script,
    script_text,
)

from conftest import requires_ffmpeg

KEY = "el-secret-789"
LINES = ["whatsapp just patched a bug.", "update the app today.", "when did you last update it?"]
CHAR = 0.05      # seconds per character in the fake alignment
PAUSE = 0.4      # extra silence the fake puts after each separator


def fake_alignment(text, pause_after_space=None):
    """One entry per character; separators between lines get PAUSE of extra silence."""
    pause_after_space = pause_after_space or set()
    chars, starts, ends, t = [], [], [], 0.0
    for i, c in enumerate(text):
        d = CHAR + (PAUSE if i in pause_after_space else 0.0)
        chars.append(c)
        starts.append(round(t, 4))
        ends.append(round(t + d, 4))
        t += d
    return {"characters": chars, "character_start_times_seconds": starts, "character_end_times_seconds": ends}


def separators(lines):
    pos, out = 0, set()
    for line in lines[:-1]:
        pos += len(line)
        out.add(pos)
        pos += 1
    return out


class FakeElevenLabs:
    def __init__(self, events=None, audio=b"ID3fake-mp3", headers=None, alignment=None):
        self.calls = []
        self.events = events if events is not None else []
        self.audio = audio
        self.headers = headers
        self.alignment = alignment

    def __call__(self, url, headers, body):
        self.calls.append((url, dict(headers), dict(body)))
        self.events.append(("post", body["text"]))
        text = body["text"]
        audio = self.audio(text) if callable(self.audio) else self.audio
        if callable(self.alignment):
            alignment = self.alignment(text)
        else:
            alignment = self.alignment or fake_alignment(text, separators(LINES) if text == script_text(LINES) else None)
        payload = {"audio_base64": base64.b64encode(audio).decode(), "alignment": alignment,
                   "normalized_alignment": alignment}
        hdrs = self.headers if self.headers is not None else {"character-cost": str(len(text))}
        return json.dumps(payload).encode(), hdrs


def folder_with_script(tmp_path, lines=LINES):
    (tmp_path / "script.txt").write_text("# comment\n" + "\n".join(lines) + "\n\n", encoding="utf-8")
    return tmp_path


def audio_length(lines=LINES):
    text = script_text(lines)
    return len(text) * CHAR + PAUSE * (len(lines) - 1) + 0.3     # 0.3 s of trailing silence


def run_voice(folder, fake, events=None, **kw):
    events = events if events is not None else fake.events
    probe = kw.pop("probe", lambda f: audio_length())
    return make_voiceover(folder, key=kw.pop("key", KEY), post=fake, log=lambda m: events.append(("log", m)),
                          probe=probe, **kw)


def test_read_script_skips_blank_lines_and_comments(tmp_path):
    assert read_script(folder_with_script(tmp_path) / "script.txt") == LINES


def test_empty_script_is_an_error(tmp_path):
    (tmp_path / "script.txt").write_text("# nothing\n\n", encoding="utf-8")
    with pytest.raises(VoiceError, match="no lines"):
        read_script(tmp_path / "script.txt")


def test_character_count_is_printed_before_anything_is_sent(tmp_path):
    fake = FakeElevenLabs()
    run_voice(folder_with_script(tmp_path), fake)
    first_post = next(i for i, e in enumerate(fake.events) if e[0] == "post")
    logs_before = [e[1] for e in fake.events[:first_post] if e[0] == "log"]
    assert any(f"{len(script_text(LINES))} characters to generate in 1 request" in m for m in logs_before)


def test_the_whole_script_goes_in_one_request_with_timestamps(tmp_path):
    fake = FakeElevenLabs()
    run_voice(folder_with_script(tmp_path), fake)
    assert len(fake.calls) == 1
    url, headers, body = fake.calls[0]
    assert body["text"] == " ".join(LINES)
    assert f"/v1/text-to-speech/{DEFAULT_VOICE}/with-timestamps?" in url and KEY not in url
    assert headers == {"xi-api-key": KEY}
    assert body["model_id"] == DEFAULT_MODEL == "eleven_multilingual_v2"
    assert DEFAULT_VOICE == "cgSgspJ2msm6clMCkdW9"


def test_audio_is_written_to_voiceover_mp3(tmp_path):
    result = run_voice(folder_with_script(tmp_path), FakeElevenLabs(audio=b"ID3narration"))
    assert result.voiceover.read_bytes() == b"ID3narration"
    assert (tmp_path / "voice" / "narration.mp3").read_bytes() == b"ID3narration"


def test_line_timings_come_from_the_alignment():
    text = script_text(LINES)
    timings = line_timings(LINES, fake_alignment(text, separators(LINES)), audio_length())
    # Line 2 starts after line 1's 28 characters, the separator and its pause:
    # the cut sits halfway through that gap.
    end1 = len(LINES[0]) * CHAR
    start2 = end1 + CHAR + PAUSE
    assert timings[0] == pytest.approx((0.0, (end1 + start2) / 2))
    assert timings[1][0] == pytest.approx((end1 + start2) / 2)
    assert sum(d for _, d in timings) == pytest.approx(audio_length())      # last line runs to the end
    assert all(d > 0 for _, d in timings)


def test_durations_json_is_readable_by_wkvideo(tmp_path):
    result = run_voice(folder_with_script(tmp_path), FakeElevenLabs())
    durations = read_line_durations(result.durations)
    assert len(durations) == 3 and sum(durations) == pytest.approx(audio_length())
    data = json.loads(result.durations.read_text(encoding="utf-8"))
    assert [l["text"] for l in data["lines"]] == LINES
    starts = [l["startSeconds"] for l in data["lines"]]
    assert starts[0] == 0 and starts == sorted(starts)
    assert starts[1] == pytest.approx(starts[0] + durations[0])
    assert result.seconds == pytest.approx(audio_length())


def test_alignment_that_does_not_match_the_text_one_to_one_still_works():
    text = script_text(LINES)
    al = fake_alignment(text, separators(LINES))
    # Merge two characters into one entry (as can happen with some scripts).
    al["characters"][1:3] = [al["characters"][1] + al["characters"][2]]
    al["character_start_times_seconds"][1:3] = [al["character_start_times_seconds"][1]]
    al["character_end_times_seconds"][1:3] = [al["character_end_times_seconds"][2]]
    timings = line_timings(LINES, al, audio_length())
    expected = line_timings(LINES, fake_alignment(text, separators(LINES)), audio_length())
    assert [x for t in timings for x in t] == pytest.approx([x for t in expected for x in t])


def test_missing_alignment_is_an_error(tmp_path):
    with pytest.raises(VoiceError, match="alignment"):
        run_voice(folder_with_script(tmp_path), FakeElevenLabs(alignment=lambda t: {}))


def test_unchanged_script_is_not_sent_again(tmp_path):
    folder = folder_with_script(tmp_path)
    first = run_voice(folder, FakeElevenLabs())
    first_durations = first.durations.read_text(encoding="utf-8")
    fake = FakeElevenLabs()
    result = run_voice(folder, fake)
    assert fake.calls == [] and result.characters_sent == 0
    assert any("nothing sent" in e[1] for e in fake.events if e[0] == "log")
    assert result.durations.read_text(encoding="utf-8") == first_durations


def test_editing_a_line_generates_the_whole_narration_again(tmp_path):
    folder = folder_with_script(tmp_path)
    run_voice(folder, FakeElevenLabs())
    edited = [LINES[0], "update whatsapp today.", LINES[2]]
    folder_with_script(tmp_path, edited)
    fake = FakeElevenLabs(alignment=lambda t: fake_alignment(t, separators(edited)))
    result = run_voice(folder, fake, probe=lambda f: audio_length(edited))
    assert [c[2]["text"] for c in fake.calls] == [" ".join(edited)]
    assert result.characters_sent == len(" ".join(edited))


def test_force_and_a_new_voice_generate_again(tmp_path):
    folder = folder_with_script(tmp_path)
    run_voice(folder, FakeElevenLabs())
    fake = FakeElevenLabs()
    run_voice(folder, fake, force=True)
    run_voice(folder, fake, voice="other-voice")
    assert len(fake.calls) == 2 and "/other-voice/with-timestamps?" in fake.calls[1][0]


def test_reported_characters_come_from_elevenlabs_when_given(tmp_path):
    result = run_voice(folder_with_script(tmp_path), FakeElevenLabs(headers={"character-cost": "7"}))
    assert result.characters_reported == 7


def test_reported_characters_fall_back_to_the_sent_count(tmp_path):
    result = run_voice(folder_with_script(tmp_path), FakeElevenLabs(headers={}))
    assert result.characters_reported == result.characters_sent == len(script_text(LINES))


def test_missing_key_is_an_error_before_any_request(tmp_path):
    fake = FakeElevenLabs()
    with pytest.raises(VoiceError, match="ELEVENLABS_API_KEY"):
        run_voice(folder_with_script(tmp_path), fake, key="")
    assert fake.calls == []


def test_too_long_a_script_is_refused_before_any_request(tmp_path, monkeypatch):
    monkeypatch.setattr(voice, "MAX_CHARACTERS", 20)
    fake = FakeElevenLabs()
    with pytest.raises(VoiceError, match="at most 20"):
        run_voice(folder_with_script(tmp_path), fake)
    assert fake.calls == []


def test_http_error_message_hides_the_key(monkeypatch):
    def fail(req, timeout=None):
        assert req.get_header("Xi-api-key") == KEY
        body = json.dumps({"detail": {"status": "quota_exceeded", "message": "This request exceeds your quota."}})
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(body.encode()))

    monkeypatch.setattr(voice.urllib.request, "urlopen", fail)
    with pytest.raises(VoiceError) as err:
        voice.http_post("https://api.elevenlabs.io/v1/text-to-speech/x", {"xi-api-key": KEY}, {"text": "hi"})
    assert "HTTP 401" in str(err.value) and "exceeds your quota" in str(err.value)
    assert KEY not in str(err.value)


def test_durations_json_shape():
    assert durations_json(["a", "b"], [(0.0, 1.25), (1.25, 2.0)]) == {"lines": [
        {"text": "a", "startSeconds": 0.0, "durationSeconds": 1.25},
        {"text": "b", "startSeconds": 1.25, "durationSeconds": 2.0},
    ]}


def test_cli_prints_characters_and_never_the_key(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "")
    monkeypatch.chdir(tmp_path)
    folder = folder_with_script(tmp_path)
    (folder / ".env").write_text(f"ELEVENLABS_API_KEY={KEY}\n", encoding="utf-8")
    monkeypatch.setattr(voice.ffmpeg, "ffprobe_binary", lambda: "ffprobe")
    monkeypatch.setattr(voice.ffmpeg, "probe_duration", lambda f, exe="ffprobe": audio_length())
    fake = FakeElevenLabs()
    assert voice.main([str(folder)], post=fake) == 0
    out = capsys.readouterr()
    total = len(script_text(LINES))
    assert f"{total} characters to generate" in out.out
    assert f"ElevenLabs characters used: {total}" in out.out
    assert KEY not in out.out + out.err
    for f in folder.rglob("*"):
        if f.is_file() and f.name != ".env":
            assert KEY.encode() not in f.read_bytes()


@requires_ffmpeg
def test_end_to_end_with_a_real_mp3(tmp_path):
    def tone(text):
        out = tmp_path / "tone.mp3"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                        f"sine=frequency=440:duration={audio_length()}", "-c:a", "libmp3lame", str(out)], check=True)
        return out.read_bytes()

    folder = folder_with_script(tmp_path)
    result = make_voiceover(folder, key=KEY, post=FakeElevenLabs(audio=tone), log=lambda m: None)
    from wkvideo.ffmpeg import probe_duration

    lines = read_line_durations(result.durations)
    assert sum(lines) == pytest.approx(probe_duration(result.voiceover), abs=0.01)
    assert probe_duration(result.voiceover) == pytest.approx(audio_length(), abs=0.1)


def test_default_voice_settings_are_sent(tmp_path):
    fake = FakeElevenLabs()
    run_voice(folder_with_script(tmp_path), fake)
    assert fake.calls[0][2]["voice_settings"] == {
        "stability": 0.45, "similarity_boost": 0.8, "style": 0.15, "use_speaker_boost": True,
    }


def test_voice_settings_are_configurable_and_change_the_cache_key(tmp_path):
    folder = folder_with_script(tmp_path)
    run_voice(folder, FakeElevenLabs())
    fake = FakeElevenLabs()
    run_voice(folder, fake, settings={"stability": 0.3, "use_speaker_boost": False})
    assert fake.calls[0][2]["voice_settings"] == {
        "stability": 0.3, "similarity_boost": 0.8, "style": 0.15, "use_speaker_boost": False,
    }
    again = FakeElevenLabs()
    run_voice(folder, again, settings={"stability": 0.3, "use_speaker_boost": False})
    assert again.calls == []


def test_voice_settings_out_of_range_are_refused():
    with pytest.raises(VoiceError, match="style must be between 0 and 1"):
        voice.voice_settings(style=1.5)


def test_cli_voice_settings_flags(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(voice.ffmpeg, "ffprobe_binary", lambda: "ffprobe")
    monkeypatch.setattr(voice.ffmpeg, "probe_duration", lambda f, exe="ffprobe": audio_length())
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    fake = FakeElevenLabs()
    folder = folder_with_script(tmp_path)
    assert voice.main([str(folder), "--stability", "0.6", "--similarity-boost", "0.7", "--style", "0",
                       "--no-speaker-boost"], post=fake) == 0
    assert fake.calls[0][2]["voice_settings"] == {
        "stability": 0.6, "similarity_boost": 0.7, "style": 0.0, "use_speaker_boost": False,
    }
    assert "speaker boost off" in capsys.readouterr().out
