import re
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.models.user import User
from app.schemas.attachments import AttachmentPresignedUploadCreate
from app.schemas.user import UserPhotoUploadRead, UserProfileUpdate, UserRead
from app.services import attachments as attachment_service
from app.services.auth import get_current_user


router = APIRouter(prefix="/api/v1", tags=["me"])

# Tour ids come from the UI's tour definitions ("raid", "governance", ...).
TOUR_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,49}$")
MAX_SEEN_TOURS = 200


def serialize_user(user: User, settings: Settings) -> dict[str, object]:
    photo_url = None
    if user.photo_s3_key and settings.s3_bucket_name:
        photo_url = attachment_service.get_s3_client(settings).generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.s3_bucket_name, "Key": user.photo_s3_key},
            ExpiresIn=settings.attachment_download_expires_seconds,
        )
    return {**UserRead.model_validate(user).model_dump(exclude={"photo_url"}), "photo_url": photo_url}


@router.get("/me", response_model=UserRead)
def read_me(
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    return serialize_user(current_user, settings)


@router.patch("/me", response_model=UserRead)
def update_me(
    profile_in: UserProfileUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    changes = profile_in.model_dump(exclude_unset=True)
    if changes.pop("remove_photo", False):
        changes["photo_s3_key"] = None
    if ("first_name" in changes or "last_name" in changes) and "full_name" not in changes:
        first_name = changes.get("first_name", current_user.first_name) or ""
        last_name = changes.get("last_name", current_user.last_name) or ""
        full_name = f"{first_name} {last_name}".strip()
        if full_name:
            changes["full_name"] = full_name
    for field, value in changes.items():
        setattr(current_user, field, value)
    db.add(current_user)
    db.commit()
    db.refresh(current_user)
    return serialize_user(current_user, settings)


@router.post("/me/tours/{tour_id}", response_model=UserRead, summary="Remember that I've seen a first-time tour")
def mark_tour_seen(
    tour_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    if not TOUR_ID.match(tour_id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown tour.")
    seen = list(current_user.seen_tours or [])
    if tour_id not in seen:
        # A new list, so SQLAlchemy notices the JSON column changed.
        current_user.seen_tours = (seen + [tour_id])[-MAX_SEEN_TOURS:]
        db.add(current_user)
        db.commit()
        db.refresh(current_user)
    return serialize_user(current_user, settings)


@router.delete("/me/tours", response_model=UserRead, summary="Show every first-time tour again")
def reset_tours(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    current_user.seen_tours = []
    db.add(current_user)
    db.commit()
    db.refresh(current_user)
    return serialize_user(current_user, settings)


@router.post("/me/photo/presigned-upload", response_model=UserPhotoUploadRead)
def create_photo_upload(
    upload_in: AttachmentPresignedUploadCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    if not settings.s3_bucket_name:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="S3 attachment bucket is not configured.",
        )
    safe_file_name = re.sub(r"[^A-Za-z0-9._-]+", "_", upload_in.file_name).strip("._") or "photo"
    s3_key = f"users/{current_user.id}/photo/{uuid4()}/{safe_file_name}"
    params: dict[str, object] = {"Bucket": settings.s3_bucket_name, "Key": s3_key}
    if upload_in.content_type:
        params["ContentType"] = upload_in.content_type
    upload_url = attachment_service.get_s3_client(settings).generate_presigned_url(
        "put_object",
        Params=params,
        ExpiresIn=settings.attachment_upload_expires_seconds,
        HttpMethod="PUT",
    )
    current_user.photo_s3_key = s3_key
    db.add(current_user)
    db.commit()
    headers = {"Content-Type": upload_in.content_type} if upload_in.content_type else {}
    return {"upload_url": upload_url, "method": "PUT", "headers": headers, "s3_key": s3_key}
