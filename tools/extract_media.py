#!/usr/bin/env python3
"""Extract common media files from Android/NAS WeChat ``wxen`` containers.

The input backup is never modified. Existing non-empty output files are
skipped, so the command can be safely resumed after interruption.
"""
import argparse
import json
import os
import shutil
import tempfile
from collections import Counter

HEAD_SIZE = 4096
HEIC_BRANDS = {b"heic", b"heix", b"hevc", b"heif", b"mif1", b"msf1"}
DOC_EXTS = {
    "docx", "pptx", "xlsx", "doc", "xls", "zip", "epub", "mobi", "xapk", "apkm"
}
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


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
    if head.startswith(OLE_MAGIC) and original_ext in {"doc", "xls"}:
        return 0, original_ext
    mp3_headers = (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2", b"\xff\xfa")
    if head.startswith(b"ID3") or any(head.startswith(x) for x in mp3_headers):
        return 0, "mp3"
    ftyp = head.find(b"ftyp")
    if ftyp >= 4:
        return ftyp - 4, ftyp_extension(head, ftyp)
    if original_ext in {"htm", "html"}:
        tag = head.find(b"<")
        if tag >= 0:
            return tag, "html"
    return None


def validate_directories(input_dir, output_dir):
    """Validate paths before walking or writing any backup data."""
    input_path = os.path.abspath(os.path.realpath(input_dir))
    output_path = os.path.abspath(os.path.realpath(output_dir))
    if not os.path.isdir(input_path):
        raise ValueError(
            "input directory does not exist or is not a directory"
        )
    if not os.access(input_path, os.R_OK | os.X_OK):
        raise ValueError("input directory is not readable")
    try:
        inside = os.path.commonpath((input_path, output_path)) == input_path
    except ValueError:
        inside = False
    if inside:
        raise ValueError("output directory must be outside input directory")
    return input_path, output_path


def extract_one(source, destination, source_root=None, head_size=HEAD_SIZE):
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
        offset, extension = detect_file(head, original_ext) or (
            0, original_ext or "bin"
        )
    if source_root:
        relative = os.path.relpath(source, source_root)
        stem, source_ext = os.path.splitext(relative)
        # Keep a mismatching source suffix so e.g. same.dat and same.jpg
        # cannot collide after both are recognized as JPEG.
        normalized_source_ext = source_ext.lower().lstrip(".")
        if normalized_source_ext == extension:
            suffix = ""
        elif normalized_source_ext:
            suffix = source_ext
        else:
            suffix = ".noext"
        output = os.path.join(destination, stem + suffix + "." + extension)
    else:
        output = os.path.join(
            destination,
            os.path.splitext(os.path.basename(source))[0] + "." + extension,
        )
    # Keep markers in a separate namespace so a legitimate source named
    # ``asset.jpg.complete`` can never collide with a marker for ``asset.jpg``.
    marker_dir = os.path.join(destination, ".complete")
    relative_output = (
        os.path.relpath(output, destination)
        if source_root else os.path.basename(output)
    )
    complete_marker = os.path.join(marker_dir, relative_output + ".marker")
    if os.path.exists(output) and os.path.exists(complete_marker):
        return "skipped", output
    os.makedirs(destination, exist_ok=True)
    os.makedirs(os.path.dirname(output), exist_ok=True)
    os.makedirs(os.path.dirname(complete_marker), exist_ok=True)
    output_dir = os.path.dirname(output)
    fd, temporary = tempfile.mkstemp(
        prefix="." + os.path.basename(output) + ".", dir=output_dir
    )
    os.close(fd)
    try:
        with open(source, "rb") as src, open(temporary, "wb") as dst:
            src.seek(offset)
            shutil.copyfileobj(src, dst, 1024 * 1024)
        os.replace(temporary, output)
        with open(complete_marker + ".tmp", "w", encoding="ascii") as marker:
            marker.write("complete\n")
        os.replace(complete_marker + ".tmp", complete_marker)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return "extracted", output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-dir", required=True, help="Backup media directory"
    )
    parser.add_argument(
        "--output-dir", required=True, help="Directory for extracted files"
    )
    parser.add_argument("--report", help="Optional JSON report path")
    args = parser.parse_args()
    try:
        input_dir, output_dir = validate_directories(
            args.input_dir, args.output_dir
        )
    except ValueError as exc:
        parser.error(str(exc))
    stats = Counter()
    failures = []
    def record_walk_error(error):
        failures.append({
            "file": os.path.relpath(error.filename, input_dir),
            "error": str(error),
        })

    for root, _, files in os.walk(input_dir, onerror=record_walk_error):
        for name in files:
            source = os.path.join(root, name)
            try:
                status, _ = extract_one(
                    source, output_dir, input_dir
                )
                stats[status] += 1
                if status == "unrecognized":
                    failures.append(os.path.relpath(source, input_dir))
            except (OSError, ValueError) as exc:
                stats["error"] += 1
                failures.append(
                    {
                        "file": os.path.relpath(source, input_dir),
                        "error": str(exc),
                    }
                )
    report = {
        "input_dir": input_dir,
        "output_dir": output_dir,
        "counts": dict(stats),
        "failures": failures,
    }
    if args.report:
        with open(args.report, "w", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
