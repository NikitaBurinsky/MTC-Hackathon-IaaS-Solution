from fastapi import APIRouter

from app.api.v1.routers.auth import router as auth_router
from app.api.v1.routers.billing import router as billing_router
from app.api.v1.routers.catalog import router as catalog_router
from app.api.v1.routers.instances import router as instances_router
from app.api.v1.routers.networks import router as networks_router
from app.api.v1.routers.scripts import router as scripts_router
from app.api.v1.routers.tasks import router as tasks_router
from app.api.v1.routers.tenant import router as tenant_router

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(tenant_router)
api_router.include_router(billing_router)
api_router.include_router(catalog_router)
api_router.include_router(instances_router)
api_router.include_router(tasks_router)
api_router.include_router(scripts_router)
api_router.include_router(networks_router)

