"""Guarded application self-update endpoints for the Settings & Data page."""
import json
import os
import subprocess
from datetime import datetime, timezone
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
    """Request the fixed, root-owned updater; never accept shell commands from users."""
    if body.confirmation_phrase != CONFIRMATION_PHRASE:
        raise HTTPException(
            status_code=422,
            detail=f'Type "{CONFIRMATION_PHRASE}" exactly to confirm.',
        )

    current = _read_status()
    if current.get("state") in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="An application update is already in progress.")

    try:
        result = subprocess.run(
            [
                "/usr/bin/sudo",
                "-n",
                "/usr/bin/systemctl",
                "start",
                "--no-block",
                "cashflow-engine-update.service",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(
            status_code=503,
            detail="Could not start the updater. Run the updater setup command from the deployment guide.",
        ) from exc

    if result.returncode != 0:
        raise HTTPException(
            status_code=503,
            detail="The updater is not installed or could not be started. See the deployment guide.",
        )

    return {
        "accepted": True,
        "message": "Update requested. The application will briefly restart while it updates.",
    }
