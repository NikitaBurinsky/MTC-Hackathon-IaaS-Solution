from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.db.init_db import init_db
from app.services import ComputeService
from app.schemas import ErrorResponse

settings = get_settings()
compute_service = ComputeService()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    compute_service.ensure_docker_available()
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    payload = ErrorResponse(
        error={
            "code": f"HTTP_{exc.status_code}",
            "message": str(exc.detail),
            "details": None,
        }
    )
    return JSONResponse(status_code=exc.status_code, content=payload.model_dump())


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    payload = ErrorResponse(
        error={
            "code": "VALIDATION_ERROR",
            "message": "Request validation failed",
            "details": exc.errors(),
        }
    )
    return JSONResponse(status_code=422, content=payload.model_dump())


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    payload = ErrorResponse(
        error={
            "code": "INTERNAL_ERROR",
            "message": "Unexpected server error",
            "details": None,
        }
    )
    return JSONResponse(status_code=500, content=payload.model_dump())

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
