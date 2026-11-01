"""Restart recovery: resume interrupted jobs without ever sending a part twice."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List

from storage.database import ACTIVE_STATUSES, Database
from transfers.manager import JobManager

log = logging.getLogger(__name__)


@dataclass
class RecoveryReport:
    resumed: List[int] = field(default_factory=list)
    needs_decision: List[int] = field(default_factory=list)

    def text(self) -> str:
        if not (self.resumed or self.needs_decision):
            return ""
        lines = ["♻️ Restarted — recovering interrupted downloads."]
        if self.resumed:
            lines.append("Resuming: " + ", ".join(f"#{i}" for i in self.resumed))
        for i in self.needs_decision:
            lines.append(
                f"⚠️ #{i} was interrupted while uploading a part; it may or may not have arrived. "
                f"Check Saved Messages, then /retry {i} to resend that part or /cancel {i}."
            )
        return "\n".join(lines)


async def recover_jobs(db: Database, manager: JobManager) -> RecoveryReport:
    report = RecoveryReport()
    for job in await db.jobs_with_status(ACTIVE_STATUSES):
        if manager.is_running(job.id):
            continue
        if job.send_inflight is not None:
            # A crash happened mid-upload: delivery is unknown, so never resend automatically.
            await db.update_job(job.id, status="failed", error="interrupted while uploading; delivery unknown")
            report.needs_decision.append(job.id)
        else:
            manager.start(job.id)
            report.resumed.append(job.id)
    return report
