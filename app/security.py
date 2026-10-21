"""Optional Basic authentication and guarded loopback/LAN access policy."""
import base64
import binascii
import ipaddress
import os
import secrets

from starlette.requests import Request
from starlette.responses import JSONResponse, Response


def _is_loopback(host: str) -> bool:
    if host in {"localhost", "testclient"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _is_private_network_client(host: str) -> bool:
    """Accept direct private-network clients only; do not trust proxy headers."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_private or address.is_loopback


def _valid_basic_auth(header: str, username: str, password: str) -> bool:
    scheme, separator, token = header.partition(" ")
    if not separator or scheme.lower() != "basic" or not token:
        return False
    try:
        decoded = base64.b64decode(token, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return False
    supplied_username, separator, supplied_password = decoded.partition(":")
    if not separator:
        return False
    return (
        secrets.compare_digest(supplied_username.encode("utf-8"), username.encode("utf-8"))
        and secrets.compare_digest(supplied_password.encode("utf-8"), password.encode("utf-8"))
    )


async def remote_access_guard(request: Request, call_next):
    username = os.environ.get("CASHFLOW_USERNAME", "")
    password = os.environ.get("CASHFLOW_PASSWORD", "")
    has_username = bool(username)
    has_password = bool(password)

    if has_username != has_password:
        return JSONResponse(
            status_code=503,
            content={"detail": "Set both CASHFLOW_USERNAME and CASHFLOW_PASSWORD, or unset both."},
        )

    client_host = request.client.host if request.client else ""
    forwarded = request.headers.get("x-forwarded-for")
    allow_lan = os.environ.get("CASHFLOW_ALLOW_LAN", "").strip().lower() in {
        "1", "true", "yes", "on"
    }

    # Only trust direct private-network clients, never proxy-supplied addresses.
    if allow_lan and not forwarded and _is_private_network_client(client_host):
        return await call_next(request)

    if has_username and has_password:
        if not _valid_basic_auth(request.headers.get("authorization", ""), username, password):
            return Response(
                content="Authentication required",
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="CashFlow Engine", charset="UTF-8"'},
            )
        return await call_next(request)

    if forwarded or not _is_loopback(client_host):
        return JSONResponse(
            status_code=503,
            content={
                "detail": (
                    "Remote access is disabled. Set CASHFLOW_ALLOW_LAN=1 to allow direct "
                    "private-network clients, or configure CASHFLOW_USERNAME and "
                    "CASHFLOW_PASSWORD for Basic authentication."
                )
            },
        )
    return await call_next(request)
