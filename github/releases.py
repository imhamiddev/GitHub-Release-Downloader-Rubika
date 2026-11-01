"""Release lists, pagination, filtering and single-release lookups."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import quote

from errors import InvalidInput, NotFound
from github.assets import Asset, AssetEntry, build_listing
from github.client import GitHubClient
from github.repository import RepoRef


@dataclass
class Release:
    id: int
    name: str
    tag: str
    published_at: Optional[str]
    author: Optional[str]
    body: str
    prerelease: bool
    draft: bool
    assets: List[Asset]
    url: str
    is_latest: bool = False

    @classmethod
    def from_api(cls, d: dict) -> "Release":
        return cls(
            id=int(d["id"]),
            name=d.get("name") or d.get("tag_name") or "",
            tag=d.get("tag_name") or "",
            published_at=d.get("published_at") or d.get("created_at"),
            author=(d.get("author") or {}).get("login"),
            body=d.get("body") or "",
            prerelease=bool(d.get("prerelease")),
            draft=bool(d.get("draft")),
            assets=[Asset.from_api(a) for a in d.get("assets") or []],
            url=d.get("html_url", ""),
        )

    def listing(self, ref: RepoRef) -> List[AssetEntry]:
        return build_listing(ref.owner, ref.name, self.tag, self.assets)


@dataclass
class ReleasePage:
    items: List[Release]
    page: int
    has_next: bool
    include_prereleases: bool
    total_known: int = field(default=0)


class ReleaseService:
    def __init__(self, client: GitHubClient):
        self.client = client

    def _base(self, ref: RepoRef) -> str:
        return f"/repos/{ref.owner}/{ref.name}"

    async def latest_stable_id(self, ref: RepoRef) -> Optional[int]:
        try:
            data = await self.client.get_json(f"{self._base(ref)}/releases/latest")
        except NotFound:
            return None
        return int(data["id"])

    async def get_latest(self, ref: RepoRef) -> Release:
        try:
            data = await self.client.get_json(f"{self._base(ref)}/releases/latest")
        except NotFound:
            raise NotFound(f"{ref} has no published stable release.") from None
        rel = Release.from_api(data)
        rel.is_latest = True
        return rel

    async def get_by_tag(self, ref: RepoRef, tag: str) -> Release:
        tag = (tag or "").strip()
        if not tag or len(tag) > 200 or any(c in tag for c in "\r\n\t ") or ".." in tag.split("/"):
            raise InvalidInput("That does not look like a valid release tag.")
        try:
            data = await self.client.get_json(f"{self._base(ref)}/releases/tags/{quote(tag, safe='/')}")
        except NotFound:
            raise NotFound(f"Release '{tag}' was not found in {ref}.") from None
        rel = Release.from_api(data)
        rel.is_latest = rel.id == await self.latest_stable_id(ref)
        return rel

    async def get_page(
        self, ref: RepoRef, page: int, page_size: int, include_prereleases: bool = True
    ) -> ReleasePage:
        """Return one UI page of the full release history (GitHub pages are fetched lazily)."""
        if page < 1:
            raise InvalidInput("Page numbers start at 1.")
        needed = page * page_size + 1  # one extra item tells us whether another page exists
        collected: List[Release] = []
        gh_page, more = 1, True
        while len(collected) < needed and more:
            pg = await self.client.get_json_page(
                f"{self._base(ref)}/releases", {"per_page": 100, "page": gh_page}
            )
            for d in pg.data:
                rel = Release.from_api(d)
                if not include_prereleases and rel.prerelease:
                    continue
                collected.append(rel)
            more = pg.has_next
            gh_page += 1
        start = (page - 1) * page_size
        items = collected[start : start + page_size]
        latest_id = await self.latest_stable_id(ref) if items else None
        for rel in items:
            rel.is_latest = rel.id == latest_id
        return ReleasePage(
            items=items,
            page=page,
            has_next=len(collected) > start + page_size,
            include_prereleases=include_prereleases,
            total_known=len(collected),
        )
