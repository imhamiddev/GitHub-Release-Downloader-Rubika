"""Shared state for command handlers, including the per-user navigation stack."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List, Optional

from config import Config
from github.assets import AssetEntry
from github.client import GitHubClient
from github.releases import Release, ReleaseService
from github.repository import RepoRef, RepositoryService
from rubika.saved_messages import SavedMessages
from storage.database import Database
from transfers.manager import JobManager


@dataclass
class Nav:
    """Where the user currently is, so short commands (/open 2, /asset 3, /back) make sense."""

    repo: Optional[RepoRef] = None
    page: int = 1
    include_pre: bool = True
    show_extras: bool = False  # signatures / checksums are hidden unless /extras turns them on
    items: List[Release] = field(default_factory=list)
    has_next: bool = False
    release: Optional[Release] = None
    listing: List[AssetEntry] = field(default_factory=list)

    @property
    def level(self) -> str:
        if self.release is not None:
            return "release"
        if self.items:
            return "list"
        return "repo" if self.repo else "none"


@dataclass
class Context:
    cfg: Config
    db: Database
    github: GitHubClient
    repos: RepositoryService
    releases: ReleaseService
    jobs: JobManager
    chat: SavedMessages
    nav: Nav = field(default_factory=Nav)
    started_at: float = field(default_factory=time.time)
