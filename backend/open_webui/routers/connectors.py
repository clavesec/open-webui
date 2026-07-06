"""TPAI Connectors router (Settings → Connectors, external-content m3 G3).

Proxies the user's connector-management actions to the TPAI bedrock
gateway's ``/connectors/gmail/*`` passthrough surface — OWUI itself has no
network path to the external-content connector (and must never get one:
the gateway owns TPAI's single audited egress path).

Every call is authenticated locally (``get_verified_user``) and forwarded
with the standard user-info headers; the gateway derives the pseudonymous
identity from ``X-OpenWebUI-User-Id`` and mints the short-TTL connector
JWT after its active-session cross-check. The whole surface is gated by
``ENABLE_CONNECTORS`` (default off).

The confirm endpoint carries the one-time nonce Google's OAuth callback
handed the browser — the connector activates the pending connection only
when the confirming user IS the identity that initiated the consent (the
account-linking defense), so this router forwards it verbatim and never
treats it as an identity assertion.
"""

import logging

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from open_webui.env import (
    AIOHTTP_CLIENT_SESSION_SSL,
    ENABLE_FORWARD_USER_INFO_HEADERS,
    SRC_LOG_LEVELS,
)
from open_webui.models.users import UserModel
from open_webui.utils.auth import get_verified_user

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS.get("MAIN", logging.INFO))

router = APIRouter()

GATEWAY_TIMEOUT_S = 35


class ConfirmForm(BaseModel):
    nonce: str = Field(min_length=16, max_length=256, pattern=r"^[A-Za-z0-9_-]+$")


def _require_enabled(request: Request) -> None:
    if not request.app.state.config.ENABLE_CONNECTORS:
        raise HTTPException(status_code=404, detail="Not Found")


def _gateway(request: Request) -> tuple[str, str]:
    """The TPAI gateway base URL + key — connection 0 of the OpenAI
    connections (the TPAI deployment configures exactly one: the bedrock
    gateway; its base is .../api/v1, the same prefix the connectors
    surface lives under)."""
    urls = request.app.state.config.OPENAI_API_BASE_URLS
    keys = request.app.state.config.OPENAI_API_KEYS
    if not urls or not keys or not urls[0] or not keys[0]:
        raise HTTPException(
            status_code=502, detail="Connector gateway is not configured"
        )
    return urls[0].rstrip("/"), keys[0]


async def _proxy(
    request: Request,
    user: UserModel,
    method: str,
    path: str,
    payload: dict | None = None,
):
    _require_enabled(request)
    base, key = _gateway(request)
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        **(
            {
                "X-OpenWebUI-User-Name": user.name,
                "X-OpenWebUI-User-Id": user.id,
                "X-OpenWebUI-User-Email": user.email or "",
                "X-OpenWebUI-User-Role": user.role,
            }
            if ENABLE_FORWARD_USER_INFO_HEADERS
            else {}
        ),
    }
    timeout = aiohttp.ClientTimeout(total=GATEWAY_TIMEOUT_S)
    try:
        async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
            async with session.request(
                method,
                f"{base}{path}",
                json=payload,
                headers=headers,
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as response:
                body = await response.json(content_type=None)
                if response.status >= 500:
                    log.error(
                        f"connector gateway returned {response.status} for {path}"
                    )
                    raise HTTPException(
                        status_code=502, detail="Connector gateway error"
                    )
                if response.status >= 400:
                    # Relay the deny as a FLAT string slug. Connector denies
                    # carry {"reason": slug}; gateway-originated denies carry
                    # {"detail": slug}. Passing the whole dict as `detail`
                    # double-wraps to {"detail": {...}}, which the frontend
                    # renders as "[object Object]" and whose slug the callback
                    # page's error['reason'] check misses. Extract the slug so
                    # the client always sees a string (its state checks —
                    # confirm-mismatch / confirm-invalid — still match).
                    slug = None
                    if isinstance(body, dict):
                        slug = body.get("reason") or body.get("detail")
                    raise HTTPException(
                        status_code=response.status,
                        detail=slug if isinstance(slug, str) else "connector-error",
                    )
                return body
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"connector gateway request failed ({type(e).__name__})")
        raise HTTPException(status_code=502, detail="Connector gateway unreachable")


@router.get("/gmail/status")
async def gmail_status(request: Request, user=Depends(get_verified_user)):
    return await _proxy(request, user, "GET", "/connectors/gmail/status")


@router.post("/gmail/connect")
async def gmail_connect(request: Request, user=Depends(get_verified_user)):
    """Create a consent session; the frontend navigates the user's own
    browser to the returned consent_url (the flow must originate from the
    authenticated Settings → Connectors action)."""
    return await _proxy(request, user, "POST", "/connectors/gmail/consent-session")


@router.post("/gmail/confirm")
async def gmail_confirm(
    request: Request, form_data: ConfirmForm, user=Depends(get_verified_user)
):
    return await _proxy(
        request, user, "POST", "/connectors/gmail/confirm", {"nonce": form_data.nonce}
    )


@router.post("/gmail/disconnect")
async def gmail_disconnect(request: Request, user=Depends(get_verified_user)):
    return await _proxy(request, user, "POST", "/connectors/gmail/disconnect")
