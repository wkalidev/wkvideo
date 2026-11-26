"""Voiceover with ElevenLabs: the whole script in one request, timed per line.

``wkvideo voice FOLDER`` reads ``FOLDER/script.txt`` (one line per scene),
prints how many characters it will send, and generates the whole script as
one continuous narration with ElevenLabs' ``with-timestamps`` endpoint. The
audio goes to ``FOLDER/voiceover.mp3``; the character alignment gives each
line's start and duration, written to ``FOLDER/durations.json`` (the
``{"lines": [...]}`` shape that ``--durations`` reads).

The narration is reused while the script, voice and model stay the same
(``voice/narration.json`` records what was generated). The key comes from
``ELEVENLABS_API_KEY`` and is never printed, logged or written.
"""

from __future__ import annotations

import argparse
import base64
import difflib
import hashlib
import json
import shutil
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import ffmpeg
from .env import get_key, load_env

API = "https://api.elevenlabs.io/v1/text-to-speech/{voice}/with-timestamps?output_format={fmt}"
DEFAULT_VOICE = "cgSgspJ2msm6clMCkdW9"  # Jessica
DEFAULT_MODEL = "eleven_multilingual_v2"
OUTPUT_FORMAT = "mp3_44100_128"
KEY_NAME = "ELEVENLABS_API_KEY"
MAX_CHARACTERS = 10_000      # per request for eleven_multilingual_v2
LINE_SEPARATOR = " "         # lines are read as one paragraph, not as separate takes


class VoiceError(RuntimeError):
    pass


# (url, headers, json body) -> (response body, response headers)
Post = Callable[[str, Dict[str, str], dict], Tuple[bytes, Dict[str, str]]]


