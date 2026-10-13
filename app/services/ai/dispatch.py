"""Starting the answer to a question without making the request wait for it.

Deployed, the AI worker Lambda is invoked asynchronously (InvocationType=Event): the API returns at once and the
answer can take longer than the API's 28-second limit. Locally there is no worker, so the question is answered
in-process after the response has been sent.
"""

import json
import logging
from collections.abc import Callable
from uuid import UUID

from fastapi import BackgroundTasks

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

Dispatcher = Callable[[UUID, BackgroundTasks], None]


class DispatchFailed(Exception):
    pass


def answer_in_process(run_id: UUID) -> None:
    from app.core.database import SessionLocal
    from app.services.ai.runner import AssistantRunner

    with SessionLocal() as db:
        AssistantRunner(db, get_settings()).run(run_id)


def dispatch_run(run_id: UUID, background: BackgroundTasks, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    if not settings.ai_worker_function:
        background.add_task(answer_in_process, run_id)
        return
    import boto3

    try:
        boto3.client("lambda", region_name=settings.aws_region).invoke(
            FunctionName=settings.ai_worker_function,
            InvocationType="Event",
            Payload=json.dumps({"run_id": str(run_id)}).encode(),
        )
    except Exception as exc:
        logger.exception("Could not start the AI worker", extra={"run_id": str(run_id)})
        raise DispatchFailed from exc
