from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status

from app.core.deps import get_current_tenant_id
from app.schemas import (
    DeploymentCreateRequest,
    DeploymentCreateResponse,
    DeploymentStatusResponse,
    MessageResponse,
)
from app.services import DeploymentService

router = APIRouter(prefix="/deployments", tags=["deployments"])
deployment_service = DeploymentService()


@router.post("", response_model=DeploymentCreateResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_deployment(
    payload: DeploymentCreateRequest,
    background_tasks: BackgroundTasks,
    tenant_id: int = Depends(get_current_tenant_id),
):
    if payload.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="tenant_id does not match authenticated tenant",
        )
    return await deployment_service.request_deployment(payload, background_tasks)


@router.get("/{deployment_id}", response_model=DeploymentStatusResponse)
async def get_deployment_status(
    deployment_id: str,
    tenant_id: int = Depends(get_current_tenant_id),
):
    return deployment_service.get_deployment_status(deployment_id=deployment_id, tenant_id=tenant_id)


@router.get("", response_model=list[DeploymentStatusResponse])
async def list_deployments(
    tenant_id: int = Depends(get_current_tenant_id),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    return deployment_service.list_deployments(tenant_id=tenant_id, limit=limit, offset=offset)


@router.delete("/{deployment_id}", response_model=MessageResponse)
async def delete_deployment(
    deployment_id: str,
    tenant_id: int = Depends(get_current_tenant_id),
):
    await deployment_service.delete_deployment(deployment_id=deployment_id, tenant_id=tenant_id)
    return MessageResponse(message="Deployment removed and resources released")
