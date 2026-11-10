# AutoPool 🛺

Campus ride-pooling web application built with **FastAPI**, **SQLite**, and a **mobile-first Tailwind CSS frontend**.

Split autos to the railway station and bus stand without spamming WhatsApp groups!

---

## 🚀 Features

- **High-Contrast Dark Theme**: Designed in zinc-950 with emerald and indigo accents, optimized for night browsing and mobile screens.
- **Smart Pod Grouping & Dynamic Capacity**: Automatically groups ride requests by `(destination, travel_date, time_slot)`. Dynamically adjusts pod capacity: 4 seats for Cabs, 3 seats for Autos/Either.
- **Vehicle Type Preference**: Supports `🛺 Auto`, `🚕 Cab`, and `🔀 Either / Any` preferences, visible on rider cards and pre-filled in WhatsApp sync messages.
- **Custom "Other" Destination Support**: Preset hubs (`Patiala Railway Station`, `New Bus Stand`, `Rajpura Jn`, `Chandigarh Airport`, `Ambala Cantt`) plus a custom destination input for any campus spot or city location (`Elante Mall`, `Zirakpur`, etc.) with quick `[📍 Other]` filter tab.
- **Capacity Tracking**: Displays live seat status (`Full Pod (4/4)` or `Full Pod (3/3)` vs `Looking for X more`) with visual auto/cab seat indicators.
- **1-Tap WhatsApp Direct Linking**: Instant chat links (`https://wa.me/91...`) pre-filled with vehicle preference, destination, and departure time so riders can coordinate in one click.
- **Campus Quick Filters**:
  - Hub pills: `Patiala Railway Station`, `New Bus Stand`, `Rajpura Jn`, `Chandigarh Airport`, `Ambala Cantt`.
  - Date toggles: `Today`, `Tomorrow`, `All Dates`, and custom calendar picker.
- **Phone Validation**: Automatically strips `+91`, leading `0`, spaces, and dashes, guaranteeing clean 10-digit numbers.
- **Ride Cancellation**: 1-click delete/cancel option for existing ride requests.

---

## 📁 Project Structure

```
.
├── main.py              # FastAPI app, API routing, pod grouping logic, and static file serving
├── models.py            # SQLite schema initialization, Pydantic validation, and DB operations
├── static/
│   └── index.html       # Responsive dark-theme frontend with Tailwind CSS
├── requirements.txt     # Python dependencies (fastapi, uvicorn)
└── rides.db             # SQLite database storing ride requests
```

---

## 🛠️ Quickstart

### 1. Install dependencies
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Run the application
```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### 3. Open in Browser
Open [http://localhost:8000](http://localhost:8000) on your desktop or phone browser.

---

## 📡 API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/` | Serves mobile-first `static/index.html` |
| `GET` | `/api/destinations` | List of popular campus transit hubs |
| `GET` | `/api/time-slots` | 30-minute intervals from 04:00 AM to 11:30 PM |
| `POST` | `/api/rides` | Post a ride request (validates 10-digit WhatsApp number) |
| `GET` | `/api/pods` | Grouped pods filtered by `destination` and `travel_date` |
| `DELETE` | `/api/rides/{ride_id}` | Cancel/delete an active ride request |

