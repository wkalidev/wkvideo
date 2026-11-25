"""Build docs/demo.webp: the first seconds of a real wkvideo render.

    python docs/demo.py ~/fake-recruiters/test-fake-recruiters

Renders the folder with wkvideo (captions on), then turns the opening into a
looping animated WebP with rounded corners (alpha), sized for a README.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

DOCS = Path(__file__).resolve().parent
SECONDS = 4.0
WIDTH, HEIGHT = 432, 768     # shown at 270px wide in the README; extra pixels keep it sharp on retina
FPS = 24
RADIUS = 30
QUALITY = 72
MAX_BYTES = 3 * 1024 * 1024


def corner_mask(path: Path) -> None:
    scale = 4  # draw large and downsample for smooth anti-aliased corners
    big = Image.new("L", (WIDTH * scale, HEIGHT * scale), 0)
    ImageDraw.Draw(big).rounded_rectangle(
        (0, 0, WIDTH * scale - 1, HEIGHT * scale - 1), radius=RADIUS * scale, fill=255
    )
    big.resize((WIDTH, HEIGHT), Image.LANCZOS).save(path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("folder", type=Path)
    ap.add_argument("-o", "--output", type=Path, default=DOCS / "demo.webp")
    args = ap.parse_args()
    folder = args.folder.expanduser()

    with tempfile.TemporaryDirectory(prefix="wkvideo_demo_") as tmp:
        tmp = Path(tmp)
        video = tmp / "demo.mp4"
        cmd = [sys.executable, "-m", "wkvideo", str(folder), "-o", str(video)]
        if (folder / "durations.json").is_file():
            cmd += ["--durations", str(folder / "durations.json")]
        subprocess.run(cmd, check=True)

        mask = tmp / "mask.png"
        corner_mask(mask)
        graph = (
            f"[0:v]fps={FPS},scale={WIDTH}:{HEIGHT}:flags=lanczos,format=rgba[v];"
            f"[1:v]format=gray[m];[v][m]alphamerge"
        )
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-t", str(SECONDS), "-i", str(video), "-loop", "1", "-i", str(mask),
             "-filter_complex", graph, "-t", str(SECONDS),
             "-c:v", "libwebp_anim", "-lossless", "0", "-q:v", str(QUALITY), "-compression_level", "6",
             "-loop", "0", "-an", str(args.output)],
            check=True,
        )
    size = args.output.stat().st_size
    print(f"wrote {args.output} ({size / 1024 / 1024:.2f} MB)")
    if size > MAX_BYTES:
        print("warning: over 3 MB; lower QUALITY or FPS", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
