"""Compare exact 64-bit ID recovery after JPEG re-encoding."""

import argparse
import csv
import random
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from blind_watermark import WaterMark
from imwatermark import WatermarkDecoder, WatermarkEncoder


METHODS = ("blind_watermark", "dwtDct", "dwtDctSvd")
DEFAULT_IMAGES = ("ori_img.jpeg", "Lena_512x512.jpg")


def quality_chain(value):
    try:
        qualities = tuple(int(part) for part in value.split(","))
    except ValueError:
        qualities = ()
    if not qualities or any(q < 0 or q > 100 for q in qualities):
        raise argparse.ArgumentTypeError("use JPEG qualities 0–100, e.g. 50 or 75,50")
    return qualities


def round_trip(image, extension, params=()):
    ok, encoded = cv2.imencode(extension, image, list(params))
    if not ok:
        raise RuntimeError(f"Could not encode {extension}")
    decoded = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    if decoded is None or decoded.shape != image.shape:
        raise RuntimeError(f"Could not decode {extension} at its original dimensions")
    return decoded


def embed(method, image, bits):
    if method == "blind_watermark":
        encoder = WaterMark(password_img=1, password_wm=1)
        encoder.read_img(img=image)
        encoder.read_wm(bits, mode="bit")
        # Match the 8-bit conversion made when saving this library's float output.
        return np.rint(encoder.embed()).astype(np.uint8)
    encoder = WatermarkEncoder()
    encoder.set_watermark("bits", bits)
    return encoder.encode(image, method)


def extract(method, image):
    if method == "blind_watermark":
        decoder = WaterMark(password_img=1, password_wm=1)
        return decoder.extract(embed_img=image, wm_shape=64, mode="bit")
    return WatermarkDecoder("bits", 64).decode(image, method)


def measure(image_path, ids, chains):
    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Could not read {image_path}")
    height, width = image.shape[:2]
    if width * height < 256 * 256 or min(width, height) < 8:
        raise ValueError(f"{image_path} is too small for the three methods")
    for value in ids:
        bits = np.unpackbits(np.frombuffer(value.to_bytes(8, "big"), dtype=np.uint8))
        for method in METHODS:
            baseline = round_trip(embed(method, image, bits), ".png")
            for chain in chains:
                received = baseline
                for quality in chain:
                    received = round_trip(received, ".jpg", [cv2.IMWRITE_JPEG_QUALITY, quality])
                recovered = np.asarray(extract(method, received))
                if recovered.shape != (64,) or not np.isin(recovered, [0, 1]).all():
                    raise ValueError(f"{method} did not return 64 binary values")
                errors = int(np.count_nonzero(bits != recovered))
                yield {
                    "image": image_path.name, "width": width, "height": height,
                    "method": method, "condition": "→".join(map(str, chain)) or "PNG",
                    "expected_id": f"{value:016x}",
                    "recovered_id": np.packbits(recovered.astype(np.uint8)).tobytes().hex(),
                    "bit_errors": errors, "exact": int(errors == 0),
                }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--images", type=Path, nargs="+",
                        default=[ROOT / "examples/pic" / name for name in DEFAULT_IMAGES])
    parser.add_argument("--ids", type=int, default=5, help="number of paired IDs per image")
    parser.add_argument("--seed", type=int, default=20261119)
    parser.add_argument("--qualities", nargs="+", type=quality_chain,
                        default=[(95,), (75,), (50,), (30,), (10,), (75, 50)],
                        help="independent JPEG conditions, e.g. 95 50 75,50 (PNG always included)")
    parser.add_argument("--output", type=Path, required=True, help="new per-trial CSV file")
    args = parser.parse_args()
    if args.ids < 1:
        parser.error("--ids must be positive")
    if args.output.exists():
        parser.error(f"output already exists: {args.output}")
    if len({p.name for p in args.images}) != len(args.images):
        parser.error("image filenames must be distinct in the report")

    rng = random.Random(args.seed)
    ids = [rng.getrandbits(64) for _ in range(args.ids)]
    chains = [(), *dict.fromkeys(args.qualities)]
    rows = []
    for path in args.images:
        print(f"Testing {path.name} ({len(ids)} IDs)", flush=True)
        rows.extend(measure(path, ids, chains))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys(), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    print("condition  method          exact/total  bit_errors/total")
    for condition in dict.fromkeys(row["condition"] for row in rows):
        for method in METHODS:
            group = [row for row in rows if (row["condition"], row["method"]) == (condition, method)]
            exact = sum(row["exact"] for row in group)
            errors = sum(row["bit_errors"] for row in group)
            print(f"{condition:9}  {method:14}  {exact}/{len(group)}          {errors}/{64 * len(group)}")


if __name__ == "__main__":
    main()
