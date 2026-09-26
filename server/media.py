"""Synthetic media generation for multimodal probes.

Images and animations are generated at runtime so that the model cannot have
seen them before, and so the expected answer is known exactly.
"""

from __future__ import annotations

import base64
import io
import random
import string

from PIL import Image, ImageDraw, ImageFont

_FONT_CANDIDATES = [
    "C:/Windows/Fonts/consolab.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # very old Pillow
        return ImageFont.load_default()


def random_code(length: int = 6, rng: random.Random | None = None) -> str:
    """Unambiguous uppercase code (no 0/O/1/I)."""
    rng = rng or random.Random()
    alphabet = "".join(c for c in string.ascii_uppercase + string.digits if c not in "0O1I")
    return "".join(rng.choice(alphabet) for _ in range(length))


def make_code_image(code: str, width: int = 640, height: int = 280, seed: int | None = None) -> bytes:
    """Render a code string with light decorative noise. Returns PNG bytes."""
    rng = random.Random(seed)
    img = Image.new("RGB", (width, height), (245, 247, 250))
    draw = ImageDraw.Draw(img)

    # Decorative shapes so the model must actually read the text.
    for _ in range(6):
        x0, y0 = rng.randint(0, width), rng.randint(0, height)
        x1, y1 = x0 + rng.randint(30, 120), y0 + rng.randint(30, 120)
        color = tuple(rng.randint(150, 230) for _ in range(3))
        if rng.random() < 0.5:
            draw.ellipse([x0, y0, x1, y1], outline=color, width=3)
        else:
            draw.rectangle([x0, y0, x1, y1], outline=color, width=3)

    font = _font(int(height * 0.45))
    bbox = draw.textbbox((0, 0), code, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (width - tw) // 2 - bbox[0]
    y = (height - th) // 2 - bbox[1]
    draw.text((x + 3, y + 3), code, font=font, fill=(200, 200, 210))
    draw.text((x, y), code, font=font, fill=(20, 24, 40))

    small = _font(int(height * 0.08))
    draw.text((12, height - int(height * 0.12)), "kimi-probe synthetic image", font=small, fill=(120, 130, 150))

    buf = io.BytesIO()
    # Metadata is only read by tools/mock_server.py; real vision models never see it.
    from PIL import PngImagePlugin

    meta = PngImagePlugin.PngInfo()
    meta.add_text("kimi-probe-expected", code)
    img.save(buf, format="PNG", optimize=True, pnginfo=meta)
    return buf.getvalue()


def make_digit_gif(digits: list[int], size: int = 320, frame_ms: int = 700) -> bytes:
    """Animated GIF with one large digit per frame. Returns GIF bytes.

    The Kimi platform decodes animated GIF/WebP passed through image_url as
    video, so this exercises the video understanding path without ffmpeg.
    """
    frames: list[Image.Image] = []
    font = _font(int(size * 0.6))
    palette = [(30, 64, 175), (185, 28, 28), (21, 128, 61), (109, 40, 217), (180, 83, 9)]
    for i, d in enumerate(digits):
        img = Image.new("RGB", (size, size), (250, 250, 250))
        draw = ImageDraw.Draw(img)
        color = palette[i % len(palette)]
        draw.rectangle([8, 8, size - 9, size - 9], outline=color, width=6)
        text = str(d)
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(((size - tw) // 2 - bbox[0], (size - th) // 2 - bbox[1]), text, font=font, fill=color)
        small = _font(int(size * 0.07))
        draw.text((16, size - int(size * 0.12)), f"frame {i + 1}/{len(digits)}", font=small, fill=(120, 120, 120))
        frames.append(img.convert("P", palette=Image.Palette.ADAPTIVE))

    buf = io.BytesIO()
    frames[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=frame_ms,
        loop=0,
        optimize=False,
        comment="kimi-probe-expected=" + "".join(str(d) for d in digits),
    )
    return buf.getvalue()


def to_data_url(data: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
