import ipaddress

from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.models import Network


class NetworkService:
    def _assert_unique_name(
        self,
        session: Session,
        tenant_id: int,
        name: str,
        network_id: int | None = None,
    ) -> None:
        stmt = select(Network).where(Network.tenant_id == tenant_id, Network.name == name)
        if network_id is not None:
            stmt = stmt.where(Network.id != network_id)
        existing = session.exec(stmt).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Network name already exists",
            )

    def _assert_no_cidr_overlap(
        self,
        session: Session,
        tenant_id: int,
        cidr: str,
        network_id: int | None = None,
    ) -> None:
        new_net = ipaddress.ip_network(cidr, strict=False)
        stmt = select(Network).where(Network.tenant_id == tenant_id)
        if network_id is not None:
            stmt = stmt.where(Network.id != network_id)
        existing = session.exec(stmt).all()
        for net in existing:
            try:
                existing_net = ipaddress.ip_network(net.cidr, strict=False)
            except ValueError:
                continue
            if new_net.overlaps(existing_net):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"CIDR overlaps with existing network {net.id}",
                )

    def list_networks(self, session: Session, tenant_id: int) -> list[Network]:
        return session.exec(
            select(Network)
            .where(Network.tenant_id == tenant_id)
            .order_by(Network.created_at.desc()),
        ).all()

    def create_network(
        self,
        session: Session,
        tenant_id: int,
        name: str,
        cidr: str,
        description: str | None,
    ) -> Network:
        self._assert_unique_name(session, tenant_id, name)
        self._assert_no_cidr_overlap(session, tenant_id, cidr)
        network = Network(
            tenant_id=tenant_id, name=name, cidr=cidr, description=description
        )
        session.add(network)
        session.commit()
        session.refresh(network)
        return network

    def get_network(self, session: Session, tenant_id: int, network_id: int) -> Network:
        network = session.exec(
            select(Network).where(
                Network.id == network_id, Network.tenant_id == tenant_id
            ),
        ).first()
        if not network:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Network not found"
            )
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
            self._assert_unique_name(session, tenant_id, name, network_id=network.id)
            network.name = name
        if cidr is not None:
            self._assert_no_cidr_overlap(session, tenant_id, cidr, network_id=network.id)
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
