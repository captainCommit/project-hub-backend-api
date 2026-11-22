"""Account > Recently deleted: list, restore and permanently delete deleted portfolios, programs and projects.

Account owners see and act on everything deleted in the account; anyone else sees what they deleted themselves.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.deleted_record import DeletedRecord
from app.models.portfolio import Portfolio
from app.models.program import Program
from app.models.user import User
from app.repositories.activity import ActivityLogRepository
from app.schemas.recently_deleted import DeletedItemRead, RestoredItemRead
from app.services.access import AccessScope, access_for
from app.services.recycle_bin import RETENTION_DAYS, RestoreError, decode, purge_expired, restore_snapshot

TABLES = {"PORTFOLIO": "portfolios", "PROGRAM": "programs", "PROJECT": "projects"}
LABELS = {"PORTFOLIO": "portfolio", "PROGRAM": "program", "PROJECT": "project"}


def display_name(user: User | None) -> str | None:
    if user is None:
        return None
    return " ".join(part for part in (user.first_name, user.last_name) if part).strip() or user.full_name or user.email


def as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class RecentlyDeletedService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list(self, *, account_id: UUID, current_user: User) -> list[DeletedItemRead]:
        access = access_for(self.db, account_id=account_id, user=current_user)
        purge_expired(self.db, account_id=account_id)
        self.db.commit()
        query = select(DeletedRecord).where(DeletedRecord.account_id == account_id)
        if not access.is_owner:
            query = query.where(DeletedRecord.deleted_by == current_user.id)
        records = self.db.scalars(query.order_by(DeletedRecord.deleted_at.desc())).all()
        users = {
            user.id: user
            for user in self.db.scalars(select(User).where(User.id.in_({r.deleted_by for r in records if r.deleted_by}))).all()
        } if records else {}
        return [self.read(record, users.get(record.deleted_by), can_restore=not access.read_only) for record in records]

    def restore(self, *, record_id: UUID, current_user: User) -> RestoredItemRead:
        record, access = self.get_for_action(record_id, current_user)
        access.require_writable()
        root = next(
            (
                {key: decode(value) for key, value in row.items()}
                for row in record.snapshot.get("rows", {}).get(TABLES[record.entity_type], [])
                if decode(row.get("id")) == record.entity_id
            ),
            None,
        )
        if root is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This item can no longer be restored.")

        overrides: dict[str, object] = {}
        if record.entity_type == "PROGRAM":
            self.require_parent(Portfolio, root["portfolio_id"], "portfolio")
        elif record.entity_type == "PROJECT":
            program = self.require_parent(Program, root["program_id"], "program")
            # The program may have moved to another portfolio since.
            overrides["portfolio_id"] = program.portfolio_id

        try:
            restore_snapshot(
                self.db, record.snapshot, root_table=TABLES[record.entity_type], root_id=record.entity_id, overrides=overrides
            )
            ActivityLogRepository(self.db).create(
                account_id=record.account_id,
                entity_type=record.entity_type,
                entity_id=record.entity_id,
                action="RESTORED",
                old_values=None,
                new_values={"name": record.name},
                created_by=current_user.id,
            )
            self.db.delete(record)
            self.db.commit()
        except RestoreError as error:
            self.db.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except IntegrityError as error:
            self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"This {LABELS[record.entity_type]} could not be restored because it conflicts with current data.",
            ) from error
        return RestoredItemRead(entity_type=record.entity_type, entity_id=record.entity_id, name=record.name)

    def delete_permanently(self, *, record_id: UUID, current_user: User) -> None:
        record, access = self.get_for_action(record_id, current_user)
        access.require_writable()
        ActivityLogRepository(self.db).create(
            account_id=record.account_id,
            entity_type=record.entity_type,
            entity_id=record.entity_id,
            action="PERMANENTLY_DELETED",
            old_values={"name": record.name},
            new_values=None,
            created_by=current_user.id,
        )
        self.db.delete(record)
        self.db.commit()

    # Helpers

    def get_for_action(self, record_id: UUID, current_user: User) -> tuple[DeletedRecord, AccessScope]:
        record = self.db.get(DeletedRecord, record_id)
        if record is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This item is no longer in Recently deleted.")
        access = access_for(self.db, account_id=record.account_id, user=current_user)
        if not access.is_owner and record.deleted_by != current_user.id:
            # Others' deletions are not visible to them at all.
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This item is no longer in Recently deleted.")
        return record, access

    def require_parent(self, model: type[Portfolio] | type[Program], parent_id: UUID, label: str):
        parent = self.db.get(model, parent_id)
        if parent is not None:
            return parent
        waiting = self.db.scalars(
            select(DeletedRecord).where(DeletedRecord.entity_id == parent_id).order_by(DeletedRecord.deleted_at.desc())
        ).first()
        detail = (
            f'Its {label} "{waiting.name}" was deleted too. Restore the {label} first.'
            if waiting is not None
            else f"Its {label} has been permanently deleted, so this cannot be restored."
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)

    def read(self, record: DeletedRecord, deleted_by: User | None, *, can_restore: bool) -> DeletedItemRead:
        deleted_at = as_utc(record.deleted_at)
        return DeletedItemRead(
            id=record.id,
            entity_type=record.entity_type,
            entity_id=record.entity_id,
            name=record.name,
            description=record.description,
            location=record.location,
            project_count=record.project_count,
            deleted_at=deleted_at,
            deleted_by_id=record.deleted_by,
            deleted_by_name=display_name(deleted_by),
            expires_at=deleted_at + timedelta(days=RETENTION_DAYS),
            can_restore=can_restore,
        )

    def read_one(self, record: DeletedRecord, current_user: User) -> DeletedItemRead:
        return self.read(record, current_user, can_restore=True)
