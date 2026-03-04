from datetime import datetime

from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.models import Script


class ScriptService:
    def _assert_unique_name(
        self,
        session: Session,
        tenant_id: int,
        name: str,
        script_id: int | None = None,
    ) -> None:
        stmt = select(Script).where(Script.tenant_id == tenant_id, Script.name == name)
        if script_id is not None:
            stmt = stmt.where(Script.id != script_id)
        existing = session.exec(stmt).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Script name already exists",
            )

    def list_scripts(self, session: Session, tenant_id: int) -> list[Script]:
        return session.exec(
            select(Script)
            .where(Script.tenant_id == tenant_id)
            .order_by(Script.updated_at.desc()),
        ).all()

    def create_script(
        self, session: Session, tenant_id: int, name: str, body: str
    ) -> Script:
        self._assert_unique_name(session, tenant_id, name)
        script = Script(
            tenant_id=tenant_id, name=name, body=body, updated_at=datetime.utcnow()
        )
        session.add(script)
        session.commit()
        session.refresh(script)
        return script

    def get_script(self, session: Session, tenant_id: int, script_id: int) -> Script:
        script = session.exec(
            select(Script).where(Script.id == script_id, Script.tenant_id == tenant_id),
        ).first()
        if not script:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Script not found"
            )
        return script

    def update_script(
        self,
        session: Session,
        tenant_id: int,
        script_id: int,
        name: str | None,
        body: str | None,
    ) -> Script:
        script = self.get_script(session, tenant_id, script_id)
        if name is not None:
            self._assert_unique_name(session, tenant_id, name, script_id=script.id)
            script.name = name
        if body is not None:
            script.body = body
        script.updated_at = datetime.utcnow()
        session.add(script)
        session.commit()
        session.refresh(script)
        return script

    def delete_script(self, session: Session, tenant_id: int, script_id: int) -> None:
        script = self.get_script(session, tenant_id, script_id)
        session.delete(script)
        session.commit()
