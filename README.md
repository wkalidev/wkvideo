# wkvideo

Turn a folder of images, an MP3 voiceover and a `scenes.json` into a vertical
**1080×1920 MP4** for Shorts, Reels and TikTok.

Each scene is rendered as:

- a blurred, darkened copy of the image filling the background
- the image fitted to 96% of the width, with its top edge at 30% of the height
- the top text in white Inter Bold with a black stroke
- an accent line in lime `#D6F048`
- a lime `{wk}` logo in the top-right corner

Every frame is upscaled to 2160×3840, then run through an ffmpeg `zoompan`
slow zoom from 1.00 to 1.04 at 30 fps (upscaling first keeps the zoom from
jittering). The clips are encoded with libx264 and concatenated, and the MP3
is muxed in as AAC.

## Requirements

- Python 3.9+
- [ffmpeg](https://ffmpeg.org/) and ffprobe on your `PATH` (built with libx264)
- The **Inter Bold** font ([rsms.me/inter](https://rsms.me/inter/)). If it can't be found,
  wkvideo falls back to Arial Bold or DejaVu Sans Bold and prints a warning.

## Install

```bash
pip install git+https://github.com/wkalidev/wkvideo.git
# or, from a clone:
pip install -e ".[test]"
```

## Usage

```
my-video/
├── scenes.json
├── voiceover.mp3
├── 01.jpg
├── 02.png
└── fonts/Inter-Bold.ttf   (optional)
```

```bash
wkvideo my-video                 # writes my-video/output.mp4
wkvideo my-video -o final.mp4 --durations my-video/durations.json
python -m wkvideo my-video       # same thing, without installing the script
```

| Option | Description |
| --- | --- |
| `-o, --output` | Output MP4. Default: `FOLDER/output.mp4` |
| `--scenes` | Scenes file. Default: `FOLDER/scenes.json` |
| `--audio` | Voiceover MP3. Default: the only `.mp3` in the folder |
| `--durations` | JSON file of per-line durations. Overrides the scene durations |
| `--font` | Path to Inter Bold. Default: see below |
| `--fps` | Frame rate. Default: 30 |
| `--keep-temp DIR` | Keep the intermediate PNG frames and per-scene clips in `DIR` |

Font lookup order: `--font`, the `WKVIDEO_FONT` environment variable, the input
folder and its `fonts/` subfolder, then the system font folders.

### scenes.json

```json
[
  {"image": "01.jpg", "duration": 3.2, "top": "Most people get this wrong", "accent": "Here's why"},
  {"image": "02.png", "duration": 2.8, "top": "Step one", "accent": "Start small"}
]
```

- `image` (required): path relative to `scenes.json`
- `duration`: seconds. Optional when `--durations` is given.
- `top` / `top_text`: white headline. Optional.
- `accent` / `accent_text`: lime line under the headline. Optional.

You can also wrap the list as `{"scenes": [...]}`. Long text wraps and shrinks
automatically to fit the space above the image.

### Per-line durations

`wkvideo.durations.read_line_durations(path)` reads a JSON file of voiceover
line timings, one per scene. These shapes are all accepted:

```json
[3.2, 2.8]
[{"text": "Most people get this wrong", "duration": 3.2}, {"text": "Step one", "duration": 2.8}]
[{"start": 0.0, "end": 3.2}, {"start": 3.2, "end": 6.0}]
{"lines": [{"startSeconds": 0.0, "durationSeconds": 3.2}, {"startSeconds": 3.2, "durationSeconds": 2.8}]}
{"lines": [3.2, 2.8]}
{"durations": [3.2, 2.8]}
```

```python
from wkvideo.durations import read_line_durations, apply_durations
from wkvideo.scenes import load_scenes

scenes = load_scenes("my-video/scenes.json")
apply_durations(scenes, read_line_durations("my-video/durations.json"))
```

The number of lines must match the number of scenes.

### Audio length

wkvideo reads the MP3 length with `ffprobe` and extends the last scene so the
video ends 0.6s after the voiceover. Audio is never cut. If the scenes already
run past that point, they are left as they are and the end plays silently.

## Development

```bash
pip install -e ".[test]"
pytest
```

The end-to-end test renders a short video and checks it with `ffprobe`. It is
skipped when ffmpeg isn't installed.

## License

[MIT](LICENSE) © wkalidev
