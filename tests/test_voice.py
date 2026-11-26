"""wkvideo voice: ElevenLabs mocked, ffmpeg mocked except in the end-to-end test."""

import io
import json
import subprocess
import urllib.error

import pytest

from wkvideo import voice
from wkvideo.durations import read_line_durations
from wkvideo.voice import (
    DEFAULT_MODEL, DEFAULT_VOICE, VoiceError, durations_json, join_command, make_voiceover, read_script,
)

from conftest import requires_ffmpeg

KEY = "el-secret-789"
LINES = ["whatsapp just patched a bug.", "update the app today.", "when did you last update it?"]


class FakeElevenLabs:
    def __init__(self, events=None, audio=b"ID3fake-mp3", headers=None):
        self.calls = []
        self.events = events if events is not None else []
        self.audio = audio
        self.headers = headers

    def __call__(self, url, headers, body):
        self.calls.append((url, dict(headers), dict(body)))
        self.events.append(("post", body["text"]))
        audio = self.audio(body["text"]) if callable(self.audio) else self.audio
        return audio, (self.headers if self.headers is not None else {"character-cost": str(len(body["text"]))})


def folder_with_script(tmp_path, lines=LINES):
    (tmp_path / "script.txt").write_text("# comment\n" + "\n".join(lines) + "\n\n", encoding="utf-8")
    return tmp_path


def run_voice(folder, fake, events=None, **kw):
    events = events if events is not None else fake.events
    commands = []
    probe = kw.pop("probe", lambda f: 1.5 + int(f.stem) / 10)
    result = make_voiceover(folder, key=kw.pop("key", KEY), post=fake, log=lambda m: events.append(("log", m)),
                            probe=probe, run=commands.append, **kw)
    return result, commands


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
    total = sum(len(l) for l in LINES)
    assert any(f"{total} characters to generate" in m for m in logs_before)


def test_one_request_per_line_with_jessica_and_the_key_in_a_header(tmp_path):
    fake = FakeElevenLabs()
    run_voice(folder_with_script(tmp_path), fake)
    assert [c[2]["text"] for c in fake.calls] == LINES
    for url, headers, body in fake.calls:
        assert f"/v1/text-to-speech/{DEFAULT_VOICE}" in url and KEY not in url
        assert headers == {"xi-api-key": KEY}
        assert body["model_id"] == DEFAULT_MODEL
    assert DEFAULT_VOICE == "cgSgspJ2msm6clMCkdW9"


def test_clips_are_joined_into_voiceover_mp3(tmp_path):
    _, commands = run_voice(folder_with_script(tmp_path), FakeElevenLabs())
    cmd = commands[0]
    inputs = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-i"]
    assert [p.replace("\\", "/").split("/")[-1] for p in inputs] == ["01.mp3", "02.mp3", "03.mp3"]
    assert "concat=n=3:v=0:a=1[a]" in " ".join(cmd) and cmd[-1].endswith("voiceover.mp3")


def test_durations_json_is_readable_by_wkvideo(tmp_path):
    result, _ = run_voice(folder_with_script(tmp_path), FakeElevenLabs())
    assert read_line_durations(result.durations) == pytest.approx([1.6, 1.7, 1.8])
    data = json.loads(result.durations.read_text(encoding="utf-8"))
    assert [l["text"] for l in data["lines"]] == LINES
    assert [l["startSeconds"] for l in data["lines"]] == pytest.approx([0, 1.6, 3.3])
    assert result.seconds == pytest.approx(5.1)


def test_unchanged_lines_are_not_sent_again(tmp_path):
    folder = folder_with_script(tmp_path)
    run_voice(folder, FakeElevenLabs())
    fake = FakeElevenLabs()
    result, _ = run_voice(folder, fake)
    assert fake.calls == [] and result.characters_sent == 0
    assert any("nothing sent" in e[1] for e in fake.events if e[0] == "log")


