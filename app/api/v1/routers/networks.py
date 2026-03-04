from fastapi import APIRouter, Depends, status
from sqlmodel import Session

from app.core.deps import get_current_tenant_id
from app.db.session import get_session
from app.schemas import (
    MessageResponse,
    NetworkCreateRequest,
    NetworkRead,
    NetworkUpdateRequest,
)
from app.services import NetworkService

router = APIRouter(prefix="/networks", tags=["networks"])
network_service = NetworkService()


@router.get("", response_model=list[NetworkRead])
def list_networks(
    tenant_id: int = Depends(get_current_tenant_id),
    session: Session = Depends(get_session),
):
    return network_service.list_networks(session, tenant_id)


@router.post("", response_model=NetworkRead, status_code=status.HTTP_201_CREATED)
def create_network(
    payload: NetworkCreateRequest,
    tenant_id: int = Depends(get_current_tenant_id),
    session: Session = Depends(get_session),
):
    return network_service.create_network(
        session=session,
        tenant_id=tenant_id,
        name=payload.name,
        cidr=payload.cidr,
        description=payload.description,
    )


@router.get("/{network_id}", response_model=NetworkRead)
def get_network(
    network_id: int,
    tenant_id: int = Depends(get_current_tenant_id),
    session: Session = Depends(get_session),
):
    return network_service.get_network(session, tenant_id, network_id)


@router.put("/{network_id}", response_model=NetworkRead)
def update_network(
    network_id: int,
    payload: NetworkUpdateRequest,
    tenant_id: int = Depends(get_current_tenant_id),
    session: Session = Depends(get_session),
):
    return network_service.update_network(
        session=session,
        tenant_id=tenant_id,
        network_id=network_id,
        name=payload.name,
        cidr=payload.cidr,
        description=payload.description,
    )


@router.delete(
    "/{network_id}", status_code=status.HTTP_200_OK, response_model=MessageResponse
)
def delete_network(
    network_id: int,
    tenant_id: int = Depends(get_current_tenant_id),
    session: Session = Depends(get_session),
):
    network_service.delete_network(session, tenant_id, network_id)
    return MessageResponse(message="Network deleted")
