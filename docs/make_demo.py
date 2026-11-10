"""Record docs/demo.gif from real oweit runs on examples/ (calls Claude: needs ANTHROPIC_API_KEY).

    uv run --group demo python docs/make_demo.py

Uses a throwaway %APPDATA% so your own oweit database is never touched.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
COMMANDS = [
    'oweit scan "examples/WhatsApp Chat with Riya.txt" examples/sent.mbox',
    "oweit list",
    "oweit tick --dry-run",
]
W, PAD, LH, ROWS = 1180, 18, 22, 24
BG, FG, PROMPT = (24, 26, 33), (220, 223, 228), (124, 199, 255)


def font() -> ImageFont.FreeTypeFont:
    for f in (
        "C:/Windows/Fonts/consola.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    ):
        if Path(f).exists():
            return ImageFont.truetype(f, 15)
    return ImageFont.load_default()


def run_all() -> list[tuple[str, list[str]]]:
    home = Path(tempfile.mkdtemp())
    try:
        (home / "oweit").mkdir()
        shutil.copy(ROOT / "examples" / "config.toml", home / "oweit" / "config.toml")
        env = {**os.environ, "APPDATA": str(home), "PYTHONIOENCODING": "utf-8"}
        out = []
        for cmd in COMMANDS:
            r = subprocess.run(
                cmd, shell=True, cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8"
            )
            if r.returncode:
                sys.exit(f"{cmd} failed:\n{r.stderr}")
            out.append((cmd, r.stdout.rstrip().splitlines()))
        return out
    finally:
        shutil.rmtree(home, ignore_errors=True)


def main() -> None:
    fnt = font()
    frames: list[Image.Image] = []
    durations: list[int] = []

    def frame(lines, ms):
        img = Image.new("RGB", (W, PAD * 2 + ROWS * LH), BG)
        d = ImageDraw.Draw(img)
        for i, (text, color) in enumerate(lines[-ROWS:]):
            d.text((PAD, PAD + i * LH), text, font=fnt, fill=color)
        frames.append(img)
        durations.append(ms)

    screen: list[tuple[str, tuple]] = []
    for cmd, output in run_all():
        print("$ " + cmd, *output, sep="\n")  # the transcript the GIF shows, for the record
        for n in range(0, len(cmd) + 3, 3):  # typing
            frame([*screen, ("$ " + cmd[:n], PROMPT)], 35)
        screen.append(("$ " + cmd, PROMPT))
        screen += [(line, FG) for line in output] + [("", FG)]
        frame(screen, 2600)
    durations[-1] = 6000
    out = ROOT / "docs" / "demo.gif"
    frames[0].save(
        out, save_all=True, append_images=frames[1:], duration=durations, loop=0, optimize=True
    )
    print(f"wrote {out} ({out.stat().st_size // 1024} KB, {len(frames)} frames)")


if __name__ == "__main__":
    main()
