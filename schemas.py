"""Pydantic v2 validation and serialization schemas for AutoPool."""

import re
from datetime import datetime, timedelta
from typing import Any, List, Literal, Optional, Tuple
from pydantic import BaseModel, ConfigDict, Field, field_validator

POPULAR_DESTINATIONS: List[str] = [
    "Patiala Railway Station",
    "New Bus Stand",
    "Rajpura Jn",
    "Chandigarh Airport",
    "Ambala Cantt",
]

VALID_VEHICLE_TYPES: Tuple[str, ...] = ("Auto", "Cab", "Either")


def generate_time_slots() -> List[str]:
    """Generate 30-minute departure windows from 04:00 AM to 11:30 PM."""
    slots: List[str] = []
    current_dt = datetime.strptime("04:00", "%H:%M")
    end_dt = datetime.strptime("23:30", "%H:%M")
    while current_dt <= end_dt:
        next_dt = current_dt + timedelta(minutes=30)
        slots.append(
            f"{current_dt.strftime('%I:%M %p')} - {next_dt.strftime('%I:%M %p')}"
        )
        current_dt = next_dt
    return slots


TIME_SLOTS: List[str] = generate_time_slots()


def clean_whatsapp_number(raw_number: str) -> str:
    """Strip non-digits, +91, and leading 0 prefixes to normalize a 10-digit number."""
    if not raw_number:
        raise ValueError("WhatsApp number cannot be empty")

    digits = re.sub(r"\D", "", raw_number)

    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]

    if len(digits) != 10:
        raise ValueError(
            "WhatsApp number must contain exactly 10 digits (e.g. 9876543210)"
        )

    return digits


class RideCreate(BaseModel):
    """Request schema for creating a new campus ride-pool entry."""

    model_config = ConfigDict(str_strip_whitespace=True)

    student_name: str = Field(
        ...,
        min_length=2,
        max_length=60,
        description="Full name of the student",
    )
    whatsapp_number: str = Field(
        ...,
        min_length=10,
        max_length=25,
        description="10-digit WhatsApp number (auto-strips spaces and +91/0)",
    )
    hostel: str = Field(
        ...,
        min_length=2,
        max_length=60,
        description="Campus hostel or gate pickup point",
    )
    destination: str = Field(
        ...,
        min_length=2,
        max_length=100,
        description="Preset transit hub or custom destination",
    )
    travel_date: str = Field(
        ...,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="Travel date in YYYY-MM-DD format",
    )
    time_slot: str = Field(
        ...,
        min_length=5,
        max_length=50,
        description="Departure time window interval",
    )
    seats_needed: int = Field(
        default=1,
        ge=1,
        le=3,
        description="Number of seats needed (1, 2, or 3)",
    )
    vehicle_type: Literal["Auto", "Cab", "Either"] = Field(
        default="Either",
        description="Vehicle preference: Auto, Cab, or Either",
    )
    notes: Optional[str] = Field(
        default=None,
        max_length=200,
        description="Optional luggage or train details",
    )

    @field_validator("whatsapp_number")
    @classmethod
    def validate_whatsapp(cls, value: str) -> str:
        """Validate and normalize the WhatsApp phone number to 10 digits."""
        return clean_whatsapp_number(value)

    @field_validator("vehicle_type", mode="before")
    @classmethod
    def validate_vehicle(cls, value: Any) -> str:
        """Normalize and validate vehicle category selection."""
        if not isinstance(value, str):
            raise ValueError("Vehicle type must be 'Auto', 'Cab', or 'Either'")
        normalized = value.strip().capitalize()
        if normalized not in VALID_VEHICLE_TYPES:
            raise ValueError("Vehicle type must be 'Auto', 'Cab', or 'Either'")
        return normalized

    @field_validator("travel_date")
    @classmethod
    def validate_date(cls, value: str) -> str:
        """Ensure travel_date represents a real calendar date in YYYY-MM-DD format."""
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError(
                "Travel date must be a valid calendar date in YYYY-MM-DD format"
            ) from exc
        return value

    @field_validator("notes", mode="before")
    @classmethod
    def normalize_notes(cls, value: Any) -> Optional[str]:
        """Convert blank whitespace notes to None."""
        if value is None:
            return None
        if isinstance(value, str):
            cleaned = value.strip()
            return cleaned if cleaned else None
        raise ValueError("Notes must be text")


class RidePublic(BaseModel):
    """Public representation of a ride request excluding private cancellation tokens."""

    id: int = Field(..., ge=1, description="Unique ride record ID")
    student_name: str
    whatsapp_number: str
    hostel: str
    destination: str
    travel_date: str
    time_slot: str
    seats_needed: int = Field(..., ge=1, le=3)
    vehicle_type: Literal["Auto", "Cab", "Either"] = "Either"
    notes: Optional[str] = None
    created_at: Optional[str] = None


class PodMember(RidePublic):
    """Ride member enriched with a direct WhatsApp sync deep-link."""

    whatsapp_link: str = Field(..., description="Encoded wa.me deep-link URL")


class PodResponse(BaseModel):
    """Aggregated ride-sharing pod grouped by destination, date, and time window."""

    pod_id: str
    destination: str
    travel_date: str
    time_slot: str
    max_capacity: int = Field(..., ge=3, le=4)
    pod_vehicle_type: str
    total_seats_taken: int = Field(..., ge=0)
    seats_remaining: int = Field(..., ge=0)
    is_full: bool
    status_badge: str
    members_count: int = Field(..., ge=1)
    members: List[PodMember]


class RideCreateResponse(BaseModel):
    """API response returned after successfully creating a ride request."""

    success: bool = True
    message: str
    ride: RidePublic
    ride_id: int = Field(..., ge=1)
    cancel_token: str = Field(..., min_length=12)


class RideDeleteResponse(BaseModel):
    """API response returned after cancelling a ride request."""

    success: bool = True
    message: str
    id: int = Field(..., ge=1)


class ErrorResponse(BaseModel):
    """Consistent JSON error envelope returned by centralized exception handlers."""

    success: bool = False
    error: str
    detail: Any
    status_code: int = Field(..., ge=400, le=599)

