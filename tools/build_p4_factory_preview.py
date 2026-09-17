"""Build preview sprites from RGB frames emitted by the P4 production renderer.
Run the firmware tests/p4_factory_face_test.cpp first (see docs/p4-factory-faces.md).
"""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from lampgo.factory_faces import FACES, RECORDING_FACES


def build(raw: Path, output: Path):
    output.mkdir(parents=True, exist_ok=True)
    atlas = {"renderer": "p4-face-v1", "width": 54, "height": 9, "ticks": 30, "effects": {}}
    thumbs = []
    for face in FACES:
        key = face["id"]
        eyes = np.frombuffer((raw / f"{key}.rgb565").read_bytes(), dtype="<u2").reshape(60, 172, 320)
        rgb = np.stack(
            (((eyes >> 11) & 31) * 255 // 31, ((eyes >> 5) & 63) * 255 // 63, (eyes & 31) * 255 // 31), axis=-1
        ).astype("uint8")
        sprite = Image.new("RGB", (3200, 1032))
        for i in range(60):
            sprite.paste(Image.fromarray(rgb[i]), ((i % 10) * 320, (i // 10) * 172))
        sprite.save(output / f"{key}.png", optimize=True)
        mouth = np.frombuffer((raw / f"{key}.rgb").read_bytes(), dtype="uint8").reshape(30, 486, 3)
        mask = np.frombuffer((raw / f"{key}.mask").read_bytes(), dtype="uint8").reshape(30, 486)
        palette, indices, frames, timeline = [], {}, [], []
        for t in range(30):
            frame = []
            for pixel, primary in zip(mouth[t], mask[t]):
                entry = ("#" + "".join(f"{int(v):02x}" for v in pixel), int(primary))
                if entry not in indices:
                    indices[entry] = len(palette)
                    palette.append(entry)
                frame.append(indices[entry])
            encoded = base64.b64encode(bytes(frame)).decode()
            if encoded not in frames:
                frames.append(encoded)
            timeline.append(frames.index(encoded))
        atlas["effects"][key] = {"palette": palette, "frames": frames, "timeline": timeline}
        thumb = Image.new("RGB", (420, 330), "#111c27")
        thumb.paste(Image.fromarray(rgb[8]), (50, 20))
        draw = ImageDraw.Draw(thumb)
        for n, col in enumerate(mouth[4]):
            x = 22 + (n % 54) * 7
            y = 216 + (n // 54) * 7
            draw.ellipse((x, y, x + 4, y + 4), fill=tuple(int(v) for v in col) if any(col) else "#27313a")
        try:
            font = ImageFont.truetype("/System/Library/Fonts/STHeiti Medium.ttc", 18)
        except OSError:
            font = ImageFont.load_default()
        draw.text((22, 289), face["label"], font=font, fill="#f2eee3")
        thumbs.append(thumb)
    (output / "led.json").write_text(json.dumps(atlas, separators=(",", ":")))
    sheet = Image.new("RGB", (420 * 4, 330 * 4), "#0a111a")
    for i, thumb in enumerate(thumbs):
        sheet.paste(thumb, ((i % 4) * 420, (i // 4) * 330))
    sheet.save(output / "contact-sheet.png")
    data = json.dumps({"faces": FACES, "recordings": RECORDING_FACES, "atlas": atlas}, ensure_ascii=False)
    html = (Path(__file__).parent / "templates" / "p4_factory_gallery.html").read_text()
    (output / "index.html").write_text(html.replace("DATA", data))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("raw", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    build(args.raw, args.output)
