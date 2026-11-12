# AutoPool 🛺

**Campus ride-pooling platform for Thapar Institute of Engineering & Technology (TIET), Patiala** — built with **FastAPI**, **Pydantic v2**, **SQLite (WAL)**, and an accessible, mobile-first **Tailwind CSS** frontend.

---

## 🎯 Campus Problem Solved (Thapar University)

Every semester break, weekend, and evening train departure, hundreds of Thapar University students leave from campus hostels (**Hostel J, Hostel H, Hostel B, Hostel N, Hostel PG, and Main Gate**) toward **Patiala Railway Station**, **New Bus Stand**, **Rajpura Jn**, **Chandigarh Airport**, and **Ambala Cantt**.

Currently, students coordinate rides by spamming unstructured text messages across dozens of hostel and batch WhatsApp groups:
- Messages scroll out of sight within minutes, causing two students leaving from the exact same hostel in the same 30-minute window to miss each other.
- Students end up paying **₹150–₹250 solo auto fares** instead of splitting **₹50–₹70 per person** in a 3-seat auto or 4-seat cab.
- Group members get spammed with irrelevant departure broadcasts even when not travelling.

**AutoPool** replaces chat spam with an indexed, real-time departure board that automatically groups students travelling to the same destination on the same date and 30-minute window into shareable **Pods**, tracks live seat capacity (`3/3` for Autos, `4/4` for Cabs), and connects matched peers via **1-tap pre-filled WhatsApp links**.

---

## 🔐 Architectural Justification: Zero-Friction Device Tokens vs. Mandatory Auth

AutoPool deliberately avoids mandatory registration, passwords, email OTPs, or university SSO walls in favor of **cryptographic zero-friction device tokens**:

1. **Speed During Urgent Transit**: A student rushing to catch a train at Patiala Railway Station or Rajpura Jn needs to check open pods or post a ride in **under 15 seconds**. Login walls cause immediate drop-off back to WhatsApp spam.
2. **Scoped Ownership Without Accounts**: When a student posts a ride via `POST /api/rides`, the backend generates a cryptographically random 128-bit `cancel_token` (`secrets.token_hex(16)`), persists it server-side, and returns it once in the creation response.
3. **Browser-Local Custody**: The frontend saves `{ [ride_id]: cancel_token }` in `localStorage['autopool_my_rides']` and only renders cancellation controls on rides owned by that device.
4. **Public Token Stripping & Constant-Time Verification**: `GET /api/pods` and SQL list projections exclude `cancel_token` completely so tokens are never leaked in the public feed. `DELETE /api/rides/{ride_id}` verifies ownership using `hmac.compare_digest()` and fails closed with `HTTP 403 Forbidden` on missing or mismatched tokens.

---

## 🏗️ Architecture & Engineering Highlights

```
.
├── main.py              # FastAPI routing, security headers middleware, structured logging & exception envelopes
├── schemas.py           # Pydantic v2 BaseModels, Field constraints, domain validators & response contracts
├── models.py            # Parameterized SQLite persistence layer, WAL pragmas, migrations & composite indexes
├── static/
│   ├── index.html       # WCAG 2.1 AA compliant dark-theme UI, XSS-safe DOM rendering & 250ms debounced search
│   └── manifest.json    # Mobile PWA manifest
├── tests/
│   ├── __init__.py
│   └── test_api.py      # Isolated pytest + TestClient suite using tmp_path temporary SQLite DBs
└── requirements.txt     # Runtime and test dependencies
```

### Engineering Guardrails
- **Code Quality**: Clean separation across validation (`schemas.py`), persistence (`models.py`), and HTTP routing (`main.py`), full type annotations, structured Python `logging`, and centralized JSON error envelopes (`success`, `error`, `detail`, `status_code`).
- **Security Hardening**:
  - Zero raw `innerHTML` interpolation; safe DOM construction via `textContent`/`createElement` paired with strict `escapeHTML(str)` helper and `https://wa.me/` URL validation.
  - Constant-time `hmac.compare_digest()` verification for cancellation tokens.
  - 100% parameterized SQL `?` placeholders.
  - Defensive HTTP headers on all responses: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`.
- **Database & UI Efficiency**:
  - SQLite tuned with `PRAGMA journal_mode=WAL;`, `PRAGMA synchronous=NORMAL;`, `PRAGMA temp_store=MEMORY;`.
  - Composite indexes on `(travel_date, destination, time_slot)`, `(destination, travel_date, time_slot)`, and `(travel_date, created_at)`.
  - 250ms debounced live search/date filtering with `AbortController` request cancellation.
- **WCAG 2.1 AA Accessibility**:
  - Full landmark hierarchy (`<header>`, `<main>`, `<nav>`, `<section>`), skip link, explicit `<label for="...">` bindings on every input/select, `<fieldset>`/`<legend>` radio groups, `aria-label` on all icon buttons, `aria-live="polite"` feed/toast regions, modal focus trap + `Escape` dismissal + focus restoration, visible `:focus-visible` outlines, and zoom-enabled viewport metadata.

---

## 🚀 Local Setup & Render Deployment

### 1. Local Development
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```
Open [http://localhost:8000](http://localhost:8000) in your browser.

Optional environment variable:
- `AUTOPOOL_DB_PATH`: Override the SQLite database file path (defaults to `rides.db`).

### 2. Render Production Deployment
Render Start Command:
```bash
uvicorn main:app --host 0.0.0.0 --port $PORT
```
*(Install command: `pip install -r requirements.txt`)*

---

## 🧪 Running the Automated Test Suite

Tests run against isolated per-test SQLite databases via `pytest`'s `tmp_path` fixture so production data (`rides.db`) is never modified:

```bash
pytest -v
```

---

## 📡 API Documentation

Interactive OpenAPI/Swagger documentation is available at `/docs` when running the server.

| Method | Endpoint | Description | Success Code |
|---|---|---|---|
| `GET` | `/` | Serves accessible mobile-first `static/index.html` | `200 OK` |
| `GET` | `/api/destinations` | Returns preset campus transit hubs | `200 OK` |
| `GET` | `/api/time-slots` | Returns 40 thirty-minute departure windows (`04:00 AM`–`12:00 AM`) | `200 OK` |
| `POST` | `/api/rides` | Validates ride payload, normalizes 10-digit phone, issues `cancel_token` | `201 Created` |
| `GET` | `/api/pods` | Groups rides into pods filtered by optional `destination` (`All`, `Other`, hub) & `travel_date` (`YYYY-MM-DD`) | `200 OK` |
| `DELETE` | `/api/rides/{ride_id}` | Cancels ride after verifying `X-Cancel-Token` header or `?token=` query param (`403` if invalid/missing, `404` if not found) | `200 OK` |

### `POST /api/rides` Request Body
```json
{
  "student_name": "Aarav Sharma",
  "whatsapp_number": "+91 98765-43210",
  "hostel": "Hostel J",
  "destination": "Patiala Railway Station",
  "travel_date": "2026-11-15",
  "time_slot": "04:00 PM - 04:30 PM",
  "seats_needed": 1,
  "vehicle_type": "Auto",
  "notes": "Catching Shatabdi, 1 suitcase"
}
```

### Error Envelope Format (`403`, `404`, `422`, `500`)
```json
{
  "success": false,
  "error": "Validation Error",
  "detail": "...",
  "status_code": 422
}
```
