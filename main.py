"""FastAPI application entrypoint, HTTP routing, security middleware, and pod aggregation."""

import hmac
import logging
import os
import secrets
import sqlite3
import urllib.parse
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional, Tuple

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

import models
from schemas import (
    ErrorResponse,
    POPULAR_DESTINATIONS,
    PodMember,
    PodResponse,
    RideCreate,
    RideCreateResponse,
    RideDeleteResponse,
    RidePublic,
    TIME_SLOTS,
    generate_time_slots,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("autopool")

BASE_DIR: str = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR: str = os.path.join(BASE_DIR, "static")


def get_db_path() -> str:
    """Resolve the active SQLite database path from environment or models default."""
    return os.getenv("AUTOPOOL_DB_PATH", models.DB_FILE)


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Initialize SQLite schema and indexes cleanly on startup."""
    db_path = get_db_path()
    logger.info("Starting AutoPool server; initializing database at %s", db_path)
    models.init_db(db_path)
    yield
    logger.info("Shutting down AutoPool server")


app = FastAPI(
    title="AutoPool - Campus Ride Pooling",
    description="Split autos and cabs to stations & bus stands without spamming WhatsApp groups",
    version="2.0.0",
    lifespan=lifespan,
)

# Allow cross-origin requests in development without insecure wildcard credentials
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Attach defensive HTTP security headers to all outgoing responses."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    return response


def _build_error_response(
    status_code: int,
    error: str,
    detail: Any,
    headers: Optional[Dict[str, str]] = None,
) -> JSONResponse:
    """Construct a consistent JSON error envelope for all error responses."""
    encoded_detail = jsonable_encoder(detail, custom_encoder={Exception: str})
    payload = ErrorResponse(
        success=False,
        error=error,
        detail=encoded_detail,
        status_code=status_code,
    ).model_dump()
    return JSONResponse(status_code=status_code, content=payload, headers=headers)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """Handle HTTPException errors with a standardized JSON error envelope."""
    logger.warning(
        "HTTP %s on %s %s: %s",
        exc.status_code,
        request.method,
        request.url.path,
        exc.detail,
    )
    return _build_error_response(
        status_code=exc.status_code,
        error="HTTP Error",
        detail=exc.detail,
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Handle Pydantic/FastAPI request validation failures consistently."""
    logger.warning(
        "Validation error on %s %s: %d error(s)",
        request.method,
        request.url.path,
        len(exc.errors()),
    )
    return _build_error_response(
        status_code=getattr(status, "HTTP_422_UNPROCESSABLE_CONTENT", 422),
        error="Validation Error",
        detail=exc.errors(),
    )


@app.exception_handler(sqlite3.Error)
async def sqlite_exception_handler(
    request: Request, exc: sqlite3.Error
) -> JSONResponse:
    """Log SQLite errors internally without leaking database details to clients."""
    logger.exception(
        "Database error while handling %s %s", request.method, request.url.path
    )
    return _build_error_response(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        error="Database Error",
        detail="A database error occurred while processing your request.",
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """Catch unexpected server errors and return a sanitized 500 JSON envelope."""
    logger.exception(
        "Unhandled server error on %s %s", request.method, request.url.path
    )
    return _build_error_response(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        error="Internal Server Error",
        detail="An unexpected internal error occurred.",
    )


def _build_whatsapp_link(
    phone: str, destination: str, time_slot: str, vehicle_type: str
) -> str:
    """Build an encoded WhatsApp direct-message deep link with vehicle preference."""
    if vehicle_type == "Cab":
        v_tag, v_word = "Cab", "a cab"
    elif vehicle_type == "Auto":
        v_tag, v_word = "Auto", "an auto"
    else:
        v_tag, v_word = "Auto/Cab", "a ride"

    message = (
        f"Hey saw your AutoPool pod ({v_tag}) for {destination} "
        f"at {time_slot}. Want to split {v_word}?"
    )
    encoded_msg = urllib.parse.quote(message, safe="")
    return f"https://wa.me/91{phone}?text={encoded_msg}"


def _build_pod_summary(
    dest: str, date_val: str, slot: str, members: List[PodMember]
) -> PodResponse:
    """Aggregate members into a PodResponse with dynamic Auto/Cab capacity."""
    cab_count = sum(1 for member in members if member.vehicle_type == "Cab")
    auto_count = sum(1 for member in members if member.vehicle_type == "Auto")

    if (cab_count > 0 and auto_count == 0) or (cab_count > auto_count):
        max_capacity = 4
        pod_vehicle_type = "Cab"
    elif auto_count > 0 and cab_count == 0:
        max_capacity = 3
        pod_vehicle_type = "Auto"
    else:
        max_capacity = 3
        pod_vehicle_type = "Auto / Either" if auto_count > 0 else "Either"

    total_seats_taken = sum(member.seats_needed for member in members)
    is_full = total_seats_taken >= max_capacity
    seats_remaining = max(0, max_capacity - total_seats_taken)

    if is_full:
        status_badge = f"Full Pod ({max_capacity}/{max_capacity})"
    else:
        status_badge = f"Looking for {seats_remaining} more"

    return PodResponse(
        pod_id=f"{dest}_{date_val}_{slot}".replace(" ", "_"),
        destination=dest,
        travel_date=date_val,
        time_slot=slot,
        max_capacity=max_capacity,
        pod_vehicle_type=pod_vehicle_type,
        total_seats_taken=total_seats_taken,
        seats_remaining=seats_remaining,
        is_full=is_full,
        status_badge=status_badge,
        members_count=len(members),
        members=members,
    )


def _pod_sort_key(pod: PodResponse) -> Tuple[str, str, str]:
    """Sort pods chronologically by travel_date, start time, and destination."""
    slot_start = pod.time_slot.split(" - ")[0].strip()
    try:
        slot_24h = datetime.strptime(slot_start, "%I:%M %p").strftime("%H:%M")
    except ValueError:
        slot_24h = slot_start
    return (pod.travel_date, slot_24h, pod.destination)


@app.get("/api/destinations", response_model=List[str])
def get_destinations() -> List[str]:
    """Return the list of popular campus transit hubs."""
    return POPULAR_DESTINATIONS


@app.get("/api/time-slots", response_model=List[str])
def get_time_slots() -> List[str]:
    """Return 30-minute departure windows from 04:00 AM to 11:30 PM."""
    return TIME_SLOTS


@app.post(
    "/api/rides",
    response_model=RideCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
def post_ride(
    ride_data: RideCreate,
    db_path: str = Depends(get_db_path),
) -> RideCreateResponse:
    """Create a validated ride request and return a device cancel_token."""
    cancel_token = secrets.token_hex(16)
    new_ride = models.create_ride_record(
        ride_data, cancel_token=cancel_token, db_path=db_path
    )
    logger.info(
        "Created ride_id=%s destination=%s date=%s vehicle=%s",
        new_ride["id"],
        new_ride["destination"],
        new_ride["travel_date"],
        new_ride["vehicle_type"],
    )
    return RideCreateResponse(
        success=True,
        message="Ride request posted successfully! You have joined the pod.",
        ride=RidePublic(**new_ride),
        ride_id=new_ride["id"],
        cancel_token=cancel_token,
    )


@app.get("/api/pods", response_model=List[PodResponse])
def get_pods(
    destination: Optional[str] = Query(
        default=None, max_length=100, description="Filter by destination or 'Other'"
    ),
    travel_date: Optional[str] = Query(
        default=None, max_length=10, description="Filter by YYYY-MM-DD travel date"
    ),
    db_path: str = Depends(get_db_path),
) -> List[PodResponse]:
    """Group active ride requests into destination/date/slot pods sorted chronologically."""
    clean_dest = destination.strip() if destination and destination.strip() else None
    clean_date = travel_date.strip() if travel_date and travel_date.strip() else None

    rides = models.get_all_rides(
        destination=clean_dest, travel_date=clean_date, db_path=db_path
    )

    grouped: Dict[Tuple[str, str, str], List[PodMember]] = {}
    for row in rides:
        key = (row["destination"], row["travel_date"], row["time_slot"])
        v_type = row.get("vehicle_type") or "Either"
        wa_link = _build_whatsapp_link(
            row["whatsapp_number"], row["destination"], row["time_slot"], v_type
        )
        member = PodMember(
            id=row["id"],
            student_name=row["student_name"],
            whatsapp_number=row["whatsapp_number"],
            hostel=row["hostel"],
            destination=row["destination"],
            travel_date=row["travel_date"],
            time_slot=row["time_slot"],
            seats_needed=row["seats_needed"],
            vehicle_type=v_type,
            notes=row.get("notes"),
            created_at=row.get("created_at"),
            whatsapp_link=wa_link,
        )
        grouped.setdefault(key, []).append(member)

    pods = [
        _build_pod_summary(dest, date_val, slot, members)
        for (dest, date_val, slot), members in grouped.items()
    ]
    pods.sort(key=_pod_sort_key)
    return pods


@app.delete("/api/rides/{ride_id}", response_model=RideDeleteResponse)
def delete_ride(
    ride_id: int,
    token: Optional[str] = Query(default=None, description="Cancellation token"),
    x_cancel_token: Optional[str] = Header(default=None, alias="X-Cancel-Token"),
    db_path: str = Depends(get_db_path),
) -> RideDeleteResponse:
    """Cancel an existing ride after constant-time ownership token verification."""
    existing = models.get_ride_record(ride_id, db_path=db_path)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ride request not found",
        )

    provided_token = (token or x_cancel_token or "").strip()
    expected_token = (existing.get("cancel_token") or "").strip()

    if not expected_token or not provided_token or not hmac.compare_digest(
        provided_token.encode("utf-8"),
        expected_token.encode("utf-8"),
    ):
        logger.warning("Unauthorized cancel attempt on ride_id=%s", ride_id)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Unauthorized: You can only cancel rides created on this device",
        )

    deleted = models.delete_ride_record(ride_id, db_path=db_path)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to cancel ride request",
        )

    logger.info("Cancelled ride_id=%s", ride_id)
    return RideDeleteResponse(
        success=True,
        message=f"Ride request #{ride_id} cancelled successfully",
        id=ride_id,
    )


os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=FileResponse)
def serve_index() -> FileResponse:
    """Serve the mobile-first single-page frontend application."""
    index_path = os.path.join(STATIC_DIR, "index.html")
    if not os.path.exists(index_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Frontend index.html not found",
        )
    return FileResponse(index_path)


__all__ = [
    "TIME_SLOTS",
    "app",
    "generate_time_slots",
    "get_db_path",
]
