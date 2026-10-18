"""Tests for the Settings & Data application updater controls."""
import json
import subprocess

from fastapi.testclient import TestClient

from app import application_update
from app.main import app


def test_update_status_reports_idle_when_no_status_file_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(application_update, "STATUS_PATH", tmp_path / "missing-status.json")
    response = TestClient(app).get("/api/settings-data/update-status")
    assert response.status_code == 200
    assert response.json()["state"] == "idle"


def test_update_requires_exact_confirmation_and_requests_only_fixed_service(tmp_path, monkeypatch):
    monkeypatch.setattr(application_update, "STATUS_PATH", tmp_path / "status.json")
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(application_update.subprocess, "run", fake_run)
    client = TestClient(app)

    rejected = client.post(
        "/api/settings-data/update-application",
        json={"confirmation_phrase": "update application"},
    )
    assert rejected.status_code == 422
    assert calls == []

    accepted = client.post(
        "/api/settings-data/update-application",
        json={"confirmation_phrase": "UPDATE APPLICATION"},
    )
    assert accepted.status_code == 202
    assert accepted.json()["accepted"] is True
    assert calls[0][0] == [
        "/usr/bin/sudo", "-n", "/usr/bin/systemctl", "start", "--no-block",
        "cashflow-engine-update.service",
    ]
    assert calls[0][1]["timeout"] == 5
    assert calls[0][1]["check"] is False


def test_update_request_reports_missing_privileged_setup(tmp_path, monkeypatch):
    monkeypatch.setattr(application_update, "STATUS_PATH", tmp_path / "status.json")
    monkeypatch.setattr(
        application_update.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 1, stdout="", stderr="sudo: a password is required"
        ),
    )
    response = TestClient(app).post(
        "/api/settings-data/update-application",
        json={"confirmation_phrase": "UPDATE APPLICATION"},
    )
    assert response.status_code == 503
    assert "not installed" in response.json()["detail"]


def test_settings_data_page_includes_application_update_controls(tmp_path, monkeypatch):
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(tmp_path / "cashflow.db"))
    monkeypatch.setenv("CASHFLOW_BACKUP_DIR", str(tmp_path / "backups"))
    response = TestClient(app).get("/settings-data")
    assert response.status_code == 200
    assert "Update application" in response.text
    assert "UPDATE APPLICATION" in response.text
    assert "/api/settings-data/update-status" in response.text
