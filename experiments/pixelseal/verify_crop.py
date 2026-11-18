"""CPU-only PixelSeal experiment; decode receives only the attacked image.

Run from the blind_watermark repository root. See README.md for dependencies.
"""
import argparse
import hashlib
import json
import platform
import re
import subprocess
import sys
import time
import zlib
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageOps
from reedsolo import RSCodec, ReedSolomonError
from torchvision.transforms.functional import to_pil_image, to_tensor


ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
RS = RSCodec(20)  # 8-byte ID + 4-byte CRC + 20 parity bytes = 256 bits.


def pack_id(identifier):
    data = bytes.fromhex(identifier)
    assert len(data) == 8
    data += zlib.crc32(data).to_bytes(4, "big")
    return np.unpackbits(np.frombuffer(bytes(RS.encode(data)), dtype=np.uint8))


def unpack_id(bits):
    """No expected ID, original image or crop metadata is available here."""
    try:
        data, _, errata = RS.decode(np.packbits(bits).tobytes())
    except ReedSolomonError:
        return None, None
    data = bytes(data)
    if len(data) != 12 or zlib.crc32(data[:8]).to_bytes(4, "big") != data[8:]:
        return None, None
    return data[:8].hex(), len(errata)


@torch.inference_mode()
def embed_image(model, tensor, bits):
    msg = torch.tensor(bits, dtype=torch.float32).unsqueeze(0)
    start = time.perf_counter()
    marked = model.embed(tensor, msgs=msg, is_video=False)["imgs_w"][0]
    seconds = time.perf_counter() - start
    return to_pil_image(marked), seconds


