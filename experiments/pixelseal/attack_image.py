"""Create an edited copy for a watermark robustness test."""

import argparse
import math
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter, ImageOps


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path, help="Output .png, .jpg or .jpeg")
    parser.add_argument("--crop", nargs=4, type=int, metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"))
    sizing = parser.add_mutually_exclusive_group()
    sizing.add_argument("--resize", nargs=2, type=int, metavar=("WIDTH", "HEIGHT"))
    sizing.add_argument("--scale", type=float, help="Scale both dimensions by this factor")
    parser.add_argument("--rotate", type=float, help="Counterclockwise degrees, expand canvas")
    parser.add_argument("--flip-horizontal", action="store_true")
    parser.add_argument("--brightness", type=float, help="Brightness multiplier")
    parser.add_argument("--blur", type=float, help="Gaussian blur radius in pixels")
    parser.add_argument("--jpeg-quality", type=int, help="JPEG quality from 1 to 100 (default 75)")
    args = parser.parse_args()

    suffix = args.output.suffix.lower()
    if suffix not in (".png", ".jpg", ".jpeg"):
        parser.error("output must end in .png, .jpg or .jpeg")
    if args.input.resolve() == args.output.resolve():
        parser.error("input and output must be different files")
    if args.jpeg_quality is not None:
        if suffix == ".png" or not 1 <= args.jpeg_quality <= 100:
            parser.error("--jpeg-quality requires .jpg/.jpeg output and a value from 1 to 100")
    if args.resize is not None and min(args.resize) < 1:
        parser.error("--resize dimensions must be positive")
    if args.scale is not None and (not math.isfinite(args.scale) or args.scale <= 0):
        parser.error("--scale must be finite and positive")
    if args.rotate is not None and not math.isfinite(args.rotate):
        parser.error("--rotate must be finite")
    for option, value in (("--brightness", args.brightness), ("--blur", args.blur)):
        if value is not None and (not math.isfinite(value) or value < 0):
            parser.error(f"{option} must be finite and non-negative")

    with Image.open(args.input) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
    if args.crop is not None:
        left, top, right, bottom = args.crop
        if not (0 <= left < right <= image.width and 0 <= top < bottom <= image.height):
            parser.error(f"--crop must specify a non-empty box inside {image.width}x{image.height}")
        image = image.crop((left, top, right, bottom))

    size = args.resize
    if args.scale is not None:
        size = tuple(max(1, round(length * args.scale)) for length in image.size)
    if size is not None:
        image = image.resize(size, Image.Resampling.LANCZOS)
    if args.rotate is not None:
        image = image.rotate(args.rotate, resample=Image.Resampling.BICUBIC, expand=True)
    if args.flip_horizontal:
        image = ImageOps.mirror(image)
    if args.brightness is not None:
        image = ImageEnhance.Brightness(image).enhance(args.brightness)
    if args.blur is not None:
        image = image.filter(ImageFilter.GaussianBlur(args.blur))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    if suffix == ".png":
        image.save(args.output)
    else:
        image.save(args.output, quality=args.jpeg_quality if args.jpeg_quality is not None else 75)
    print(f"{args.output} ({image.width}x{image.height})")


if __name__ == "__main__":
    main()
