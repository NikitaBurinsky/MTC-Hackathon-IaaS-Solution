from fastapi import APIRouter, Depends, status
from sqlmodel import Session

from app.core.deps import get_current_tenant_id
from app.db.session import get_session
from app.schemas import (
    MessageResponse,
    ScriptCreateRequest,
    ScriptRead,
    ScriptUpdateRequest,
)
from app.services import ScriptService

router = APIRouter(prefix="/scripts", tags=["scripts"])
script_service = ScriptService()


@router.get("", response_model=list[ScriptRead])
def list_scripts(
    tenant_id: int = Depends(get_current_tenant_id),
    session: Session = Depends(get_session),
):
    return script_service.list_scripts(session, tenant_id)


@router.post("", response_model=ScriptRead, status_code=status.HTTP_201_CREATED)
def create_script(
    payload: ScriptCreateRequest,
    tenant_id: int = Depends(get_current_tenant_id),
    session: Session = Depends(get_session),
):
    return script_service.create_script(session, tenant_id, payload.name, payload.body)


@router.put("/{script_id}", response_model=ScriptRead)
def update_script(
    script_id: int,
    payload: ScriptUpdateRequest,
    tenant_id: int = Depends(get_current_tenant_id),
    session: Session = Depends(get_session),
):
    return script_service.update_script(
        session=session,
        tenant_id=tenant_id,
        script_id=script_id,
        name=payload.name,
        body=payload.body,
    )


@router.delete(
    "/{script_id}", status_code=status.HTTP_200_OK, response_model=MessageResponse
)
def delete_script(
    script_id: int,
    tenant_id: int = Depends(get_current_tenant_id),
    session: Session = Depends(get_session),
):
    script_service.delete_script(session, tenant_id, script_id)
    return MessageResponse(message="Script deleted")
