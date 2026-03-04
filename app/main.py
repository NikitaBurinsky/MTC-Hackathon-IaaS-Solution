from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.db.init_db import init_db
from app.services import ComputeService

settings = get_settings()
compute_service = ComputeService()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    compute_service.ensure_docker_available()
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.include_router(api_router, prefix=settings.api_prefix)


@app.get("/")
async def root():
    return {"message": "Cloud Platform MVP is running!"}
