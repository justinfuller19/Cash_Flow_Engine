"""Tests for the Settings & Data application updater controls."""
from fastapi.testclient import TestClient

from app import application_update
from app.main import app


def setup_update_paths(tmp_path, monkeypatch):
    status_path = tmp_path / "status.json"
    request_path = tmp_path / "application-update-request"
    path_unit = tmp_path / "cashflow-engine-update.path"
    path_unit.write_text("[Path]\nPathExists=/tmp/request\n", encoding="utf-8")
    monkeypatch.setattr(application_update, "STATUS_PATH", status_path)
    monkeypatch.setattr(application_update, "REQUEST_PATH", request_path)
    monkeypatch.setattr(application_update, "PATH_UNIT_FILE", path_unit)
    return status_path, request_path, path_unit


def test_update_status_reports_idle_when_no_status_file_exists(tmp_path, monkeypatch):
    setup_update_paths(tmp_path, monkeypatch)
    response = TestClient(app).get("/api/settings-data/update-status")
    assert response.status_code == 200
    assert response.json()["state"] == "idle"


def test_update_requires_exact_confirmation_and_creates_only_fixed_request_marker(tmp_path, monkeypatch):
    _status_path, request_path, _path_unit = setup_update_paths(tmp_path, monkeypatch)
    client = TestClient(app)

    rejected = client.post(
        "/api/settings-data/update-application",
        json={"confirmation_phrase": "update application"},
    )
    assert rejected.status_code == 422
    assert not request_path.exists()

    accepted = client.post(
        "/api/settings-data/update-application",
        json={"confirmation_phrase": "UPDATE APPLICATION"},
    )
    assert accepted.status_code == 202
    assert accepted.json()["accepted"] is True
    assert request_path.read_text(encoding="utf-8") == "update requested\n"

    status = application_update._read_status()
    assert status["state"] == "queued"
    assert status["updated_at"].endswith("Z")


def test_status_writer_publishes_valid_json(tmp_path, monkeypatch):
    status_path = tmp_path / "status.json"
    monkeypatch.setattr(application_update, "STATUS_PATH", status_path)

    application_update._write_status("running", "Testing status write.")

    status = application_update._read_status()
    assert status["state"] == "running"
    assert status["message"] == "Testing status write."
    assert status["updated_at"].endswith("Z")



def test_update_request_reports_missing_privileged_setup(tmp_path, monkeypatch):
    setup_update_paths(tmp_path, monkeypatch)
    monkeypatch.setattr(application_update, "PATH_UNIT_FILE", tmp_path / "not-installed.path")
    response = TestClient(app).post(
        "/api/settings-data/update-application",
        json={"confirmation_phrase": "UPDATE APPLICATION"},
    )
    assert response.status_code == 503
    assert "not installed" in response.json()["detail"]


def test_update_request_prevents_duplicate_queue(tmp_path, monkeypatch):
    _status_path, request_path, _path_unit = setup_update_paths(tmp_path, monkeypatch)
    request_path.write_text("update requested\n", encoding="utf-8")
    response = TestClient(app).post(
        "/api/settings-data/update-application",
        json={"confirmation_phrase": "UPDATE APPLICATION"},
    )
    assert response.status_code == 409


def test_settings_data_page_includes_application_update_controls(tmp_path, monkeypatch):
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(tmp_path / "cashflow.db"))
    monkeypatch.setenv("CASHFLOW_BACKUP_DIR", str(tmp_path / "backups"))
    response = TestClient(app).get("/settings-data")
    assert response.status_code == 200
    assert "Update application" in response.text
    assert "UPDATE APPLICATION" in response.text
    assert "/api/settings-data/update-status" in response.text
