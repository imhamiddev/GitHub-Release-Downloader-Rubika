"""Release assets, source archives and the numbered listing the user selects from."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence
from urllib.parse import quote

from errors import InvalidInput
from github.client import API_BASE
from utils.file_utils import safe_filename


@dataclass(frozen=True)
class Asset:
    id: int
    name: str
    size: int
    content_type: Optional[str]
    download_count: int
    state: str
    digest: Optional[str]

    @classmethod
    def from_api(cls, d: dict) -> "Asset":
        return cls(
            id=int(d["id"]),
            name=d.get("name", ""),
            size=int(d.get("size") or 0),
            content_type=d.get("content_type"),
            download_count=int(d.get("download_count") or 0),
            state=d.get("state", "uploaded"),
            digest=d.get("digest"),
        )


@dataclass(frozen=True)
class AssetEntry:
    """One selectable row: either a real release asset or a generated source archive."""

    index: int  # 1-based position in the listing (what ``/asset N`` refers to)
    key: str  # "<asset id>", "zipball" or "tarball" (unique per release)
    name: str
    size: Optional[int]  # None for source archives (GitHub generates them on demand)
    content_type: Optional[str]
    download_count: Optional[int]
    available: bool
    digest: Optional[str]
    url: str  # API URL we build ourselves; validated again on download
    is_source: bool = False

    @property
    def asset_id(self) -> Optional[int]:
        return None if self.is_source else int(self.key)


def parse_digest(digest: Optional[str]) -> Optional[str]:
    """Return the lower-case hex SHA-256 from GitHub's ``sha256:<hex>`` digest, else None."""
    if not digest or not digest.lower().startswith("sha256:"):
        return None
    hex_part = digest.split(":", 1)[1].strip().lower()
    if len(hex_part) == 64 and all(c in "0123456789abcdef" for c in hex_part):
        return hex_part
    return None


def build_listing(owner: str, repo: str, tag: str, assets: Sequence[Asset], include_source: bool = True) -> List[AssetEntry]:
    entries: List[AssetEntry] = []
    for a in assets:
        entries.append(
            AssetEntry(
                index=len(entries) + 1,
                key=str(a.id),
                name=a.name,
                size=a.size,
                content_type=a.content_type,
                download_count=a.download_count,
                available=a.state == "uploaded",
                digest=a.digest,
                url=f"{API_BASE}/repos/{owner}/{repo}/releases/assets/{a.id}",
            )
        )
    if include_source:
        ref = quote(tag, safe="/")
        base = safe_filename(f"{repo}-{tag}")
        for key, ext, ctype in (("zipball", ".zip", "application/zip"), ("tarball", ".tar.gz", "application/gzip")):
            entries.append(
                AssetEntry(
                    index=len(entries) + 1,
                    key=key,
                    name=base + ext,
                    size=None,
                    content_type=ctype,
                    download_count=None,
                    available=True,
                    digest=None,
                    url=f"{API_BASE}/repos/{owner}/{repo}/{key}/{ref}",
                    is_source=True,
                )
            )
    return entries


def resolve_selectors(listing: Sequence[AssetEntry], selectors: Sequence[str]) -> List[AssetEntry]:
    """Map user tokens to entries.

    A number up to len(listing) is a list position; any other number is looked up as a real
    asset ID; ``zip``/``tar`` pick the source archives. Duplicates are dropped, order kept.
    """
    chosen: List[AssetEntry] = []
    seen = set()
    for token in selectors:
        t = token.strip().lower().strip(",")
        entry: Optional[AssetEntry] = None
        if t in ("zip", "zipball"):
            entry = next((e for e in listing if e.key == "zipball"), None)
        elif t in ("tar", "tarball", "tar.gz", "tgz"):
            entry = next((e for e in listing if e.key == "tarball"), None)
        elif t.isdigit():
            n = int(t)
            if 1 <= n <= len(listing):
                entry = listing[n - 1]
            else:
                entry = next((e for e in listing if not e.is_source and e.key == str(n)), None)
        else:
            raise InvalidInput(f"'{token}' is not an asset number or ID.")
        if entry is None:
            raise InvalidInput(f"'{token}' does not match any asset in this release.")
        if entry.key not in seen:
            seen.add(entry.key)
            chosen.append(entry)
    return chosen