def http_post(url: str, headers: Dict[str, str], body: dict, timeout: float = 180.0) -> Tuple[bytes, Dict[str, str]]:
    """POST JSON. Errors carry the status and ElevenLabs' message, never the headers."""
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={**headers, "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read(), {k.lower(): v for k, v in resp.headers.items()}
    except urllib.error.HTTPError as exc:
        raw = exc.read(2000).decode("utf-8", "replace")
        try:
            detail = json.loads(raw).get("detail", raw)
            if isinstance(detail, dict):
                detail = detail.get("message") or detail.get("status") or json.dumps(detail)
        except (ValueError, AttributeError):
            detail = raw
        raise VoiceError(f"ElevenLabs HTTP {exc.code}: {str(detail).strip()[:300]}") from None
    except (urllib.error.URLError, OSError) as exc:
        raise VoiceError(f"ElevenLabs: {getattr(exc, 'reason', exc)}") from None


def read_script(path: Path) -> List[str]:
    """Non-empty lines of the script; lines starting with ``#`` are comments."""
    path = Path(path)
    if not path.is_file():
        raise VoiceError(f"script not found: {path}")
    lines = [l.strip() for l in path.read_text(encoding="utf-8-sig").splitlines()]
    lines = [l for l in lines if l and not l.startswith("#")]
    if not lines:
        raise VoiceError(f"{path} has no lines")
    return lines


def script_text(lines: Sequence[str]) -> str:
    """The text sent to ElevenLabs: every line, in one paragraph."""
    return LINE_SEPARATOR.join(lines)


def line_spans(lines: Sequence[str]) -> List[Tuple[int, int]]:
    """``(start, end)`` character offsets of each line inside :func:`script_text`."""
    spans, pos = [], 0
    for line in lines:
        spans.append((pos, pos + len(line)))
        pos += len(line) + len(LINE_SEPARATOR)
    return spans


def request_id(text: str, voice: str, model: str) -> str:
    return hashlib.sha256(json.dumps([text, voice, model]).encode("utf-8")).hexdigest()[:16]


def _char_times(text: str, alignment: dict) -> Tuple[List[Optional[float]], List[Optional[float]]]:
    """Start and end time of every character of ``text``, from ElevenLabs' alignment.

    The alignment normally lists the characters of ``text`` one for one; if it
    doesn't, characters are matched with difflib and the unmatched ones get None.
    """
    try:
        chars = list(alignment["characters"])
        starts = [float(t) for t in alignment["character_start_times_seconds"]]
        ends = [float(t) for t in alignment["character_end_times_seconds"]]
    except (KeyError, TypeError, ValueError):
        raise VoiceError("ElevenLabs returned no character alignment") from None
    if not chars or not (len(chars) == len(starts) == len(ends)):
        raise VoiceError("ElevenLabs returned an incomplete character alignment")
    if len(chars) == len(text) and "".join(chars) == text:
        return list(starts), list(ends)
    # An entry of ``chars`` may hold more than one code point: match on a flat index.
    flat, owner = [], []
    for k, c in enumerate(chars):
        for ch in c:
            flat.append(ch)
            owner.append(k)
    out_s: List[Optional[float]] = [None] * len(text)
    out_e: List[Optional[float]] = [None] * len(text)
    for block in difflib.SequenceMatcher(None, text, "".join(flat), autojunk=False).get_matching_blocks():
        for d in range(block.size):
            k = owner[block.b + d]
            out_s[block.a + d], out_e[block.a + d] = starts[k], ends[k]
    return out_s, out_e


def line_timings(lines: Sequence[str], alignment: dict, total: float) -> List[Tuple[float, float]]:
    """``(start, duration)`` of every line, from the character alignment.

    Each line is spoken from its first to its last non-space character. The
    cut between two lines sits halfway through the pause between them, so a
    scene never starts after its first word or ends before its last one. The
    first line starts at 0 and the last one runs to ``total`` (the audio
    length), so the durations add up to the whole voiceover.
    """
    text = script_text(lines)
    starts, ends = _char_times(text, alignment)
    spoken = []
    for i, (a, b) in enumerate(line_spans(lines)):
        s = [starts[k] for k in range(a, b) if starts[k] is not None and not text[k].isspace()]
        e = [ends[k] for k in range(a, b) if ends[k] is not None and not text[k].isspace()]
        if not s or not e:
            raise VoiceError(f"line {i + 1} is missing from ElevenLabs' alignment")
        spoken.append((s[0], e[-1]))
    cuts = [0.0]
    for (_, end_prev), (start_next, _) in zip(spoken, spoken[1:]):
        cut = (end_prev + start_next) / 2 if start_next > end_prev else start_next
        cuts.append(max(cut, cuts[-1]))
    cuts.append(max(total, spoken[-1][1], cuts[-1]))
    timings = [(cuts[i], cuts[i + 1] - cuts[i]) for i in range(len(lines))]
    for i, (_, d) in enumerate(timings):
        if d <= 0:
            raise VoiceError(f"line {i + 1} got no time in ElevenLabs' alignment")
    return timings


def durations_json(lines: Sequence[str], timings: Sequence[Tuple[float, float]]) -> dict:
    return {"lines": [{"text": text, "startSeconds": round(s, 6), "durationSeconds": round(d, 6)}
                      for text, (s, d) in zip(lines, timings)]}


def generate(text: str, key: str, voice: str, model: str, post: Post = http_post) -> Tuple[bytes, dict, int]:
    """One with-timestamps request. Returns (MP3 bytes, alignment, characters reported)."""
    if not key:
        raise VoiceError(f"{KEY_NAME} is not set (environment or .env)")
    body, headers = post(API.format(voice=voice, fmt=OUTPUT_FORMAT), {"xi-api-key": key},
                         {"text": text, "model_id": model})
    try:
        data = json.loads(body)
        audio = base64.b64decode(data["audio_base64"])
    except (ValueError, KeyError, TypeError):
        raise VoiceError("ElevenLabs returned no audio") from None
    if not audio:
        raise VoiceError("ElevenLabs returned no audio")
    alignment = data.get("alignment") or data.get("normalized_alignment")
    if not alignment:
        raise VoiceError("ElevenLabs returned no character alignment")
    try:
        used = int(headers.get("character-cost", len(text)))
    except ValueError:
        used = len(text)
    return audio, alignment, used


@dataclass
class VoiceResult:
    voiceover: Path
    durations: Path
    characters_sent: int
    characters_reported: int
    lines: int
    seconds: float


def _read_json(path: Path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def make_voiceover(
    folder: Path,
    script: Optional[Path] = None,
    voice: str = DEFAULT_VOICE,
    model: str = DEFAULT_MODEL,
    force: bool = False,
    key: Optional[str] = None,
    post: Post = http_post,
    log: Callable[[str], None] = print,
    probe: Optional[Callable[[Path], float]] = None,
) -> VoiceResult:
    folder = Path(folder)
    lines = read_script(script or folder / "script.txt")
    text = script_text(lines)
    voice_dir = folder / "voice"
    audio_path, meta_path = voice_dir / "narration.mp3", voice_dir / "narration.json"
    rid = request_id(text, voice, model)
    meta = _read_json(meta_path)
    fresh = not force and audio_path.is_file() and meta.get("id") == rid and meta.get("alignment")

    sent = reported = 0
    if fresh:
        log(f"ElevenLabs: 0 characters to generate (script unchanged, {len(text)} characters); "
            "nothing sent to ElevenLabs")
        alignment = meta["alignment"]
    else:
        if len(text) > MAX_CHARACTERS:
            raise VoiceError(f"script is {len(text)} characters; one request takes at most {MAX_CHARACTERS}")
        log(f"ElevenLabs: {len(text)} characters to generate in 1 request ({len(lines)} lines)")
        audio, alignment, reported = generate(text, key if key is not None else get_key(KEY_NAME),
                                              voice, model, post)
        sent = len(text)
        voice_dir.mkdir(parents=True, exist_ok=True)
        tmp = audio_path.with_suffix(".part")
        tmp.write_bytes(audio)
        tmp.replace(audio_path)
        meta_path.write_text(json.dumps({"id": rid, "voice": voice, "model": model, "text": text,
                                         "alignment": alignment}) + "\n", encoding="utf-8")

    out = folder / "voiceover.mp3"
    shutil.copyfile(audio_path, out)
    if probe is None:
        exe = ffmpeg.ffprobe_binary()
        probe = lambda f: ffmpeg.probe_duration(f, exe)  # noqa: E731
    total = probe(out)
    timings = line_timings(lines, alignment, total)
    dur_path = folder / "durations.json"
    dur_path.write_text(json.dumps(durations_json(lines, timings), indent=1) + "\n", encoding="utf-8")
    return VoiceResult(out, dur_path, sent, reported, len(lines), total)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="wkvideo voice",
        description="Generate the voiceover with ElevenLabs: script.txt read as one narration "
                    "into voiceover.mp3, plus durations.json with each line's timing.",
    )
    p.add_argument("folder", type=Path, help="video folder")
    p.add_argument("--script", type=Path, help="script file, one line per scene (default: FOLDER/script.txt)")
    p.add_argument("--voice", default=DEFAULT_VOICE, help=f"ElevenLabs voice ID (default: {DEFAULT_VOICE}, Jessica)")
    p.add_argument("--model", default=DEFAULT_MODEL, help=f"ElevenLabs model (default: {DEFAULT_MODEL})")
    p.add_argument("--force", action="store_true", help="generate again even if the script is unchanged")
    p.add_argument("--env-file", type=Path, help=f"file with {KEY_NAME} (default: the nearest .env)")
    return p


def main(argv: Optional[List[str]] = None, post: Post = http_post) -> int:
    args = build_parser().parse_args(argv)
    if not args.folder.is_dir():
        print(f"error: not a folder: {args.folder}", file=sys.stderr)
        return 2
    load_env([args.folder], args.env_file)
    try:
        r = make_voiceover(args.folder, args.script, args.voice, args.model, args.force, post=post)
    except (VoiceError, ffmpeg.FFmpegError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Wrote {r.voiceover} ({r.seconds:.2f}s, {r.lines} lines) and {r.durations}")
    print(f"ElevenLabs characters used: {r.characters_reported}")
    return 0
