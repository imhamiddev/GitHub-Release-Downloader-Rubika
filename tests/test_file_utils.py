import pytest

from utils.file_utils import _raw_split, safe_filename, split_file


@pytest.mark.parametrize("raw,expected", [
    ("app-release.apk", "app-release.apk"),
    ("../../etc/passwd", "passwd"),
    ("a\\b\\c.zip", "c.zip"),
    ("-rf.sh", "rf.sh"),
    ("", "file"),
    ("..", "file"),
    ("na me;$(x).bin", "na me__(x).bin"),
])
def test_safe_filename(raw, expected):
    assert safe_filename(raw) == expected


def test_safe_filename_truncates_keeping_extension():
    n = safe_filename("a" * 400 + ".apk")
    assert len(n) <= 150 and n.endswith(".apk")


def test_raw_split_roundtrip(tmp_path):
    data = bytes(range(256)) * 40  # 10240 bytes
    src = tmp_path / "big.bin"
    src.write_bytes(data)
    parts = _raw_split(src, tmp_path, 4096)
    assert [p.stat().st_size for p in parts] == [4096, 4096, 2048]
    assert b"".join(p.read_bytes() for p in parts) == data


@pytest.mark.anyio
async def test_split_file_falls_back_without_7z(tmp_path):
    src = tmp_path / "big.bin"
    src.write_bytes(b"x" * (3 * 1024 * 1024 + 5))
    res = await split_file(src, tmp_path / "parts", 1, None)
    assert res.method == "raw" and len(res.parts) == 4
    assert sum(p.stat().st_size for p in res.parts) == src.stat().st_size
