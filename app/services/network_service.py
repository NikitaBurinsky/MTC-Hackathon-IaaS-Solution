from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.models import Network


class NetworkService:
    def list_networks(self, session: Session, tenant_id: int) -> list[Network]:
        return session.exec(
            select(Network).where(Network.tenant_id == tenant_id).order_by(Network.created_at.desc()),
        ).all()

    def create_network(self, session: Session, tenant_id: int, name: str, cidr: str, description: str | None) -> Network:
        network = Network(tenant_id=tenant_id, name=name, cidr=cidr, description=description)
        session.add(network)
        session.commit()
        session.refresh(network)
        return network

    def get_network(self, session: Session, tenant_id: int, network_id: int) -> Network:
        network = session.exec(
            select(Network).where(Network.id == network_id, Network.tenant_id == tenant_id),
        ).first()
        if not network:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Network not found")
        return network

    def update_network(
        self,
        session: Session,
        tenant_id: int,
        network_id: int,
        name: str | None,
        cidr: str | None,
        description: str | None,
    ) -> Network:
        network = self.get_network(session, tenant_id, network_id)
        if name is not None:
            network.name = name
        if cidr is not None:
            network.cidr = cidr
        if description is not None:
            network.description = description
        session.add(network)
        session.commit()
        session.refresh(network)
        return network

    def delete_network(self, session: Session, tenant_id: int, network_id: int) -> None:
        network = self.get_network(session, tenant_id, network_id)
        session.delete(network)
        session.commit()

