from __future__ import annotations

from datetime import date, datetime, time, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Event, Gallery, SyncAsset, TransferJob
from app.gallery.service import unique_slug
from app.transfers.service import MAX_RETENTION_DAYS
from datetime import timedelta

ASSET_STATUSES = ("waiting", "uploading", "verified", "complete", "failed", "expired")


def upsert_event(
    session: Session,
    *,
    source_ref: str,
    values: dict[str, object],
) -> Event:
    master_source_ref = str(
        values.pop("master_source_ref", "") or ""
    ).strip() or None

    event_by_source = session.scalar(
        select(Event).where(Event.source_ref == source_ref)
    )

    event_by_master = None

    if master_source_ref:
        event_by_master = session.scalar(
            select(Event).where(
                Event.master_source_ref == master_source_ref
            )
        )

    if (
        event_by_source is not None
        and event_by_master is not None
        and event_by_source.id != event_by_master.id
    ):
        raise ValueError(
            "Event identity conflict: operational and master "
            "references point to different events"
        )

    event = event_by_source or event_by_master

    if event is None:
        event = Event(
            source_ref=source_ref,
            master_source_ref=master_source_ref,
            **values,
        )
        session.add(event)
    else:
        if (
            event.source_ref
            and event.source_ref != source_ref
        ):
            raise ValueError(
                "Event already belongs to another operational source"
            )

        event.source_ref = source_ref

        if master_source_ref:
            if (
                event.master_source_ref
                and event.master_source_ref != master_source_ref
            ):
                raise ValueError(
                    "Event already belongs to another Stuphie master event"
                )

            event.master_source_ref = master_source_ref

        current_status = str(
            event.status or ""
        ).strip().lower()

        for key, value in values.items():
            if key == "status":
                incoming_status = str(
                    value or ""
                ).strip().lower()

                # Cloud lifecycle remains authoritative after closure.
                if current_status == "complete":
                    if incoming_status not in {
                        "complete",
                        "archived",
                    }:
                        continue

                elif current_status == "archived":
                    if incoming_status != "archived":
                        continue

            setattr(event, key, value)

    session.commit()
    session.refresh(event)
    return event


def upsert_gallery(session: Session, *, source_ref: str, event: Event, values: dict[str, object]) -> Gallery:
    gallery = session.scalar(select(Gallery).where(Gallery.source_ref == source_ref))

    event_access_code_hash = str(
        event.gallery_access_code_hash or ""
    ).strip()

    if gallery is None:
        desired_slug = str(values.pop("slug", "") or values.get("name", "gallery"))
        gallery = Gallery(source_ref=source_ref, event_id=event.id, slug=unique_slug(session, desired_slug), **values)
        session.add(gallery)
    else:
        gallery.event_id = event.id
        for key, value in values.items():
            if key == "slug":
                gallery.slug = unique_slug(session, str(value), exclude_id=gallery.id)
            else:
                setattr(gallery, key, value)

    gallery.access_code_hash = event_access_code_hash

    if event_access_code_hash:
        gallery.visibility = "private"

    session.commit()
    session.refresh(gallery)
    return gallery


def create_or_get_transfer(session: Session, *, source_ref: str, gallery: Gallery, destination: str) -> TransferJob:
    job = session.scalar(select(TransferJob).where(TransferJob.source_ref == source_ref))
    if job is None:
        now = datetime.now(timezone.utc)
        job = TransferJob(
            source_ref=source_ref,
            gallery_id=gallery.id,
            destination=destination,
            status="waiting",
            expires_at=now + timedelta(days=MAX_RETENTION_DAYS),
        )
        session.add(job)
        session.commit()
        session.refresh(job)
    return job


def register_manifest(session: Session, *, job: TransferJob, assets: list[dict[str, object]]) -> list[SyncAsset]:
    existing = {
        asset.source_ref: asset
        for asset in session.scalars(select(SyncAsset).where(SyncAsset.transfer_job_id == job.id))
    }
    result: list[SyncAsset] = []
    total = 0
    for payload in assets:
        source_ref = str(payload["source_ref"])
        asset = existing.get(source_ref)
        if asset is None:
            asset = SyncAsset(transfer_job_id=job.id, source_ref=source_ref)
            session.add(asset)
        asset.filename = str(payload["filename"])
        asset.size_bytes = max(0, int(payload.get("size_bytes", 0)))
        asset.sha256 = str(payload.get("sha256", "")).lower()
        asset.bytes_received = int(asset.bytes_received or 0)
        if asset.bytes_received > asset.size_bytes:
            asset.bytes_received = asset.size_bytes
        total += asset.size_bytes
        result.append(asset)
    job.item_count = len(result)
    job.bytes_total = total
    job.bytes_transferred = sum(asset.bytes_received for asset in result)
    session.add(job)
    session.commit()
    for asset in result:
        session.refresh(asset)
    session.refresh(job)
    return result


def transfer_resume_state(session: Session, job: TransferJob) -> list[SyncAsset]:
    return list(session.scalars(
        select(SyncAsset).where(SyncAsset.transfer_job_id == job.id).order_by(SyncAsset.filename.asc())
    ))


def update_asset_progress(session: Session, *, asset: SyncAsset, bytes_received: int, status: str) -> SyncAsset:
    if status not in ASSET_STATUSES:
        raise ValueError("Unsupported asset status")
    asset.bytes_received = min(max(0, bytes_received), asset.size_bytes)
    asset.status = status
    job = session.get(TransferJob, asset.transfer_job_id)
    if job is not None:
        assets = transfer_resume_state(session, job)
        job.bytes_transferred = sum(a.bytes_received if a.id != asset.id else asset.bytes_received for a in assets)
        if job.bytes_transferred > 0 and job.status == "waiting":
            job.status = "uploading"
        session.add(job)
    session.add(asset)
    session.commit()
    session.refresh(asset)
    return asset
