"""Comprehensive API, security, validation, and database performance tests for AutoPool."""

import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterator

import pytest
from fastapi.testclient import TestClient

import main
import models


@pytest.fixture()
def test_db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Provide an isolated temporary SQLite DB path without touching production data."""
    db_file = str(tmp_path / "test_rides.db")
    monkeypatch.setenv("AUTOPOOL_DB_PATH", db_file)
    monkeypatch.setattr(models, "DB_FILE", db_file)
    models.init_db(db_file)
    return db_file


@pytest.fixture()
def client(test_db_path: str) -> Iterator[TestClient]:
    """Create a FastAPI TestClient wired to the isolated temporary SQLite database."""
    main.app.dependency_overrides[main.get_db_path] = lambda: test_db_path
    with TestClient(main.app) as test_client:
        yield test_client
    main.app.dependency_overrides.clear()


def _sample_ride_payload(**overrides: Any) -> Dict[str, Any]:
    """Build a valid default ride creation payload with optional overrides."""
    payload: Dict[str, Any] = {
        "student_name": "Aarav Sharma",
        "whatsapp_number": "9876543210",
        "hostel": "Hostel J",
        "destination": "Patiala Railway Station",
        "travel_date": "2026-11-15",
        "time_slot": "04:00 PM - 04:30 PM",
        "seats_needed": 1,
        "vehicle_type": "Auto",
        "notes": "1 medium suitcase",
    }
    payload.update(overrides)
    return payload


def test_root_returns_html_and_security_headers(client: TestClient) -> None:
    """GET / serves the HTML frontend along with defensive security headers."""
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "AutoPool" in response.text

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def test_destinations_and_time_slots_metadata(client: TestClient) -> None:
    """Metadata endpoints return the expected transit hubs and 30-minute windows."""
    dest_resp = client.get("/api/destinations")
    assert dest_resp.status_code == 200
    assert dest_resp.json() == [
        "Patiala Railway Station",
        "New Bus Stand",
        "Rajpura Jn",
        "Chandigarh Airport",
        "Ambala Cantt",
    ]

    slots_resp = client.get("/api/time-slots")
    assert slots_resp.status_code == 200
    slots = slots_resp.json()
    assert len(slots) == 40
    assert slots[0] == "04:00 AM - 04:30 AM"
    assert slots[-1] == "11:30 PM - 12:00 AM"


def test_create_ride_and_filter_pods(client: TestClient) -> None:
    """Happy-path ride creation, phone normalization, and pod filtering."""
    # Post ride to a preset hub with +91 prefix and spaces
    res1 = client.post(
        "/api/rides",
        json=_sample_ride_payload(whatsapp_number="+91 98765-43210"),
    )
    assert res1.status_code == 201
    body1 = res1.json()
    assert body1["success"] is True
    assert body1["ride"]["whatsapp_number"] == "9876543210"
    assert body1["ride_id"] == body1["ride"]["id"]
    assert len(body1["cancel_token"]) >= 12
    assert "cancel_token" not in body1["ride"]

    # Post ride to custom destination with leading 0 prefix
    res2 = client.post(
        "/api/rides",
        json=_sample_ride_payload(
            student_name="Simran Kaur",
            whatsapp_number="09123456789",
            destination="Elante Mall, Chandigarh",
            travel_date="2026-11-16",
            vehicle_type="Cab",
        ),
    )
    assert res2.status_code == 201
    assert res2.json()["ride"]["whatsapp_number"] == "9123456789"

    # Unfiltered query returns both pods and never exposes cancel_token
    all_pods = client.get("/api/pods")
    assert all_pods.status_code == 200
    pods_data = all_pods.json()
    assert len(pods_data) == 2
    for pod in pods_data:
        for member in pod["members"]:
            assert "cancel_token" not in member

    # Filter by exact hub
    hub_pods = client.get(
        "/api/pods", params={"destination": "Patiala Railway Station"}
    ).json()
    assert len(hub_pods) == 1
    assert hub_pods[0]["destination"] == "Patiala Railway Station"

    # Filter by custom "Other" destinations
    other_pods = client.get("/api/pods", params={"destination": "Other"}).json()
    assert len(other_pods) == 1
    assert other_pods[0]["destination"] == "Elante Mall, Chandigarh"

    # Filter by travel_date
    date_pods = client.get("/api/pods", params={"travel_date": "2026-11-16"}).json()
    assert len(date_pods) == 1
    assert date_pods[0]["travel_date"] == "2026-11-16"


@pytest.mark.parametrize(
    "bad_override",
    [
        {"whatsapp_number": "12345"},
        {"whatsapp_number": "abcdefghij"},
        {"whatsapp_number": "+91 999999999999"},
        {"student_name": "   "},
        {"student_name": "A"},
        {"hostel": ""},
        {"destination": " "},
        {"time_slot": "  "},
        {"vehicle_type": "Helicopter"},
        {"vehicle_type": ""},
        {"seats_needed": 0},
        {"seats_needed": 4},
        {"travel_date": "15-11-2026"},
        {"travel_date": "2026-02-30"},
    ],
)
def test_validation_errors_return_consistent_envelope(
    client: TestClient, bad_override: Dict[str, Any]
) -> None:
    """Invalid phone numbers, blank required fields, and bad categories fail with 422."""
    response = client.post("/api/rides", json=_sample_ride_payload(**bad_override))
    assert response.status_code == 422
    err = response.json()
    assert err["success"] is False
    assert err["error"] == "Validation Error"
    assert err["status_code"] == 422
    assert isinstance(err["detail"], list)


def test_delete_ride_token_ownership_security(client: TestClient) -> None:
    """DELETE enforces constant-time cancel_token authorization and returns 403 otherwise."""
    create_resp = client.post("/api/rides", json=_sample_ride_payload())
    assert create_resp.status_code == 201
    ride_id = create_resp.json()["ride_id"]
    token = create_resp.json()["cancel_token"]

    # Missing token -> 403 Forbidden
    no_tok = client.delete(f"/api/rides/{ride_id}")
    assert no_tok.status_code == 403
    assert no_tok.json()["success"] is False

    # Wrong query token -> 403 Forbidden
    bad_query_tok = client.delete(f"/api/rides/{ride_id}?token=wrongtoken123")
    assert bad_query_tok.status_code == 403

    # Wrong header token -> 403 Forbidden
    bad_hdr_tok = client.delete(
        f"/api/rides/{ride_id}", headers={"X-Cancel-Token": "wrongtoken123"}
    )
    assert bad_hdr_tok.status_code == 403

    # Valid header token -> 200 OK
    ok_resp = client.delete(
        f"/api/rides/{ride_id}", headers={"X-Cancel-Token": token}
    )
    assert ok_resp.status_code == 200
    assert ok_resp.json()["success"] is True

    # Deleting non-existent ride -> 404 Not Found with error envelope
    not_found = client.delete(
        f"/api/rides/{ride_id}", headers={"X-Cancel-Token": token}
    )
    assert not_found.status_code == 404
    assert not_found.json()["success"] is False

    # Create another ride and delete via query param ?token=
    create_resp2 = client.post("/api/rides", json=_sample_ride_payload())
    ride_id2 = create_resp2.json()["ride_id"]
    token2 = create_resp2.json()["cancel_token"]
    ok_query = client.delete(f"/api/rides/{ride_id2}?token={token2}")
    assert ok_query.status_code == 200


def test_pod_seat_aggregation_dynamic_capacity_and_sorting(client: TestClient) -> None:
    """Verify Auto (3) vs Cab (4) pod capacity calculations and chronological sorting."""
    # Auto pod hitting 3/3 capacity
    client.post(
        "/api/rides",
        json=_sample_ride_payload(
            student_name="Rohan",
            destination="Rajpura Jn",
            time_slot="02:00 PM - 02:30 PM",
            seats_needed=2,
            vehicle_type="Auto",
        ),
    )
    client.post(
        "/api/rides",
        json=_sample_ride_payload(
            student_name="Meera",
            destination="Rajpura Jn",
            time_slot="02:00 PM - 02:30 PM",
            seats_needed=1,
            vehicle_type="Either",
        ),
    )

    # Cab pod reaching 3/4 then 4/4 capacity at an earlier morning slot
    client.post(
        "/api/rides",
        json=_sample_ride_payload(
            student_name="Kabir",
            destination="Chandigarh Airport",
            time_slot="08:00 AM - 08:30 AM",
            seats_needed=3,
            vehicle_type="Cab",
        ),
    )

    pods_before = client.get("/api/pods").json()
    assert len(pods_before) == 2

    # Morning 08:00 AM must sort ahead of 02:00 PM
    cab_pod = pods_before[0]
    auto_pod = pods_before[1]

    assert cab_pod["time_slot"] == "08:00 AM - 08:30 AM"
    assert cab_pod["max_capacity"] == 4
    assert cab_pod["total_seats_taken"] == 3
    assert cab_pod["seats_remaining"] == 1
    assert cab_pod["is_full"] is False
    assert cab_pod["status_badge"] == "Looking for 1 more"
    assert cab_pod["members"][0]["whatsapp_link"].startswith("https://wa.me/91")
    assert "Cab" in cab_pod["members"][0]["whatsapp_link"]

    assert auto_pod["time_slot"] == "02:00 PM - 02:30 PM"
    assert auto_pod["max_capacity"] == 3
    assert auto_pod["total_seats_taken"] == 3
    assert auto_pod["seats_remaining"] == 0
    assert auto_pod["is_full"] is True
    assert auto_pod["status_badge"] == "Full Pod (3/3)"

    # Adding 1 more seat fills the Cab pod to 4/4
    client.post(
        "/api/rides",
        json=_sample_ride_payload(
            student_name="Ananya",
            destination="Chandigarh Airport",
            time_slot="08:00 AM - 08:30 AM",
            seats_needed=1,
            vehicle_type="Either",
        ),
    )
    cab_pod_after = client.get(
        "/api/pods", params={"destination": "Chandigarh Airport"}
    ).json()[0]
    assert cab_pod_after["max_capacity"] == 4
    assert cab_pod_after["total_seats_taken"] == 4
    assert cab_pod_after["is_full"] is True
    assert cab_pod_after["status_badge"] == "Full Pod (4/4)"


def test_xss_payload_storage_and_frontend_hardening(client: TestClient) -> None:
    """User-supplied script/HTML characters do not break API and frontend uses strict sanitizers."""
    xss_name = "<script>alert(1)</script>"
    xss_notes = "O'Brien\"><img src=x onerror=alert(1)>"

    resp = client.post(
        "/api/rides",
        json=_sample_ride_payload(student_name=xss_name, notes=xss_notes),
    )
    assert resp.status_code == 201
    pods = client.get("/api/pods").json()
    assert pods[0]["members"][0]["student_name"] == xss_name
    assert pods[0]["members"][0]["notes"] == xss_notes

    html_res = client.get("/")
    html = html_res.text
    assert 'lang="en"' in html
    assert "function escapeHTML(" in html
    assert "aria-live=\"polite\"" in html
    assert "maximum-scale=1.0" not in html
    assert "user-scalable=no" not in html
    assert "<header" in html and "<main" in html and "<nav" in html


def test_sqlite_pragmas_and_composite_indexes(test_db_path: str) -> None:
    """Database connections enforce WAL/NORMAL/MEMORY pragmas and composite indexes exist."""
    conn = models.get_db_connection(test_db_path)
    try:
        journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        temp_store = conn.execute("PRAGMA temp_store;").fetchone()[0]
        indexes = {
            row["name"]
            for row in conn.execute("PRAGMA index_list('ride_requests');").fetchall()
        }
    finally:
        conn.close()

    assert journal_mode.lower() == "wal"
    assert temp_store == 2  # 2 == MEMORY in SQLite PRAGMA temp_store
    assert "idx_rides_lookup" in indexes
    assert "idx_rides_dest_date_slot" in indexes
    assert "idx_rides_date_created" in indexes
    assert os.path.exists(test_db_path)


def test_centralized_database_error_envelope(
    test_db_path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Centralized exception handlers catch SQLite errors cleanly without stack-trace leaks."""

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise sqlite3.OperationalError("simulated secret internal table path")

    monkeypatch.setattr(models, "get_all_rides", _boom)
    main.app.dependency_overrides[main.get_db_path] = lambda: test_db_path
    try:
        with TestClient(main.app, raise_server_exceptions=False) as err_client:
            resp = err_client.get("/api/pods")
        assert resp.status_code == 500
        body = resp.json()
        assert body["success"] is False
        assert body["error"] == "Database Error"
        assert body["status_code"] == 500
        assert "simulated secret" not in body["detail"]
    finally:
        main.app.dependency_overrides.clear()


def test_frontend_accessibility_and_zero_unsafe_innerhtml(client: TestClient) -> None:
    """Ensure every form field has an explicit label and DOM updates avoid raw innerHTML."""
    html = client.get("/").text
    assert re.search(r'<label[^>]+for="inputName"', html)
    assert re.search(r'<label[^>]+for="inputWhatsApp"', html)
    assert re.search(r'<label[^>]+for="inputHostel"', html)
    assert re.search(r'<label[^>]+for="inputDestination"', html)
    assert re.search(r'<label[^>]+for="custom-destination"', html)
    assert re.search(r'<label[^>]+for="inputTravelDate"', html)
    assert re.search(r'<label[^>]+for="inputTimeSlot"', html)
    assert re.search(r'<label[^>]+for="inputNotes"', html)
    assert re.search(r'<label[^>]+for="searchInput"', html)
    assert re.search(r'<label[^>]+for="customDatePicker"', html)
    assert "innerHTML" not in html
    assert "debounce(" in html
    assert "Escape" in html

