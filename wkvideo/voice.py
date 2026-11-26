"""Voiceover with ElevenLabs: one clip per script line, joined into one MP3.

``wkvideo voice FOLDER`` reads ``FOLDER/script.txt`` (one line per scene),
prints how many characters it will send, generates each missing clip into
``FOLDER/voice/``, joins them into ``FOLDER/voiceover.mp3`` and writes
``FOLDER/durations.json`` (the ``{"lines": [...]}`` shape that
``--durations`` reads).

Clips are reused while their line, voice and model stay the same, so editing
one line only spends that line's characters. The key comes from
``ELEVENLABS_API_KEY`` and is never printed, logged or written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import ffmpeg
from .env import get_key, load_env

API = "https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format={fmt}"
DEFAULT_VOICE = "cgSgspJ2msm6clMCkdW9"  # Jessica
DEFAULT_MODEL = "eleven_multilingual_v2"
OUTPUT_FORMAT = "mp3_44100_128"
KEY_NAME = "ELEVENLABS_API_KEY"


class VoiceError(RuntimeError):
    pass


# (url, headers, json body) -> (audio bytes, response headers)
Post = Callable[[str, Dict[str, str], dict], Tuple[bytes, Dict[str, str]]]


def http_post(url: str, headers: Dict[str, str], body: dict, timeout: float = 120.0) -> Tuple[bytes, Dict[str, str]]:
    """POST JSON. Errors carry the status and ElevenLabs' message, never the headers."""
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={**headers, "Content-Type": "application/json", "Accept": "audio/mpeg"})
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


def clip_id(text: str, voice: str, model: str) -> str:
    return hashlib.sha256(json.dumps([text, voice, model]).encode("utf-8")).hexdigest()[:16]


@dataclass
class Plan:
    lines: List[str]
    files: List[Path]
    todo: List[int]          # indexes of lines to generate

    @property
    def characters(self) -> int:
        """Characters that will be sent (and billed)."""
        return sum(len(self.lines[i]) for i in self.todo)

    @property
    def total_characters(self) -> int:
        return sum(len(l) for l in self.lines)


def plan(lines: Sequence[str], clip_dir: Path, voice: str, model: str, force: bool = False) -> Plan:
    manifest = _read_manifest(clip_dir)
    files, todo = [], []
    for i, text in enumerate(lines):
        f = Path(clip_dir) / f"{i + 1:02d}.mp3"
        files.append(f)
        if force or not f.is_file() or manifest.get(f.name) != clip_id(text, voice, model):
            todo.append(i)
    return Plan(list(lines), files, todo)


def _read_manifest(clip_dir: Path) -> Dict[str, str]:
    try:
        return json.loads((Path(clip_dir) / "clips.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_manifest(clip_dir: Path, manifest: Dict[str, str]) -> None:
    (Path(clip_dir) / "clips.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")


def generate_clips(p: Plan, key: str, voice: str, model: str, post: Post = http_post,
                   log: Callable[[str], None] = print) -> int:
    """Generate the planned clips. Returns the characters ElevenLabs reported (or sent)."""
    if not key:
        raise VoiceError(f"{KEY_NAME} is not set (environment or .env)")
    clip_dir = p.files[0].parent
    clip_dir.mkdir(parents=True, exist_ok=True)
    manifest = _read_manifest(clip_dir)
    used = 0
    url = API.format(voice=voice, fmt=OUTPUT_FORMAT)
    for i in p.todo:
        text = p.lines[i]
        log(f"  line {i + 1}/{len(p.lines)}: {len(text)} chars")
        audio, headers = post(url, {"xi-api-key": key}, {"text": text, "model_id": model})
        if not audio:
            raise VoiceError(f"line {i + 1}: ElevenLabs returned no audio")
        tmp = p.files[i].with_suffix(".part")
        tmp.write_bytes(audio)
        tmp.replace(p.files[i])
        manifest[p.files[i].name] = clip_id(text, voice, model)
        _write_manifest(clip_dir, manifest)
        try:
            used += int(headers.get("character-cost", len(text)))
        except ValueError:
            used += len(text)
    return used


def join_command(clips: Sequence[Path], out: Path, exe: str = "ffmpeg") -> List[str]:
    """Decode every clip and encode them back to back as one MP3."""
    cmd = [exe, "-y", "-hide_banner", "-loglevel", "error"]
    for c in clips:
        cmd += ["-i", str(c)]
    streams = "".join(f"[{i}:a]" for i in range(len(clips)))
    cmd += ["-filter_complex", f"{streams}concat=n={len(clips)}:v=0:a=1[a]", "-map", "[a]",
            "-c:a", "libmp3lame", "-b:a", "192k", "-ar", "44100", str(out)]
    return cmd


def durations_json(lines: Sequence[str], durations: Sequence[float]) -> dict:
    out, start = [], 0.0
    for text, d in zip(lines, durations):
        out.append({"text": text, "startSeconds": round(start, 6), "durationSeconds": round(d, 6)})
        start += d
    return {"lines": out}


@dataclass
class VoiceResult:
    voiceover: Path
    durations: Path
    characters_sent: int
    characters_reported: int
    lines: int
    seconds: float


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
    run: Optional[Callable[[Sequence[str]], None]] = None,
) -> VoiceResult:
    folder = Path(folder)
    lines = read_script(script or folder / "script.txt")
    p = plan(lines, folder / "voice", voice, model, force)
    log(f"ElevenLabs: {p.characters} characters to generate "
        f"({len(p.todo)} of {len(lines)} lines; script total {p.total_characters} characters)")
    reported = 0
    if p.todo:
        reported = generate_clips(p, key if key is not None else get_key(KEY_NAME), voice, model, post, log)
    else:
        log("All clips are up to date; nothing sent to ElevenLabs")
    if probe is None:
        exe = ffmpeg.ffprobe_binary()
        probe = lambda f: ffmpeg.probe_duration(f, exe)  # noqa: E731
    durations = [probe(f) for f in p.files]
    out = folder / "voiceover.mp3"
    if run is None:
        run, exe = ffmpeg.run, ffmpeg.ffmpeg_binary()
    else:
        exe = "ffmpeg"
    run(join_command(p.files, out, exe))
    dur_path = folder / "durations.json"
    dur_path.write_text(json.dumps(durations_json(lines, durations), indent=1) + "\n", encoding="utf-8")
    return VoiceResult(out, dur_path, p.characters, reported, len(lines), sum(durations))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="wkvideo voice",
        description="Generate the voiceover with ElevenLabs: one clip per line of script.txt, "
                    "joined into voiceover.mp3, plus durations.json.",
    )
    p.add_argument("folder", type=Path, help="video folder")
    p.add_argument("--script", type=Path, help="script file, one line per scene (default: FOLDER/script.txt)")
    p.add_argument("--voice", default=DEFAULT_VOICE, help=f"ElevenLabs voice ID (default: {DEFAULT_VOICE}, Jessica)")
    p.add_argument("--model", default=DEFAULT_MODEL, help=f"ElevenLabs model (default: {DEFAULT_MODEL})")
    p.add_argument("--force", action="store_true", help="regenerate every clip")
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
