import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from tools.decrypt import decrypt_xor, imei_key_derivation, validate_key
from tools.extract_media import detect_file, extract_one, validate_directories


def test_imei_derivation_returns_four_bytes():
    assert len(imei_key_derivation("123456789012345", "42")) == 4


def test_xor_round_trip():
    plaintext = "你好, WeChat"
    encrypted = bytes(byte ^ 0x36 for byte in plaintext.encode()).hex()
    assert decrypt_xor(encrypted, bytes([0x36])) == plaintext


def test_media_signature_detection():
    assert detect_file(b"\xff\xd8\xff" + b"x" * 20, "jpg") == (0, "jpg")
    assert detect_file(b"\x89PNG\r\n" + b"x" * 20, "bin") == (0, "png")


def test_wxen_media_extraction_is_resumable(tmp_path):
    source = tmp_path / "sample.jpg"
    source.write_bytes(b"wxenHEAD" + b"\xff\xd8\xffpayload")
    output = tmp_path / "out"
    status, path = extract_one(str(source), str(output))
    assert status == "extracted"
    assert path.endswith("sample.jpg")
    assert open(path, "rb").read() == b"\xff\xd8\xffpayload"
    assert extract_one(str(source), str(output))[0] == "skipped"


def test_media_extraction_preserves_duplicate_basenames(tmp_path):
    first = tmp_path / "one" / "same.jpg"
    second = tmp_path / "two" / "same.jpg"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    output = tmp_path / "out"
    extract_one(str(first), str(output), str(tmp_path))
    extract_one(str(second), str(output), str(tmp_path))
    assert (output / "one" / "same.jpg").read_bytes() == b"one"
    assert (output / "two" / "same.jpg").read_bytes() == b"two"


def test_interrupted_output_is_not_considered_complete(tmp_path):
    source = tmp_path / "sample.jpg"
    source.write_bytes(b"payload")
    output = tmp_path / "out"
    output.mkdir()
    partial = output / "sample.jpg"
    partial.write_bytes(b"partial")
    status, path = extract_one(str(source), str(output))
    assert status == "extracted"
    assert Path(path).read_bytes() == b"payload"


def test_output_must_be_outside_input(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    for output_dir in (input_dir, input_dir / "out"):
        try:
            validate_directories(str(input_dir), str(output_dir))
        except ValueError as exc:
            assert "outside input" in str(exc)
        else:
            raise AssertionError("input-contained output was accepted")


def test_normalized_extensions_do_not_collide(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    first = input_dir / "same.dat"
    second = input_dir / "same.jpg"
    first.write_bytes(b"\xff\xd8\xffone")
    second.write_bytes(b"\xff\xd8\xfftwo")
    output = tmp_path / "output"
    extract_one(str(first), str(output), str(input_dir))
    extract_one(str(second), str(output), str(input_dir))
    assert (output / "same.dat.jpg").read_bytes() == b"\xff\xd8\xffone"
    assert (output / "same.jpg").read_bytes() == b"\xff\xd8\xfftwo"


def test_missing_input_is_rejected(tmp_path):
    try:
        validate_directories(str(tmp_path / "missing"), str(tmp_path / "out"))
    except ValueError as exc:
        assert "does not exist" in str(exc)
    else:
        raise AssertionError("missing input was accepted")


def test_validate_key_on_minimal_database(tmp_path):
    db = tmp_path / "sample.xb"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE wx_chat (content TEXT, msg_type INTEGER)")
    encrypted = bytes(byte ^ 0x36 for byte in b"hello").hex()
    conn.execute("INSERT INTO wx_chat VALUES (?, 1)", (encrypted,))
    conn.commit()
    conn.close()
    rate, replacements = validate_key(str(db), bytes([0x36]))
    assert rate == 1.0
    assert replacements == 0
