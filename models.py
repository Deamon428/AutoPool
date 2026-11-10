import re
import sqlite3
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, field_validator

DB_FILE = "rides.db"

POPULAR_DESTINATIONS = [
    "Patiala Railway Station",
    "New Bus Stand",
    "Rajpura Jn",
    "Chandigarh Airport",
    "Ambala Cantt"
]


def get_db_connection(db_path: str = DB_FILE) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def clean_whatsapp_number(raw_number: str) -> str:
    """Auto-strips spaces, dashes, +91, and leading 0 to validate and return a clean 10-digit number."""
    if not raw_number:
        raise ValueError("WhatsApp number cannot be empty")
    
    # Strip any non-digit character
    digits = re.sub(r"\D", "", raw_number)
    
    # Handle country code +91 or 91 prefix
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
        
    if len(digits) != 10:
        raise ValueError("WhatsApp number must contain exactly 10 digits (e.g. 9876543210)")
        
    return digits


class RideCreate(BaseModel):
    student_name: str = Field(..., min_length=2, max_length=60, description="Full name of student")
    whatsapp_number: str = Field(..., description="10-digit WhatsApp number")
    hostel: str = Field(..., min_length=2, max_length=60, description="Hostel or Campus Gate")
    destination: str = Field(..., description="Target destination")
    travel_date: str = Field(..., description="Travel date in YYYY-MM-DD format")
    time_slot: str = Field(..., description="Time window interval")
    seats_needed: int = Field(1, ge=1, le=3, description="Seats needed (1, 2, or 3)")
    vehicle_type: str = Field("Either", description="Vehicle preference: Auto, Cab, or Either")
    notes: Optional[str] = Field(None, max_length=200, description="Optional notes")

    @field_validator("whatsapp_number")
    @classmethod
    def validate_whatsapp(cls, v: str) -> str:
        return clean_whatsapp_number(v)

    @field_validator("vehicle_type")
    @classmethod
    def validate_vehicle(cls, v: str) -> str:
        if not v:
            return "Either"
        val = v.strip().capitalize()
        if val not in ["Auto", "Cab", "Either"]:
            raise ValueError("Vehicle type must be 'Auto', 'Cab', or 'Either'")
        return val

    @field_validator("travel_date")
    @classmethod
    def validate_date(cls, v: str) -> str:
        try:
            datetime.strptime(v, "%Y-%m-%d")
            return v
        except ValueError:
            raise ValueError("Travel date must be in YYYY-MM-DD format")

    @field_validator("student_name", "hostel")
    @classmethod
    def strip_strings(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Field cannot be blank")
        return cleaned


def init_db(db_path: str = DB_FILE):
    """Initializes the SQLite database schema if it does not exist."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS ride_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_name TEXT NOT NULL,
            whatsapp_number TEXT NOT NULL,
            hostel TEXT NOT NULL,
            destination TEXT NOT NULL,
            travel_date TEXT NOT NULL,
            time_slot TEXT NOT NULL,
            seats_needed INTEGER DEFAULT 1,
            vehicle_type TEXT DEFAULT 'Either',
            cancel_token TEXT,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    # Ensure vehicle_type column exists if table already existed without it
    try:
        cursor.execute("ALTER TABLE ride_requests ADD COLUMN vehicle_type TEXT DEFAULT 'Either'")
        conn.commit()
    except sqlite3.OperationalError:
        pass

    # Ensure cancel_token column exists if table already existed without it
    try:
        cursor.execute("ALTER TABLE ride_requests ADD COLUMN cancel_token TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass

    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_rides_lookup ON ride_requests(travel_date, destination, time_slot);"
    )
    conn.commit()
    conn.close()


def create_ride_record(
    ride_data: RideCreate,
    cancel_token: Optional[str] = None,
    db_path: str = DB_FILE
) -> Dict[str, Any]:
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO ride_requests (
            student_name, whatsapp_number, hostel, destination,
            travel_date, time_slot, seats_needed, vehicle_type, cancel_token, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ride_data.student_name,
            ride_data.whatsapp_number,
            ride_data.hostel,
            ride_data.destination,
            ride_data.travel_date,
            ride_data.time_slot,
            ride_data.seats_needed,
            ride_data.vehicle_type,
            cancel_token,
            ride_data.notes
        )
    )
    conn.commit()
    ride_id = cursor.lastrowid
    cursor.execute("SELECT * FROM ride_requests WHERE id = ?", (ride_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row)


def get_ride_record(ride_id: int, db_path: str = DB_FILE) -> Optional[Dict[str, Any]]:
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM ride_requests WHERE id = ?", (ride_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def delete_ride_record(ride_id: int, db_path: str = DB_FILE) -> bool:
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM ride_requests WHERE id = ?", (ride_id,))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def get_all_rides(
    destination: Optional[str] = None,
    travel_date: Optional[str] = None,
    db_path: str = DB_FILE
) -> List[Dict[str, Any]]:
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    query = "SELECT * FROM ride_requests WHERE 1=1"
    params: List[Any] = []
    
    if destination:
        if destination.lower() == "all":
            pass
        elif destination.lower() == "other":
            placeholders = ",".join("?" for _ in POPULAR_DESTINATIONS)
            query += f" AND destination NOT IN ({placeholders})"
            params.extend(POPULAR_DESTINATIONS)
        else:
            query += " AND destination = ?"
            params.append(destination)
        
    if travel_date:
        query += " AND travel_date = ?"
        params.append(travel_date)
        
    query += " ORDER BY travel_date ASC, time_slot ASC, created_at ASC"
    
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

