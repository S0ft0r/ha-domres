"""Pure event decisions for DomRes account updates."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .data import integer_days, numeric_value

EVENT_READING_DEADLINE = "domres_reading_deadline"
EVENT_UNPAID_BILL = "domres_unpaid_bill"


def _account_balance(snapshot: dict[str, Any]) -> float | None:
    summary = snapshot.get("balance")
    if not isinstance(summary, dict):
        return None
    return numeric_value(summary.get("balance"))


def _indications_available(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().casefold() not in {"false", "0", "no", "нет", "unavailable"}
    return bool(value)


def _period_date(value: Any) -> str | None:
    """Normalize the API's datePeriodFrom/datePeriodTo into ISO form."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    for date_format in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, date_format).date().isoformat()
        except ValueError:
            continue
    return None


def indication_period_key(status: Any) -> tuple[str, str] | None:
    """Return a stable start/end key from the confirmed indications/status response."""
    if not isinstance(status, dict):
        return None
    period_from = _period_date(status.get("datePeriodFrom"))
    period_to = _period_date(status.get("datePeriodTo"))
    if period_from is None or period_to is None or period_from > period_to:
        return None
    return period_from, period_to


class DomResEventTracker:
    """Emit the deadline event per active window and unpaid event once per period."""

    def __init__(self, deadline_days: int, stored_state: dict[str, Any] | None = None) -> None:
        if not 1 <= deadline_days <= 7:
            raise ValueError("deadline_days must be between 1 and 7")
        self.deadline_days = deadline_days
        self._deadline_notified: set[str] = set()
        self._unpaid_notified_periods: dict[str, str] = {}
        if isinstance(stored_state, dict):
            unpaid_periods = stored_state.get("unpaid_periods")
            if isinstance(unpaid_periods, dict):
                self._unpaid_notified_periods = {
                    str(account_id): str(period_key)
                    for account_id, period_key in unpaid_periods.items()
                    if isinstance(account_id, str) and isinstance(period_key, str)
                }

    @property
    def stored_state(self) -> dict[str, dict[str, str]]:
        """Return the persistent one-event-per-period markers."""
        return {"unpaid_periods": self._unpaid_notified_periods.copy()}

    def update(self, snapshots: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
        """Return new events; unpaid notices are deduplicated across periods and restarts."""
        events: list[tuple[str, dict[str, Any]]] = []
        for snapshot in snapshots:
            account_id = str(snapshot.get("account_id") or "")
            if not account_id:
                continue
            account_name = snapshot.get("name")

            availability = snapshot.get("availability")
            if isinstance(availability, dict):
                days = integer_days(availability.get("days"))
                if days is not None:
                    if days > self.deadline_days:
                        self._deadline_notified.discard(account_id)
                    elif (
                        _indications_available(availability.get("indicationAvailability"))
                        and account_id not in self._deadline_notified
                    ):
                        events.append(
                            (
                                EVENT_READING_DEADLINE,
                                {
                                    "account_id": account_id,
                                    "account_name": account_name,
                                    "days_remaining": days,
                                    "deadline_days": self.deadline_days,
                                    "period_title": availability.get("title"),
                                },
                            )
                        )
                        self._deadline_notified.add(account_id)

            balance = _account_balance(snapshot)
            period = indication_period_key(snapshot.get("indication_status"))
            if balance is not None and balance > 0 and period is not None:
                period_key = f"{period[0]}/{period[1]}"
                if self._unpaid_notified_periods.get(account_id) != period_key:
                    summary = snapshot.get("balance")
                    fine = numeric_value(summary.get("fine")) if isinstance(summary, dict) else None
                    events.append(
                        (
                            EVENT_UNPAID_BILL,
                            {
                                "account_id": account_id,
                                "account_name": account_name,
                                "balance": balance,
                                "fine": fine,
                                "period_from": period[0],
                                "period_to": period[1],
                            },
                        )
                    )
                    self._unpaid_notified_periods[account_id] = period_key

        return events
