"""Repository reference parsing/validation and repository metadata."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

from errors import InvalidInput
from github.client import GitHubClient

_OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_GITHUB_HOSTS = {"github.com", "www.github.com"}
_NOT_REPOS = {"orgs", "users", "settings", "marketplace", "topics", "sponsors", "features", "about", "login"}


@dataclass(frozen=True)
class RepoRef:
    owner: str
    name: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    @property
    def url(self) -> str:
        return f"https://github.com/{self.owner}/{self.name}"

    def __str__(self) -> str:
        return self.full_name


def parse_repo_ref(text: str) -> RepoRef:
    """Accept ``owner/repo`` or a github.com URL; reject everything else.

    Raises InvalidInput with a user-facing message.
    """
    raw = (text or "").strip().strip("<>")
    if not raw:
        raise InvalidInput("Give a repository: owner/repo or https://github.com/owner/repo")

    if "://" in raw or raw.lower().startswith(("github.com/", "www.github.com/")):
        if "://" not in raw:
            raw = "https://" + raw
        p = urlparse(raw)
        if p.scheme not in ("http", "https") or (p.hostname or "").lower() not in _GITHUB_HOSTS:
            raise InvalidInput("Only github.com repository URLs are accepted.")
        if p.username or p.password:
            raise InvalidInput("Invalid repository URL.")
        segments = [s for s in p.path.split("/") if s]
        if len(segments) < 2:
            raise InvalidInput("The URL must point to a repository: https://github.com/owner/repo")
        owner, name = segments[0], segments[1]
    else:
        parts = raw.split("/")
        if len(parts) != 2:
            raise InvalidInput("Use the form owner/repo (for example: 2dust/v2rayNG).")
        owner, name = parts

    if name.lower().endswith(".git"):
        name = name[:-4]
    if owner.lower() in _NOT_REPOS or not _OWNER_RE.match(owner) or "--" in owner:
        raise InvalidInput(f"'{owner}' is not a valid GitHub owner name.")
    if not _NAME_RE.match(name) or name in (".", "..") or name.startswith("."):
        raise InvalidInput(f"'{name}' is not a valid repository name.")
    return RepoRef(owner, name)


@dataclass
class RepoInfo:
    name: str
    owner: str
    full_name: str
    description: Optional[str]
    stars: int
    forks: int
    language: Optional[str]
    default_branch: str
    url: str
    archived: bool = False

    @classmethod
    def from_api(cls, d: dict) -> "RepoInfo":
        return cls(
            name=d.get("name", ""),
            owner=(d.get("owner") or {}).get("login", ""),
            full_name=d.get("full_name", ""),
            description=d.get("description"),
            stars=int(d.get("stargazers_count") or 0),
            forks=int(d.get("forks_count") or 0),
            language=d.get("language"),
            default_branch=d.get("default_branch", ""),
            url=d.get("html_url", ""),
            archived=bool(d.get("archived")),
        )


class RepositoryService:
    def __init__(self, client: GitHubClient):
        self.client = client

    async def get(self, ref: RepoRef) -> RepoInfo:
        data = await self.client.get_json(f"/repos/{ref.owner}/{ref.name}")
        return RepoInfo.from_api(data)
