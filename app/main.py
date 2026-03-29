from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.db.init_db import init_db
from app.schemas import ErrorResponse
from app.services import ComputeService
from app.services.billing_scheduler import BillingScheduler

settings = get_settings()
compute_service = ComputeService()
billing_scheduler = BillingScheduler()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    compute_service.ensure_docker_available()
    billing_scheduler.start()
    yield
    billing_scheduler.stop()


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    payload = ErrorResponse(
        error={
            "code": f"HTTP_{exc.status_code}",
            "message": str(exc.detail),
        }
    )
    return JSONResponse(status_code=exc.status_code, content=payload.model_dump())


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    errors = exc.errors()
    if errors:
        first = errors[0]
        loc = ".".join(str(part) for part in first.get("loc", []))
        message = f"Validation error at {loc}: {first.get('msg')}"
    else:
        message = "Request validation failed"
    payload = ErrorResponse(
        error={
            "code": "VALIDATION_ERROR",
            "message": message,
        }
    )
    return JSONResponse(status_code=422, content=payload.model_dump())


@app.exception_handler(IntegrityError)
async def integrity_error_handler(
    request: Request,
    exc: IntegrityError,
) -> JSONResponse:
    payload = ErrorResponse(
        error={
            "code": "CONFLICT",
            "message": "Conflict",
        }
    )
    return JSONResponse(status_code=409, content=payload.model_dump())


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    payload = ErrorResponse(
        error={
            "code": "INTERNAL_ERROR",
            "message": "Unexpected server error",
        }
    )
    return JSONResponse(status_code=500, content=payload.model_dump())


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://api.formatis.online",
        "https://formatis.online",
        "https://www.formatis.online",
        "https://iaas.formatis.online",
        "https://api.formatis.online",
        "http://api.formatis.online:8000",
        "http://api.formatis.online",
        "http://localhost",
        "http://localhost:80",
        "http://localhost:3000",
        "http://localhost:3001",
        "http://localhost:8000",
        "localhost",
        "localhost:80",
        "localhost:3000",
        "localhost:3001",
        "localhost:8000",
        "http://127.0.0.1",
        "http://127.0.0.1:80",
        "http://127.0.0.1j:3000",
        "http://127.0.0.1:3001",
        "http://127.0.0.1:8000",
        "127.0.0.1",
        "127.0.0.1:80",
        "127.0.0.1:3000",
        "127.0.0.1:3001",
        "127.0.0.1:8000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router, prefix=settings.api_prefix)


@app.get("/")
async def root():
    return {"message": "Cloud Platform MVP is running!"}
