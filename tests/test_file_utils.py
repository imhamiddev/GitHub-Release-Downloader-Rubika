import pytest

import threading
import zipfile

from errors import JobCancelled
from tests.conftest import is_random_name, unzip_all
from utils.file_utils import random_name, safe_filename, zip_to_volumes


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


def test_random_names_are_unique_and_meaningless():
    names = {random_name() for _ in range(200)}
    assert len(names) == 200 and all(is_random_name(n) for n in names)


def test_single_zip_stores_file_under_given_member_name(tmp_path):
    src = tmp_path / "real-name.apk"
    src.write_bytes(b"payload" * 100)
    paths = zip_to_volumes(src, tmp_path / "out", "abc123", "scrambled", None)
    assert [p.name for p in paths] == ["abc123.zip"]
    assert unzip_all([paths[0].read_bytes()]) == {"scrambled": b"payload" * 100}
    with zipfile.ZipFile(paths[0]) as zf:
        assert zf.infolist()[0].compress_type == zipfile.ZIP_STORED  # no recompression
        assert "real-name" not in "".join(zf.namelist())


def test_multi_volume_zip_joins_into_valid_archive(tmp_path):
    data = bytes((i * 7) % 256 for i in range(300_000))
    src = tmp_path / "big.bin"
    src.write_bytes(data)
    paths = zip_to_volumes(src, tmp_path / "out", "xyz", "m", 100_000)
    assert [p.name for p in paths] == ["xyz.zip.001", "xyz.zip.002", "xyz.zip.003", "xyz.zip.004"]
    assert all(p.stat().st_size <= 100_000 for p in paths)
    assert unzip_all([p.read_bytes() for p in paths]) == {"m": data}


def test_split_that_fits_one_volume_becomes_plain_zip(tmp_path):
    src = tmp_path / "s.bin"
    src.write_bytes(b"x" * 1000)
    paths = zip_to_volumes(src, tmp_path / "out", "one", "m", 10_000_000)
    assert [p.name for p in paths] == ["one.zip"]


def test_cancel_removes_partial_output(tmp_path):
    src = tmp_path / "s.bin"
    src.write_bytes(b"x" * 500_000)
    ev = threading.Event()
    ev.set()
    try:
        zip_to_volumes(src, tmp_path / "out", "c", "m", 100_000, ev)
        raised = False
    except JobCancelled:
        raised = True
    assert raised and list((tmp_path / "out").glob("*")) == []
