"""Every account member is also a resource, so work assigned to them counts as theirs.

When someone joins an account they get a resource linked to their login. If the account already has an
unlinked resource with the same name (for example one added before they were invited), that one is
linked instead of creating a duplicate.
"""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.resource import Resource
from app.models.user import User


def member_resource_name(user: User) -> str:
    return " ".join((user.full_name or "").split()) or user.email.split("@")[0]


def ensure_member_resource(db: Session, *, account_id: UUID, user: User) -> Resource:
    linked = db.scalar(select(Resource).where(Resource.account_id == account_id, Resource.user_id == user.id))
    if linked is not None:
        return linked

    name = member_resource_name(user)
    same_name = db.scalar(
        select(Resource)
        .where(
            Resource.account_id == account_id,
            Resource.user_id.is_(None),
            Resource.is_active.is_(True),
            func.lower(func.trim(Resource.name)) == name.lower(),
        )
        .order_by(Resource.created_at, Resource.id)
    )
    if same_name is not None:
        same_name.user_id = user.id
        db.flush()
        return same_name

    resource = Resource(account_id=account_id, user_id=user.id, name=name)
    db.add(resource)
    db.flush()
    return resource
