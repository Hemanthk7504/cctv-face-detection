# FaceTrack AI - Advanced CCTV Facial Surveillance & Intelligence System

FaceTrack AI is a real-time, multi-camera intelligent CCTV surveillance and biometric facial recognition system built on FastAPI, OpenCV (YuNet + SFace), SQLite WAL mode, and NVIDIA Nemotron LLM intelligence.

---

## Key Features

- **Real-Time Facial Recognition HUD:** Dual-badge video overlays rendering identity name, role, match confidence, job title, department, badge number, and access level directly on camera feeds.
- **Multi-Sample / Multi-Angle Face Training:** Enroll multiple angle portraits per subject (Frontal, Left Profile, Right Profile, Overhead CCTV) with 128D cosine similarity matching for precision recognition across camera perspectives.
- **Dedicated Security Alerts Center (`/alerts`):** Zero popup toast clutter; automated triage with filters for Blacklist detections, Restricted Zone crossings, and Loitering alarms.
- **Multi-Camera Matrix View (`/live`):** Live streaming matrix (1x1, 2x2, 3x3 layouts) with a real-time Biometric Identity Stream displaying live visitor and staff dossier cards.
- **Dossier & Forensics Hub (`/people/{id}/dossier`):** Comprehensive printable biometric dossiers, sightings timelines, confidence analytics, and one-click filtered forensic CSV export.
- **NVIDIA Nemotron AI Security Analyst (`/ask`):** Natural language security intelligence querying powered by `nvidia/nemotron-3-super-120b-a12b`.
- **High-Concurrency SQLite WAL Engine:** Multi-worker thread protection using SQLite Write-Ahead Logging (`WAL`), 30-second busy timeout, and anti-spam detection debouncing.
- **Executive Daylight Theme:** High-contrast, clean daylight UI with no dark theme fatigue.

---

## Architecture & Directory Layout

```
├── app/
│   ├── main.py             # FastAPI application lifecycle and middleware
│   ├── core/               # Configuration, security, and Jinja2 templating
│   ├── db/                 # Models (Person, PersonEmbedding, Sighting, Alert, Camera) & Session (WAL mode)
│   ├── schemas/            # Pydantic schemas for REST API
│   ├── routes/             # Web pages (/live, /alerts, /people, /ask) and REST APIs (/api/v1)
│   ├── services/           # YuNet detector, SFace recognizer, background worker threads, NVIDIA LLM
│   ├── templates/          # Responsive Jinja2 templates in Daylight theme
│   └── static/             # CSS styling, face portrait gallery, forensic crops, snapshots
├── data/                   # Video assets and local SQLite storage
├── tests/                  # Automated integration and API test suite
├── run.py                  # Auto port-binding launcher
└── requirements.txt        # Python package dependencies
```

---

## Quickstart Guide

### 1. Clone & Set Up Environment

```bash
git clone https://github.com/Hemanthk7504/cctv-face-detection.git
cd cctv-face-detection

python -m venv venv
venv\Scripts\activate   # Windows
# or: source venv/bin/activate  # Linux/macOS

pip install -r requirements.txt
```

### 2. Configure Environment Variables

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

Set your NVIDIA API key for the AI Analyst:
```env
NVIDIA_API_KEY=nvapi-your-key-here
```

### 3. Launch Surveillance System

```bash
python run.py
```

The system automatically binds to `http://localhost:8000` (or `http://localhost:8001` if port 8000 is occupied).

---

## Endpoints Overview

- **Dashboard:** `http://localhost:8001/`
- **Live Matrix & Biometric Stream:** `http://localhost:8001/live`
- **Security Alerts Center:** `http://localhost:8001/alerts`
- **Personnel & Biometric Directory:** `http://localhost:8001/people`
- **Person Profile & Angle Training:** `http://localhost:8001/people/{id}`
- **Printable Dossier:** `http://localhost:8001/people/{id}/dossier`
- **Sightings Audit Log:** `http://localhost:8001/sightings`
- **NVIDIA AI Analyst:** `http://localhost:8001/ask`
- **Interactive REST API Docs:** `http://localhost:8001/docs`

---

## License

MIT License. Designed for enterprise security teams and CCTV operators.
