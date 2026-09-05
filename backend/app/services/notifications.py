"""SMS / push delivery with pluggable providers.

Providers are selected by SMS_PROVIDER: `console` (default), `twilio`, `msg91`.
The console provider logs the fully rendered message and marks the delivery
`sent`, so the whole alerting path - audience resolution, per-recipient
language, delivery ledger, retry accounting - is exercised without spending
money or messaging real people during development.

Two decisions worth flagging:

* Delivery rows are written *before* the send is attempted, in `queued` state.
  If the process dies mid-batch, the ledger still shows who was owed a warning.
  The opposite order loses that record precisely during the incident.

* A provider failure never propagates. One dead gateway must not abort the
  remaining recipients, and it must not roll back the risk assessment that
  triggered the alert.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.alert import Alert, AlertDelivery
from app.services.i18n import estimate_sms_parts

logger = logging.getLogger(__name__)

SEND_TIMEOUT = 15.0


class DeliveryResult:
    __slots__ = ("ok", "provider_ref", "error")

    def __init__(self, ok: bool, provider_ref: str | None = None, error: str | None = None):
        self.ok = ok
        self.provider_ref = provider_ref
        self.error = error


# --------------------------------------------------------------------------
# Providers
# --------------------------------------------------------------------------


def _send_console(recipient: str, text: str) -> DeliveryResult:
    parts = estimate_sms_parts(text)
    logger.info(
        "[SMS/console] to=%s parts=%d chars=%d\n         %s",
        recipient,
        parts,
        len(text),
        text,
    )
    return DeliveryResult(True, provider_ref=f"console-{datetime.now(timezone.utc).timestamp():.0f}")


def _send_twilio(recipient: str, text: str) -> DeliveryResult:
    if not (settings.twilio_account_sid and settings.twilio_auth_token):
        return DeliveryResult(False, error="Twilio credentials not configured")
    try:
        response = httpx.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Messages.json",
            auth=(settings.twilio_account_sid, settings.twilio_auth_token),
            data={"To": recipient, "From": settings.twilio_from_number, "Body": text},
            timeout=SEND_TIMEOUT,
        )
        response.raise_for_status()
        return DeliveryResult(True, provider_ref=response.json().get("sid"))
    except httpx.HTTPError as exc:
        return DeliveryResult(False, error=f"Twilio error: {exc}")


def _send_msg91(recipient: str, text: str) -> DeliveryResult:
    """MSG91 - an Indian gateway with DLT registration, which matters here.

    Indian regulation requires transactional SMS templates to be pre-registered
    on a DLT platform. The template IDs must match what was registered, or
    messages are silently dropped by the operator - a failure mode that looks
    like success from the API side, so delivery reports should be reconciled
    separately before relying on this in production.
    """
    if not settings.msg91_auth_key:
        return DeliveryResult(False, error="MSG91 auth key not configured")
    try:
        response = httpx.post(
            "https://api.msg91.com/api/v2/sendsms",
            headers={"authkey": settings.msg91_auth_key, "Content-Type": "application/json"},
            json={
                "sender": settings.msg91_sender_id,
                "route": "4",
                "country": "91",
                "sms": [{"message": text, "to": [recipient.lstrip("+").removeprefix("91")]}],
            },
            timeout=SEND_TIMEOUT,
        )
        response.raise_for_status()
        return DeliveryResult(True, provider_ref=str(response.json().get("message")))
    except httpx.HTTPError as exc:
        return DeliveryResult(False, error=f"MSG91 error: {exc}")


_PROVIDERS = {"console": _send_console, "twilio": _send_twilio, "msg91": _send_msg91}


def active_provider() -> str:
    provider = settings.sms_provider.lower()
    return provider if provider in _PROVIDERS else "console"


def provider_status() -> dict:
    provider = active_provider()
    return {
        "provider": provider,
        "is_live": provider != "console",
        "note": (
            "SMS provider is `console`: messages are logged, not delivered. "
            "Set SMS_PROVIDER and the matching credentials to send for real."
        )
        if provider == "console"
        else None,
    }


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------


def queue_delivery(
    db: Session,
    alert: Alert,
    recipient: str,
    text: str,
    language: str,
    channel: str = "sms",
    user_id: int | None = None,
) -> AlertDelivery:
    """Write a delivery row in `queued` state. Does not send."""
    delivery = AlertDelivery(
        alert_id=alert.id,
        user_id=user_id,
        recipient=recipient,
        channel=channel,
        language=language,
        rendered_text=text,
        status="queued",
    )
    db.add(delivery)
    return delivery


def flush_queue(db: Session, alert_id: int | None = None) -> dict:
    """Attempt delivery for every queued row, optionally scoped to one alert."""
    query = db.query(AlertDelivery).filter(AlertDelivery.status == "queued")
    if alert_id is not None:
        query = query.filter(AlertDelivery.alert_id == alert_id)
    pending = query.all()

    send = _PROVIDERS[active_provider()]
    sent = failed = 0

    for delivery in pending:
        if delivery.channel != "sms":
            # Push/dashboard channels are delivered by the client polling the
            # API; marking them sent here keeps one consistent ledger.
            delivery.status = "sent"
            delivery.sent_at = datetime.now(timezone.utc)
            sent += 1
            continue

        try:
            result = send(delivery.recipient, delivery.rendered_text or "")
        except Exception as exc:  # noqa: BLE001 - a bad gateway must not stop the batch
            logger.exception("Unexpected send failure for %s", delivery.recipient)
            result = DeliveryResult(False, error=str(exc))

        if result.ok:
            delivery.status = "sent"
            delivery.sent_at = datetime.now(timezone.utc)
            delivery.provider_ref = result.provider_ref
            sent += 1
        else:
            delivery.status = "failed"
            delivery.error = result.error
            failed += 1

    db.commit()
    return {"attempted": len(pending), "sent": sent, "failed": failed, "provider": active_provider()}
