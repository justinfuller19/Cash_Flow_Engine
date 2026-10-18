"""Guarded application self-update endpoints for the Settings & Data page."""
import json
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter()
CONFIRMATION_PHRASE = "UPDATE APPLICATION"
STATUS_PATH = Path(
    os.environ.get(
        "CASHFLOW_UPDATE_STATUS_PATH",
        "/var/lib/cashflow-engine/application-update-status.json",
    )
)
REQUEST_PATH = STATUS_PATH.with_name("application-update-request")
PATH_UNIT_FILE = Path("/etc/systemd/system/cashflow-engine-update.path")


class ApplicationUpdateRequest(BaseModel):
    confirmation_phrase: str = Field(min_length=1, max_length=40)


def _read_status() -> dict:
    try:
        with STATUS_PATH.open("r", encoding="utf-8") as status_file:
            data = json.load(status_file)
        if not isinstance(data, dict):
            raise ValueError("Invalid update status")
        return data
    except FileNotFoundError:
        return {"state": "idle", "message": "No application update has run yet."}
    except (OSError, json.JSONDecodeError, ValueError):
        return {"state": "unknown", "message": "Update status is temporarily unavailable."}


@router.get("/api/settings-data/update-status")
def application_update_status():
    """Return status written by the root-owned systemd update service."""
    return _read_status()


@router.post("/api/settings-data/update-application", status_code=202)
def request_application_update(body: ApplicationUpdateRequest):
    """Create a fixed request marker observed by the root-owned systemd path unit."""
    if body.confirmation_phrase != CONFIRMATION_PHRASE:
        raise HTTPException(
            status_code=422,
            detail=f'Type "{CONFIRMATION_PHRASE}" exactly to confirm.',
        )

    if not PATH_UNIT_FILE.is_file():
        raise HTTPException(
            status_code=503,
            detail="The updater is not installed. Run the updater setup command from the deployment guide.",
        )

    current = _read_status()
    if current.get("state") in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="An application update is already in progress.")

    try:
        descriptor = os.open(
            REQUEST_PATH,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as request_file:
            request_file.write("update requested\n")
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail="An application update is already queued.") from exc
    except OSError as exc:
        raise HTTPException(status_code=503, detail="Could not queue the application update.") from exc

    return {
        "accepted": True,
        "message": "Update requested. The application will briefly restart while it updates.",
    }
