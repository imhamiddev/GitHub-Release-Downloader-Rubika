import pytest

import sys
import threading

from errors import JobCancelled
from tests.conftest import is_random_name, unzip_all
from utils.file_utils import (
    create_encrypted_zip,
    random_name,
    random_password,
    safe_filename,
    split_into_volumes,
)


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


def test_random_passwords_are_unique_alphanumeric():
    pws = {random_password() for _ in range(200)}
    assert len(pws) == 200 and all(len(p) == 16 and p.isalnum() for p in pws)


def test_zip_is_built_with_aes256_password_and_member_name(tmp_path):
    src = tmp_path / "real-name.apk"
    src.write_bytes(b"payload" * 100)
    out = create_encrypted_zip(src, tmp_path / "o" / "abc.zip", "scrambled", "S3cretPassw0rdXY")
    pz = sys.modules["pyzipper"]
    writer = pz.instances[-1] if getattr(pz, "IS_FAKE", False) else None
    assert unzip_all([out.read_bytes()], "S3cretPassw0rdXY") == {"scrambled": b"payload" * 100}
    if writer is not None:
        assert writer.password == b"S3cretPassw0rdXY" and writer.nbits == 256 and writer.encryption == pz.WZ_AES


def test_zip_failure_leaves_no_partial_file(tmp_path):
    try:
        create_encrypted_zip(tmp_path / "missing", tmp_path / "o" / "x.zip", "m", "pw")
        raised = False
    except FileNotFoundError:
        raised = True
    assert raised and not (tmp_path / "o" / "x.zip").exists()


def test_cancelled_before_zipping(tmp_path):
    src = tmp_path / "s"
    src.write_bytes(b"x")
    ev = threading.Event()
    ev.set()
    try:
        create_encrypted_zip(src, tmp_path / "z.zip", "m", "pw", ev)
        raised = False
    except JobCancelled:
        raised = True
    assert raised and not (tmp_path / "z.zip").exists()


def test_volumes_join_back_into_identical_zip(tmp_path):
    z = tmp_path / "abc.zip"
    blob = bytes((i * 13) % 256 for i in range(250_000))
    z.write_bytes(blob)
    vols = split_into_volumes(z, 100_000)
    assert [v.name for v in vols] == ["abc.zip.001", "abc.zip.002", "abc.zip.003"]
    assert [v.stat().st_size for v in vols] == [100_000, 100_000, 50_000]
    assert b"".join(v.read_bytes() for v in vols) == blob
    assert not z.exists()


def test_small_zip_is_not_split(tmp_path):
    z = tmp_path / "abc.zip"
    z.write_bytes(b"x" * 10)
    assert split_into_volumes(z, 100) == [z] and z.exists()


def test_split_cancel_removes_volumes(tmp_path):
    z = tmp_path / "abc.zip"
    z.write_bytes(b"x" * 500_000)
    ev = threading.Event()
    ev.set()
    try:
        split_into_volumes(z, 100_000, ev, chunk=10_000)
        raised = False
    except JobCancelled:
        raised = True
    assert raised and sorted(p.name for p in tmp_path.iterdir()) == ["abc.zip"]
