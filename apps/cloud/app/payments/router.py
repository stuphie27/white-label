from __future__ import annotations

from fastapi import APIRouter, Request, Response

from app.payments.sumup import SumUpError, verify_and_apply_checkout


def build_payments_router() -> APIRouter:
    router = APIRouter(prefix="/api/payments", tags=["payments"])

    @router.post("/sumup/webhook", include_in_schema=False)
    async def sumup_webhook(request: Request):
        payload = await request.json()
        # SumUp may add new event types. Unknown events are intentionally ignored.
        if not isinstance(payload, dict) or payload.get("event_type") != "CHECKOUT_STATUS_CHANGED":
            return Response(status_code=204)
        checkout_id = str(payload.get("id") or "")
        if not checkout_id:
            return Response(status_code=204)
        try:
            with request.app.state.session_factory() as session:
                verify_and_apply_checkout(session, request.app.state.settings, checkout_id)
        except SumUpError:
            # Non-2xx asks SumUp to retry the notification.
            return Response(status_code=503)
        return Response(status_code=204)

    return router
