from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum

from app.core.config import get_settings
from app.core.logging import configure_logging
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

app = FastAPI(title=settings.app_name)

if settings.cors_origin_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


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