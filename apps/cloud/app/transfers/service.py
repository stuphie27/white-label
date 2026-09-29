from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import TransferJob

TRANSFER_DESTINATIONS = ("customer", "zenfolio")
TRANSFER_STATUSES = ("waiting", "uploading", "verifying", "delivering", "complete", "expired", "failed")
MAX_RETENTION_DAYS = 5


def create_transfer_job(
    session: Session,
    *,
    gallery_id: str,
    destination: str,
    item_count: int = 0,
    bytes_total: int = 0,
) -> TransferJob:
    if destination not in TRANSFER_DESTINATIONS:
        raise ValueError("Unsupported transfer destination")
    now = datetime.now(timezone.utc)
    job = TransferJob(
        gallery_id=gallery_id,
        destination=destination,
        status="waiting",
        item_count=max(0, item_count),
        bytes_total=max(0, bytes_total),
        bytes_transferred=0,
        expires_at=now + timedelta(days=MAX_RETENTION_DAYS),
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def list_transfer_jobs(session: Session, *, status: str = "") -> list[TransferJob]:
    statement = select(TransferJob).order_by(TransferJob.created_at.desc())
    if status in TRANSFER_STATUSES:
        statement = statement.where(TransferJob.status == status)
    return list(session.scalars(statement))


def get_transfer_job(session: Session, job_id: str) -> TransferJob | None:
    return session.get(TransferJob, job_id)


def update_transfer_status(session: Session, job: TransferJob, status: str, *, error_message: str = "") -> TransferJob:
    if status not in TRANSFER_STATUSES:
        raise ValueError("Unsupported transfer status")
    job.status = status
    job.error_message = error_message.strip()
    if status == "complete":
        job.completed_at = datetime.now(timezone.utc)
        job.bytes_transferred = job.bytes_total
    elif status == "expired":
        job.completed_at = job.completed_at or datetime.now(timezone.utc)
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def expire_due_jobs(session: Session, *, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    due = list(session.scalars(select(TransferJob).where(
        TransferJob.expires_at <= now,
        TransferJob.status.notin_(("expired",)),
    )))
    for job in due:
        job.status = "expired"
        job.error_message = "Temporary delivery window ended; any transfer files must be deleted."
    session.commit()
    return len(due)


def transfer_status_counts(session: Session) -> dict[str, int]:
    counts = {status: 0 for status in TRANSFER_STATUSES}
    for status, total in session.execute(select(TransferJob.status, func.count(TransferJob.id)).group_by(TransferJob.status)):
        if status in counts:
            counts[status] = int(total)
    return counts


def count_active_transfers(session: Session) -> int:
    return int(session.scalar(select(func.count(TransferJob.id)).where(
        TransferJob.status.notin_(("complete", "expired", "failed"))
    )) or 0)
