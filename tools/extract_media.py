#!/usr/bin/env python3
"""Extract common media files from Android/NAS WeChat ``wxen`` containers.

The input backup is never modified. Existing non-empty output files are
skipped, so the command can be safely resumed after interruption.
"""
import argparse
import json
import os
import shutil
from collections import Counter

HEAD_SIZE = 4096
HEIC_BRANDS = {b"heic", b"heix", b"hevc", b"heif", b"mif1", b"msf1"}
DOC_EXTS = {"docx", "pptx", "xlsx", "doc", "xls", "zip", "epub", "mobi", "xapk", "apkm"}


def ftyp_extension(head, offset):
    brand = head[offset + 4:offset + 8]
    if brand in HEIC_BRANDS:
        return "heic"
    if brand[:3] == b"M4A":
        return "m4a"
    return "mp4"


def detect_file(head, original_ext):
    """Return (offset, extension), or None when no known signature is found."""
    if head.startswith(b"\xff\xd8\xff"):
        return 0, "jpg"
    if head.startswith(b"\x89PNG"):
        return 0, "png"
    if head.startswith(b"GIF8"):
        return 0, "gif"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return 0, "webp"
    if head.startswith(b"BM"):
        return 0, "bmp"
    if head.startswith(b"%PDF"):
        return 0, "pdf"
    if head.startswith(b"PK"):
        return 0, original_ext if original_ext in DOC_EXTS else "zip"
    if head.startswith(b"ID3") or any(head.startswith(x) for x in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2", b"\xff\xfa")):
        return 0, "mp3"
    ftyp = head.find(b"ftyp")
    if ftyp >= 4:
        return ftyp - 4, ftyp_extension(head, ftyp)
    if original_ext in {"htm", "html"}:
        tag = head.find(b"<")
        if tag >= 0:
            return tag, "html"
    return None


def extract_one(source, destination, head_size=HEAD_SIZE):
    with open(source, "rb") as stream:
        head = stream.read(head_size)
    original_ext = os.path.splitext(source)[1].lower().lstrip(".")
    if head.startswith(b"wxen"):
        # wxen consists of a 4-byte magic and a 4-byte container field.
        plan = detect_file(head[8:], original_ext)
        if plan is None:
            return "unrecognized", None
        offset, extension = plan
        offset += 8
    else:
        offset, extension = detect_file(head, original_ext) or (0, original_ext or "bin")
    output = os.path.join(destination, os.path.splitext(os.path.basename(source))[0] + "." + extension)
    if os.path.exists(output) and os.path.getsize(output) > 0:
        return "skipped", output
    os.makedirs(destination, exist_ok=True)
    with open(source, "rb") as src, open(output, "wb") as dst:
        src.seek(offset)
        shutil.copyfileobj(src, dst, 1024 * 1024)
    return "extracted", output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", required=True, help="Backup media directory")
    parser.add_argument("--output-dir", required=True, help="Directory for extracted files")
    parser.add_argument("--report", help="Optional JSON report path")
    args = parser.parse_args()
    stats = Counter()
    failures = []
    for root, _, files in os.walk(args.input_dir):
        for name in files:
            source = os.path.join(root, name)
            try:
                status, _ = extract_one(source, args.output_dir)
                stats[status] += 1
                if status == "unrecognized":
                    failures.append(os.path.relpath(source, args.input_dir))
            except (OSError, ValueError) as exc:
                stats["error"] += 1
                failures.append({"file": os.path.relpath(source, args.input_dir), "error": str(exc)})
    report = {"input_dir": os.path.abspath(args.input_dir), "output_dir": os.path.abspath(args.output_dir), "counts": dict(stats), "failures": failures}
    if args.report:
        with open(args.report, "w", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
