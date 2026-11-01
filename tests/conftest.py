import asyncio
import sys
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from github.transport import HttpResponse  # noqa: E402


def _install_fake_pyzipper():
    """Stand-in used only when the real pyzipper is not installed (offline CI/sandboxes).

    It writes a normal STORED zip and records the password/bit-size it was given, so the wiring
    can be tested. Real AES output is covered by tests/test_encryption_real.py when pyzipper exists.
    """
    import types
    import zipfile

    mod = types.ModuleType("pyzipper")
    mod.IS_FAKE = True
    mod.ZIP_STORED = zipfile.ZIP_STORED
    mod.WZ_AES = 99
    mod.instances = []

    class AESZipFile(zipfile.ZipFile):
        def __init__(self, file, mode="r", compression=zipfile.ZIP_STORED, encryption=None, **kw):
            super().__init__(file, mode, compression, **kw)
            self.password, self.nbits, self.encryption = None, None, encryption
            mod.instances.append(self)

        def setpassword(self, pwd):
            self.password = pwd

        def setencryption(self, encryption, nbits=256):
            self.encryption, self.nbits = encryption, nbits

    mod.AESZipFile = AESZipFile
    sys.modules["pyzipper"] = mod


try:
    import pyzipper  # noqa: F401
except ImportError:
    _install_fake_pyzipper()


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FakeStream:
    def __init__(self, status, headers=None, chunks=(), fail_after=None):
        self.status, self.headers = status, {k.lower(): v for k, v in (headers or {}).items()}
        self._chunks, self._fail_after = list(chunks), fail_after

    async def aiter_bytes(self):
        from errors import TransportError

        for i, c in enumerate(self._chunks):
            if self._fail_after is not None and i >= self._fail_after:
                raise TransportError("boom")
            yield c


class FakeTransport:
    """Scripted transport: queue responses per URL substring."""

    def __init__(self):
        self.requests = []  # (method, url, headers, params)
        self.streams = []  # (url, headers)
        self._json = deque()
        self._stream_script = deque()

    def add_response(self, resp: HttpResponse):
        self._json.append(resp)

    def add_stream(self, stream: FakeStream):
        self._stream_script.append(stream)

    async def request(self, method, url, headers, params=None):
        self.requests.append((method, url, dict(headers), params))
        return self._json.popleft()

    @asynccontextmanager
    async def open_stream(self, url, headers):
        self.streams.append((url, dict(headers)))
        yield self._stream_script.popleft()

    async def aclose(self):
        pass


@pytest.fixture
def transport():
    return FakeTransport()


async def no_sleep(_):
    return None


class FakeMessenger:
    def __init__(self, guid="me-guid"):
        self.my_guid = guid
        self.texts, self.edits, self.files = [], [], []
        self.fail_files = 0
        self._own = set()
        self._n = 0

    async def send_text(self, text):
        self._n += 1
        mid = f"m{self._n}"
        self._own.add(mid)
        self.texts.append(text)
        return mid

    async def edit_text(self, message_id, text):
        self.edits.append((message_id, text))

    async def send_file(self, path, caption, progress=None):
        if self.fail_files > 0:
            self.fail_files -= 1
            raise RuntimeError("upload failed")
        self.files.append((path, caption, Path(path).read_bytes()))
        if progress:
            await progress(10, 10)

    def is_own_message(self, message_id):
        return message_id in self._own


@pytest.fixture
def messenger():
    return FakeMessenger()


def unzip_all(parts_bytes, password=None):
    """Join delivered volumes and return {member name: bytes} (what 7-Zip does with .zip.001)."""
    import io

    import pyzipper

    with pyzipper.AESZipFile(io.BytesIO(b"".join(parts_bytes))) as zf:
        if password is not None:
            zf.setpassword(password.encode())
        return {n: zf.read(n) for n in zf.namelist()}


def caption_password(caption):
    import re

    m = re.search(r"password: `([A-Za-z0-9]{16})`", caption)
    return m.group(1) if m else None


def is_random_name(name):
    import re

    return re.fullmatch(r"[0-9a-f]{20}(\.zip(\.\d{3})?)?", name) is not None
