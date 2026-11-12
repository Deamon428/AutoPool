"""SQLite persistence layer for AutoPool campus ride requests."""

import logging
import os
import sqlite3
from contextlib import closing
from typing import Any, Dict, List, Optional

from schemas import POPULAR_DESTINATIONS, RideCreate, clean_whatsapp_number

logger = logging.getLogger("autopool.models")

DB_FILE: str = os.getenv("AUTOPOOL_DB_PATH", "rides.db")

_PUBLIC_COLUMNS: str = (
    "id, student_name, whatsapp_number, hostel, destination, "
    "travel_date, time_slot, seats_needed, vehicle_type, notes, created_at"
)


def _resolve_db_path(db_path: Optional[str] = None) -> str:
    """Resolve the active SQLite database file path dynamically."""
    return db_path if db_path is not None else DB_FILE


def get_db_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Open a configured SQLite connection with WAL and memory-temp pragmas enabled."""
    resolved_path = _resolve_db_path(db_path)
    conn = sqlite3.connect(resolved_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA temp_store=MEMORY;")
    return conn


def init_db(db_path: Optional[str] = None) -> None:
    """Initialize SQLite tables, backward-compatible columns, and composite indexes."""
    resolved_path = _resolve_db_path(db_path)
    with closing(get_db_connection(resolved_path)) as conn:
        with conn:
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

            cursor.execute("PRAGMA table_info(ride_requests);")
            existing_columns = {row["name"] for row in cursor.fetchall()}

            if "vehicle_type" not in existing_columns:
                cursor.execute(
                    "ALTER TABLE ride_requests ADD COLUMN vehicle_type TEXT DEFAULT 'Either';"
                )
            if "cancel_token" not in existing_columns:
                cursor.execute(
                    "ALTER TABLE ride_requests ADD COLUMN cancel_token TEXT;"
                )

            # Composite indexes optimized for pod filtering, grouping, and sorting
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_rides_lookup "
                "ON ride_requests(travel_date, destination, time_slot);"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_rides_dest_date_slot "
                "ON ride_requests(destination, travel_date, time_slot);"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_rides_date_created "
                "ON ride_requests(travel_date, created_at);"
            )
    logger.debug("SQLite schema and indexes initialized for %s", resolved_path)


def create_ride_record(
    ride_data: RideCreate,
    cancel_token: Optional[str] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Insert a new validated ride request and return its public record fields."""
    resolved_path = _resolve_db_path(db_path)
    with closing(get_db_connection(resolved_path)) as conn:
        with conn:
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
                    ride_data.notes,
                ),
            )
            ride_id = cursor.lastrowid
            cursor.execute(
                f"SELECT {_PUBLIC_COLUMNS} FROM ride_requests WHERE id = ?",
                (ride_id,),
            )
            row = cursor.fetchone()
    if row is None:
        raise sqlite3.DatabaseError("Inserted ride record could not be retrieved")
    result = dict(row)
    result["vehicle_type"] = result.get("vehicle_type") or "Either"
    return result


def get_ride_record(
    ride_id: int, db_path: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Fetch a single ride record by ID including cancel_token for internal ownership checks."""
    resolved_path = _resolve_db_path(db_path)
    with closing(get_db_connection(resolved_path)) as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT {_PUBLIC_COLUMNS}, cancel_token FROM ride_requests WHERE id = ?",
            (ride_id,),
        )
        row = cursor.fetchone()
    return dict(row) if row else None


def delete_ride_record(ride_id: int, db_path: Optional[str] = None) -> bool:
    """Delete a ride record by ID and return True if a row was removed."""
    resolved_path = _resolve_db_path(db_path)
    with closing(get_db_connection(resolved_path)) as conn:
        with conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM ride_requests WHERE id = ?", (ride_id,))
            deleted = cursor.rowcount > 0
    return deleted


def get_all_rides(
    destination: Optional[str] = None,
    travel_date: Optional[str] = None,
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Query public ride records filtered by optional destination and travel date."""
    resolved_path = _resolve_db_path(db_path)
    query = f"SELECT {_PUBLIC_COLUMNS} FROM ride_requests WHERE 1=1"
    params: List[Any] = []

    if destination:
        normalized_dest = destination.strip()
        if normalized_dest.lower() == "all":
            pass
        elif normalized_dest.lower() == "other":
            placeholders = ",".join("?" for _ in POPULAR_DESTINATIONS)
            query += f" AND destination NOT IN ({placeholders})"
            params.extend(POPULAR_DESTINATIONS)
        else:
            query += " AND destination = ?"
            params.append(normalized_dest)

    if travel_date:
        query += " AND travel_date = ?"
        params.append(travel_date.strip())

    query += " ORDER BY travel_date ASC, time_slot ASC, created_at ASC"

    with closing(get_db_connection(resolved_path)) as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()

    results: List[Dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["vehicle_type"] = item.get("vehicle_type") or "Either"
        results.append(item)
    return results


__all__ = [
    "DB_FILE",
    "POPULAR_DESTINATIONS",
    "RideCreate",
    "clean_whatsapp_number",
    "create_ride_record",
    "delete_ride_record",
    "get_all_rides",
    "get_db_connection",
    "get_ride_record",
    "init_db",
]
