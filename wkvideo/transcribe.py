"""Local word-level transcription with faster-whisper (no cloud API).

The Whisper model is downloaded from Hugging Face on first use and cached;
transcription itself runs entirely on this machine.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, List, Optional

DEFAULT_MODEL = "small"


@dataclass
class Word:
    text: str
    start: float
    end: float


class TranscriptionError(RuntimeError):
    pass


def transcribe_words(audio: Path, model_size: str = DEFAULT_MODEL, language: Optional[str] = None) -> List[Word]:
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise TranscriptionError(
            "captions need faster-whisper: pip install faster-whisper (or use --captions off)"
        ) from None
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, _info = model.transcribe(str(audio), word_timestamps=True, language=language)
    words = [
        Word(w.word.strip(), float(w.start), float(w.end))
        for segment in segments
        for w in (segment.words or [])
        if w.word.strip()
    ]
    if not words:
        raise TranscriptionError(f"no speech found in {audio}")
    return words


def cache_path(audio: Path) -> Path:
    audio = Path(audio)
    return audio.with_name(audio.stem + ".words.json")


def load_or_transcribe(
    audio: Path,
    model_size: str = DEFAULT_MODEL,
    transcribe: Callable[[Path, str], List[Word]] = transcribe_words,
    log: Callable[[str], None] = print,
) -> List[Word]:
    """Return word timings, reusing ``<audio>.words.json`` if it matches the file.

    The cache is keyed on the audio's size, modification time and the model
    name, so editing the MP3 or switching models triggers a new transcription.
    """
    audio = Path(audio)
    stat = audio.stat()
    key = {"audio_size": stat.st_size, "audio_mtime": int(stat.st_mtime), "model": model_size}
    cache = cache_path(audio)
    if cache.is_file():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if all(data.get(k) == v for k, v in key.items()):
                log(f"Using cached transcript {cache.name}")
                return [Word(**w) for w in data["words"]]
        except (ValueError, KeyError, TypeError):
            pass
    log(f"Transcribing {audio.name} with faster-whisper ({model_size})...")
    words = transcribe(audio, model_size)
    cache.write_text(json.dumps({**key, "words": [asdict(w) for w in words]}, indent=1), encoding="utf-8")
    return words
