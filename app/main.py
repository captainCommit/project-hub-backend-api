import logging
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum

from app.core.config import get_settings
from app.core.errors import error_response, register_exception_handlers
from app.core.logging import configure_logging
from app.core.request_context import REQUEST_ID_HEADER, generate_request_id, reset_request_id, set_request_id
from app.routers.accounts import router as accounts_router
from app.routers.activity import router as activity_router
from app.routers.attachments import router as attachments_router
from app.routers.comments import router as comments_router
from app.routers.health import router as health_router
from app.routers.hierarchy import router as hierarchy_router
from app.routers.me import router as me_router
from app.routers.options import router as options_router
from app.routers.raid import router as raid_router
from app.routers.tasks import router as tasks_router


settings = get_settings()
configure_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title=settings.app_name)
register_exception_handlers(app)

if settings.cors_origin_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.middleware("http")
async def request_context_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
    request_id = request.headers.get(REQUEST_ID_HEADER) or generate_request_id()
    request.state.request_id = request_id
    token = set_request_id(request_id)
    start_time = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
    except Exception:
        status_code = 500
        logger.exception(
            "Unhandled request error",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
            },
        )
        response = error_response(
            status_code=500,
            error_code="INTERNAL_SERVER_ERROR",
            message="Internal server error.",
            request_id=request_id,
        )
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
    finally:
        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        logger.info(
            "HTTP request completed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": status_code,
                "duration_ms": duration_ms,
            },
        )
        reset_request_id(token)


@app.get("/", tags=["root"])
def read_root() -> dict[str, str]:
    return {"message": "Project Hub API is running"}


app.include_router(health_router)
app.include_router(me_router)
app.include_router(accounts_router)
app.include_router(options_router)
app.include_router(hierarchy_router)
app.include_router(tasks_router)
app.include_router(raid_router)
app.include_router(comments_router)
app.include_router(activity_router)
app.include_router(attachments_router)

handler = Mangum(app)