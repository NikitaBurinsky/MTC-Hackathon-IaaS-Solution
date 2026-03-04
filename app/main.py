from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://formatis.online",
        "https://mts-hackathon.vercel.app",
        "http://localhost",
        "http://localhost:80",
        "http://localhost:8000",
        "http://127.0.0.1",
        "http://127.0.0.1:80",
        "http://127.0.0.1:8000",
        "localhost",
        "127.0.0.1",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router, prefix=settings.api_prefix)


@app.get("/")
async def root():
    return {"message": "Cloud Platform MVP is running!"}
