import os
import secrets
import urllib.parse
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, HTTPException, Query, Header, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from models import (
    init_db,
    POPULAR_DESTINATIONS,
    RideCreate,
    create_ride_record,
    get_ride_record,
    delete_ride_record,
    get_all_rides,
)

app = FastAPI(
    title="AutoPool - Campus Ride Pooling",
    description="Split autos to station & bus stand without spamming WhatsApp groups",
    version="1.0.0"
)

# Enable CORS for local dev / testing
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Database on startup
@app.on_event("startup")
def startup_event():
    init_db()


def generate_time_slots() -> List[str]:
    """Generates 30-minute intervals from 04:00 AM to 11:30 PM."""
    slots = []
    # Starting from 04:00 AM (4*60 = 240 mins) to 11:30 PM (23*60+30 = 1410 mins)
    current_dt = datetime.strptime("04:00", "%H:%M")
    end_dt = datetime.strptime("23:30", "%H:%M")
    while current_dt <= end_dt:
        next_dt = current_dt + timedelta(minutes=30)
        slot_str = f"{current_dt.strftime('%I:%M %p')} - {next_dt.strftime('%I:%M %p')}"
        slots.append(slot_str)
        current_dt = next_dt
    return slots


TIME_SLOTS = generate_time_slots()


@app.get("/api/destinations", response_model=List[str])
def get_destinations():
    """Returns list of popular campus transit hubs."""
    return POPULAR_DESTINATIONS


@app.get("/api/time-slots", response_model=List[str])
def get_time_slots():
    """Returns 30-minute intervals from 04:00 AM to 11:30 PM."""
    return TIME_SLOTS


@app.post("/api/rides", status_code=status.HTTP_201_CREATED)
def post_ride(ride_data: RideCreate):
    """
    Accepts JSON to post a ride request.
    Validates that whatsapp_number contains 10 digits (auto-strips spaces/+91/0).
    Generates a secure cancellation token for zero-friction ownership.
    """
    try:
        cancel_token = secrets.token_hex(6)
        new_ride = create_ride_record(ride_data, cancel_token=cancel_token)
        return {
            "success": True,
            "message": "Ride request posted successfully! You have joined the pod.",
            "ride": new_ride,
            "ride_id": new_ride["id"],
            "cancel_token": cancel_token
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/pods")
def get_pods(
    destination: Optional[str] = Query(None, description="Filter by destination"),
    travel_date: Optional[str] = Query(None, description="Filter by YYYY-MM-DD date")
):
    """
    Accepts optional query params destination and travel_date.
    Groups active requests matching the same (destination, travel_date, time_slot) into 'Pods':
    - Calculate total seats taken (sum of seats_needed).
    - If seats taken >= 3, flag pod as 'Full Pod (3/3)'.
    - Otherwise show 'Looking for {3 - seats_taken} more'.
    - Return grouped pods sorted by date and time slot.
    """
    rides = get_all_rides(destination=destination, travel_date=travel_date)

    # Group rides by key: (destination, travel_date, time_slot)
    grouped: Dict[tuple, List[Dict[str, Any]]] = {}
    for r in rides:
        key = (r["destination"], r["travel_date"], r["time_slot"])
        if key not in grouped:
            grouped[key] = []
        
        # Build WhatsApp personalized direct link with vehicle preference
        clean_num = r["whatsapp_number"]
        dest = r["destination"]
        slot = r["time_slot"]
        v_type = r.get("vehicle_type") or "Either"

        if v_type == "Cab":
            v_tag = "Cab"
            v_word = "a cab"
        elif v_type == "Auto":
            v_tag = "Auto"
            v_word = "an auto"
        else:
            v_tag = "Auto/Cab"
            v_word = "a ride"

        default_msg = f"Hey saw your AutoPool pod ({v_tag}) for {dest} at {slot}. Want to split {v_word}?"
        encoded_msg = urllib.parse.quote(default_msg)
        wa_link = f"https://wa.me/91{clean_num}?text={encoded_msg}"

        rider_dict = dict(r)
        rider_dict.pop("cancel_token", None)  # Ensure cancel_token is never leaked publicly
        rider_dict["vehicle_type"] = v_type
        rider_dict["whatsapp_link"] = wa_link
        grouped[key].append(rider_dict)

    pods = []
    for (dest, date_val, slot), members in grouped.items():
        # Determine dynamic pod capacity based on vehicle preference
        cab_count = sum(1 for m in members if m.get("vehicle_type") == "Cab")
        auto_count = sum(1 for m in members if m.get("vehicle_type") == "Auto")

        if (cab_count > 0 and auto_count == 0) or (cab_count > auto_count):
            max_capacity = 4
            pod_vehicle_type = "Cab"
        elif auto_count > 0 and cab_count == 0:
            max_capacity = 3
            pod_vehicle_type = "Auto"
        else:
            max_capacity = 3
            pod_vehicle_type = "Auto / Either" if auto_count > 0 else "Either"

        total_seats_taken = sum(m["seats_needed"] for m in members)
        is_full = total_seats_taken >= max_capacity
        seats_remaining = max(0, max_capacity - total_seats_taken)

        if is_full:
            status_badge = f"Full Pod ({max_capacity}/{max_capacity})"
        else:
            status_badge = f"Looking for {seats_remaining} more"

        pod = {
            "pod_id": f"{dest}_{date_val}_{slot}".replace(" ", "_"),
            "destination": dest,
            "travel_date": date_val,
            "time_slot": slot,
            "max_capacity": max_capacity,
            "pod_vehicle_type": pod_vehicle_type,
            "total_seats_taken": total_seats_taken,
            "seats_remaining": seats_remaining,
            "is_full": is_full,
            "status_badge": status_badge,
            "members_count": len(members),
            "members": members
        }
        pods.append(pod)

    # Sort pods primarily by travel_date, then time_slot
    # For accurate sorting of time_slot, convert slot start time to 24h representation
    def sort_key(p):
        date_part = p["travel_date"]
        slot_part = p["time_slot"].split(" - ")[0]
        try:
            slot_time = datetime.strptime(slot_part, "%I:%M %p").strftime("%H:%M")
        except Exception:
            slot_time = slot_part
        return (date_part, slot_time, p["destination"])

    pods.sort(key=sort_key)
    return pods


@app.delete("/api/rides/{ride_id}")
def delete_ride(
    ride_id: int,
    token: Optional[str] = Query(None, description="Cancellation token"),
    x_cancel_token: Optional[str] = Header(None, alias="X-Cancel-Token")
):
    """Simple cancellation endpoint with zero-friction device token authorization."""
    existing = get_ride_record(ride_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Ride request not found")

    provided_token = token or x_cancel_token
    expected_token = existing.get("cancel_token")

    # If ride has a cancel_token assigned, strictly verify it
    if expected_token:
        if not provided_token or provided_token.strip() != expected_token.strip():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Unauthorized: You can only cancel rides created on this device"
            )

    success = delete_ride_record(ride_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to cancel ride request")

    return {
        "success": True,
        "message": f"Ride request #{ride_id} cancelled successfully",
        "id": ride_id
    }


# Ensure static directory exists before mounting
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", response_class=FileResponse)
def serve_index():
    """Renders static/index.html at root /."""
    index_path = os.path.join("static", "index.html")
    if not os.path.exists(index_path):
        raise HTTPException(status_code=404, detail="Frontend index.html not found")
    return FileResponse(index_path)

