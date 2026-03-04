import logging
from threading import Event, Thread

from sqlmodel import Session

from app.db.session import engine
from app.services.billing_service import BillingService

logger = logging.getLogger(__name__)


class BillingScheduler:
    def __init__(self, interval_sec: int = 60) -> None:
        self.interval_sec = interval_sec
        self._stop_event = Event()
        self._thread: Thread | None = None
        self._service = BillingService()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = Thread(
            target=self._run_loop,
            name="billing-scheduler",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run_loop(self) -> None:
        while not self._stop_event.wait(self.interval_sec):
            try:
                with Session(engine) as session:
                    self._service.run_realtime_billing(
                        session, interval_sec=self.interval_sec
                    )
            except Exception:  # noqa: BLE001
                logger.exception("Billing scheduler tick failed")
