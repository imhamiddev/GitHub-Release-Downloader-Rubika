"""Runs only when the real pyzipper is installed (pip install -r requirements.txt)."""
import io
import sys

import pytest

import tests.conftest  # noqa: F401  (installs the fake when pyzipper is missing)
from utils.file_utils import create_encrypted_zip

pz = sys.modules.get("pyzipper")
pytestmark = pytest.mark.skipif(getattr(pz, "IS_FAKE", False), reason="real pyzipper not installed")


def test_real_aes256_roundtrip_and_wrong_password_fails(tmp_path):
    src = tmp_path / "secret.bin"
    src.write_bytes(b"top secret " * 1000)
    out = create_encrypted_zip(src, tmp_path / "x.zip", "scrambled", "CorrectHorse1234AB")
    with pz.AESZipFile(out) as zf:
        info = zf.infolist()[0]
        assert info.compress_type == pz.ZIP_STORED and info.filename == "scrambled"
        zf.setpassword(b"CorrectHorse1234AB")
        assert zf.read("scrambled") == src.read_bytes()
    with pz.AESZipFile(out) as zf:
        zf.setpassword(b"wrong-password")
        with pytest.raises(RuntimeError):
            zf.read("scrambled")
    raw = out.read_bytes()
    assert b"top secret" not in raw  # content really is encrypted on disk