def test_editing_one_line_only_regenerates_that_line(tmp_path):
    folder = folder_with_script(tmp_path)
    run_voice(folder, FakeElevenLabs())
    edited = [LINES[0], "update whatsapp today.", LINES[2]]
    folder_with_script(tmp_path, edited)
    fake = FakeElevenLabs()
    result, _ = run_voice(folder, fake)
    assert [c[2]["text"] for c in fake.calls] == ["update whatsapp today."]
    assert result.characters_sent == len("update whatsapp today.")


def test_changing_the_voice_regenerates_everything(tmp_path):
    folder = folder_with_script(tmp_path)
    run_voice(folder, FakeElevenLabs())
    fake = FakeElevenLabs()
    run_voice(folder, fake, voice="other-voice")
    assert len(fake.calls) == 3 and "/other-voice?" in fake.calls[0][0]


def test_reported_characters_come_from_elevenlabs_when_given(tmp_path):
    fake = FakeElevenLabs(headers={"character-cost": "7"})
    result, _ = run_voice(folder_with_script(tmp_path), fake)
    assert result.characters_reported == 21


def test_reported_characters_fall_back_to_the_sent_count(tmp_path):
    result, _ = run_voice(folder_with_script(tmp_path), FakeElevenLabs(headers={}))
    assert result.characters_reported == result.characters_sent == sum(len(l) for l in LINES)


def test_missing_key_is_an_error_before_any_request(tmp_path):
    fake = FakeElevenLabs()
    with pytest.raises(VoiceError, match="ELEVENLABS_API_KEY"):
        run_voice(folder_with_script(tmp_path), fake, key="")
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
    assert durations_json(["a", "b"], [1.25, 2.0]) == {"lines": [
        {"text": "a", "startSeconds": 0.0, "durationSeconds": 1.25},
        {"text": "b", "startSeconds": 1.25, "durationSeconds": 2.0},
    ]}


def test_join_command_reencodes_once():
    cmd = join_command(["a.mp3", "b.mp3"], "out.mp3")
    assert cmd[cmd.index("-c:a") + 1] == "libmp3lame" and cmd[-1] == "out.mp3"


def test_cli_prints_characters_and_never_the_key(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "")
    monkeypatch.chdir(tmp_path)
    folder = folder_with_script(tmp_path)
    (folder / ".env").write_text(f"ELEVENLABS_API_KEY={KEY}\n", encoding="utf-8")
    monkeypatch.setattr(voice.ffmpeg, "run", lambda cmd: None)
    monkeypatch.setattr(voice.ffmpeg, "ffmpeg_binary", lambda: "ffmpeg")
    monkeypatch.setattr(voice.ffmpeg, "ffprobe_binary", lambda: "ffprobe")
    monkeypatch.setattr(voice.ffmpeg, "probe_duration", lambda f, exe="ffprobe": 2.0)
    fake = FakeElevenLabs()
    assert voice.main([str(folder)], post=fake) == 0
    out = capsys.readouterr()
    total = sum(len(l) for l in LINES)
    assert f"{total} characters to generate" in out.out
    assert f"ElevenLabs characters used: {total}" in out.out
    assert KEY not in out.out + out.err
    for f in folder.rglob("*"):
        if f.is_file() and f.name != ".env":
            assert KEY.encode() not in f.read_bytes()


@requires_ffmpeg
def test_end_to_end_with_real_mp3_clips(tmp_path):
    def tone(text):
        out = tmp_path / "tone.mp3"
        seconds = len(text) / 20
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                        f"sine=frequency=440:duration={seconds}", "-c:a", "libmp3lame", str(out)], check=True)
        return out.read_bytes()

    folder = folder_with_script(tmp_path)
    result = make_voiceover(folder, key=KEY, post=FakeElevenLabs(audio=tone), log=lambda m: None)
    from wkvideo.ffmpeg import probe_duration

    lines = read_line_durations(result.durations)
    assert lines == pytest.approx([len(l) / 20 for l in LINES], abs=0.08)
    assert probe_duration(result.voiceover) == pytest.approx(sum(lines), abs=0.15)