@torch.inference_mode()
def decode_file(model, path):
    """The sole per-image input is a saved image, at its received dimensions."""
    with Image.open(path) as image:
        tensor = to_tensor(ImageOps.exif_transpose(image).convert("RGB")).unsqueeze(0)
    start = time.perf_counter()
    preds = model.detect(tensor, is_video=False)["preds"]
    assert preds.shape == (1, 257), preds.shape
    scores = preds[0, 1:].numpy()
    return (scores > 0).astype(np.uint8), scores, time.perf_counter() - start


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=HERE / "upstream")
    parser.add_argument("--checkpoint", type=Path, default=HERE / "models/checkpoint.pth")
    parser.add_argument("--output", type=Path, help="Embed output PNG or experiment output directory")
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--encoding", choices=("rs", "bch"), default="rs")
    parser.add_argument("--skip-random", action="store_true", help="Test only encoded IDs")
    parser.add_argument("--seed", type=int, default=20261117)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--embed", type=Path, metavar="IMAGE", help="Embed a specified 64-bit ID")
    action.add_argument("--decode", type=Path, nargs="+", metavar="IMAGE",
                        help="Decode images without original image or expected ID")
    parser.add_argument("--bch-retry", action="store_true",
                        help="Try flipping low-confidence bits if standard BCH decoding fails")
    parser.add_argument("--id", help="ID for --embed, exactly 16 hexadecimal characters")
    args = parser.parse_args()
    if args.embed is not None:
        if args.id is None or re.fullmatch(r"[0-9a-fA-F]{16}", args.id) is None:
            parser.error("--embed requires --id with exactly 16 hexadecimal characters")
        if args.output is None or args.output.suffix.lower() != ".png":
            parser.error("--embed requires --output ending in .png")
        if args.output.resolve() == args.embed.resolve():
            parser.error("embed input and output must be different files")
    elif args.id is not None:
        parser.error("--id is only valid with --embed")
    if args.bch_retry and (not args.decode or args.encoding != "bch"):
        parser.error("--bch-retry requires --decode and --encoding bch")
    torch.set_num_threads(args.threads)
    torch.set_num_interop_threads(1)
    torch.manual_seed(args.seed)
    sys.path.insert(0, str(args.source.resolve()))
    from omegaconf import OmegaConf
    from videoseal.utils.cfg import setup_model

    encoder, decoder = pack_id, unpack_id
    correction_field = "rs_corrected_bytes"
    encoding_description = "RS(32,12): 8-byte ID + CRC32 + 20 parity bytes; external to PixelSeal."
    if args.encoding == "bch":
        from bch_id import BCHIdentifier
        codec = BCHIdentifier()
        encoder, decoder = codec.encode, codec.decode
        correction_field = "bch_corrected_bits"
        encoding_description = (f"BCH({codec.code.n},{codec.code.k}), d={codec.code.d}, t={codec.code.t}: "
                                "64-bit ID + CRC32 + 3 zero bits; 255-bit codeword + 1 unused bit. External to PixelSeal.")
    cfg = OmegaConf.load(args.source / "videoseal/cards/pixelseal.yaml")
    cfg.args.attenuation_config = str(args.source / "videoseal/configs/attenuation.yaml")
    if not Path(cfg.args.attenuation_config).is_file():
        cfg.args.attenuation_config = str(args.source / "configs/attenuation.yaml")
    start = time.perf_counter()
    model = setup_model(cfg, args.checkpoint.resolve()).to("cpu").eval()
    load_seconds = time.perf_counter() - start
    assert all(p.device.type == "cpu" for p in model.parameters())

    if args.embed is not None:
        identifier = args.id.lower()
        with Image.open(args.embed) as image:
            tensor = to_tensor(ImageOps.exif_transpose(image).convert("RGB")).unsqueeze(0)
        marked, seconds = embed_image(model, tensor, encoder(identifier))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        marked.save(args.output)
        print(json.dumps({"file": str(args.output), "embedded_id": identifier,
                          "encoding": args.encoding, "size_wh": marked.size,
                          "embed_seconds": seconds}, indent=2))
        return

    if args.decode:
        decoded = []
        for path in args.decode:
            bits, scores, seconds = decode_file(model, path)
            identifier, corrected = decoder(bits)
            retry_used = args.bch_retry and identifier is None
            if retry_used:
                identifier, corrected = codec.retry(bits, scores)
            row = {"file": str(path), "decoded_id": identifier,
                   correction_field: corrected, "detect_seconds": seconds,
                   "raw_bits_hex": np.packbits(bits).tobytes().hex()}
            if args.bch_retry:
                row["bch_retry_used"] = retry_used
            decoded.append(row)
        print(json.dumps(decoded[0] if len(decoded) == 1 else decoded, indent=2))
        return

    output = (args.output or HERE / ("outputs" if args.encoding == "rs" else "outputs_bch")).resolve()
    output.mkdir(parents=True, exist_ok=True)
    original = Image.open(ROOT / "examples/pic/ori_img.jpeg").convert("RGB")
    crop_box = (0, 395, 738, 1108)
    assert original.size == crop_box[2:], original.size
    controls = {
        "negative_original": original,
        "negative_original_crop": original.crop(crop_box),
    }
    old_marked_path = ROOT / "examples/output/embedded.png"
    old_cropped_path = ROOT / "examples/output/embedded2.png"
    if old_marked_path.is_file() and old_cropped_path.is_file():
        with Image.open(old_marked_path) as marked_file, Image.open(old_cropped_path) as cropped_file:
            old_marked = marked_file.convert("RGB")
            old_cropped = cropped_file.convert("RGB")
        if np.array_equal(np.asarray(old_marked.crop(crop_box)), np.asarray(old_cropped)):
            controls["negative_other_algorithm"] = old_cropped
    tensor = to_tensor(original).unsqueeze(0)
    rng = np.random.default_rng(args.seed)
    results = []
    embeddings = []

    def evaluate(path, name, expected_bits=None, expected_id=None):
        bits, _, seconds = decode_file(model, path)
        correction_start = time.perf_counter()
        identifier, corrected = decoder(bits)
        correction_seconds = time.perf_counter() - correction_start
        with Image.open(path) as img:
            size = list(img.size)
        row = {"case": name, "file": str(path.relative_to(output)),
               "size_wh": size, "detect_seconds": seconds,
               "decoded_bits_hex": np.packbits(bits).tobytes().hex(),
               "decoded_id": identifier, correction_field: corrected,
               "correction_seconds": correction_seconds}
        if expected_bits is not None:
            errors = int(np.count_nonzero(bits != expected_bits))
            row.update(bit_errors=errors, bit_accuracy=1 - errors / 256,
                       raw_256_exact=errors == 0)
        if expected_id is not None:
            row.update(expected_id=expected_id, id_exact=identifier == expected_id)
        results.append(row)
        print(json.dumps(row), flush=True)

    modes = (f"id64_{args.encoding}",) if args.skip_random else ("random256", f"id64_{args.encoding}")
    for mode in modes:
        for trial in range(args.trials):
            prefix = f"{mode}_{trial}"
            identifier = rng.bytes(8).hex() if mode.startswith("id64_") else None
            bits = encoder(identifier) if identifier else rng.integers(0, 2, 256, dtype=np.uint8)
            marked, seconds = embed_image(model, tensor, bits)
            # Follow the official to_pil_image conversion, then reload the PNG.
            marked_path = output / f"{prefix}_full.png"
            marked.save(marked_path)
            marked = Image.open(marked_path).convert("RGB")
            mse = np.mean((np.asarray(marked, dtype=np.float64) - np.asarray(original, dtype=np.float64)) ** 2)
            embeddings.append({"trial": prefix, "message_hex": np.packbits(bits).tobytes().hex(),
                               "id": identifier, "embed_seconds": seconds,
                               "psnr_rgb_db": float(10 * np.log10(255 ** 2 / mse))})
            evaluate(marked_path, prefix + "/full", bits, identifier)
            cropped = marked.crop(crop_box)
            path = output / f"{prefix}_user_crop.png"
            cropped.save(path)
            evaluate(path, prefix + "/user_crop", bits, identifier)
            if mode.startswith("id64_") and trial == 0:
                variants = {
                    "keep_top_713": marked.crop((0, 0, 738, 713)),
                    "center_50pct_area": marked.crop((108, 162, 630, 946)),
                    "center_25pct_area": marked.crop((184, 277, 553, 831)),
                    "user_crop_half_size": cropped.resize((369, 356), Image.Resampling.LANCZOS),
                }
                for name, image in variants.items():
                    path = output / f"{prefix}_{name}.png"
                    image.save(path)
                    evaluate(path, prefix + "/" + name, bits, identifier)
                path = output / f"{prefix}_user_crop_jpeg75.jpg"
                cropped.save(path, quality=75)
                evaluate(path, prefix + "/user_crop_jpeg75", bits, identifier)

    for name, image in controls.items():
        path = output / f"{name}.png"
        image.save(path)
        evaluate(path, name)

    commit = subprocess.check_output(["git", "-C", str(args.source), "rev-parse", "HEAD"], text=True).strip()
    versions = subprocess.check_output([sys.executable, "-c",
        "import importlib.metadata as m,json; print(json.dumps({n:m.version(n) for n in ['torch','torchvision','numpy','pillow','timm','omegaconf','reedsolo','galois']}))"], text=True)
    metadata = {"model": "PixelSeal", "source_commit": commit, "device": "cpu",
                "python": sys.version, "platform": platform.platform(), "threads": args.threads,
                "versions": json.loads(versions), "load_seconds": load_seconds,
                "scaling_w": float(model.blender.scaling_w),
                "seed": args.seed, "trials": args.trials, "encoding": args.encoding,
                "crop_ltrb": crop_box, "original_size_wh": list(original.size),
                "original_sha256": hashlib.sha256((ROOT / "examples/pic/ori_img.jpeg").read_bytes()).hexdigest(),
                "checkpoint_sha256": hashlib.file_digest(args.checkpoint.open("rb"), "sha256").hexdigest(),
                "method": "Official image embed/detect; PNG saved/reloaded; no geometry supplied to decode.",
                "id_encoding": encoding_description,
                "embeddings": embeddings, "results": results}
    (output / "results.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print("Results saved:", output / "results.json", flush=True)


if __name__ == "__main__":
    main()
