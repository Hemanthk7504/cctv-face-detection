import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
ROOT = APP_DIR.parent


class Settings:
    app_name = "FaceTrack CCTV"
    version = "2.0.0"
    root = ROOT
    database_url = os.getenv("DATABASE_URL", f"sqlite:///{ROOT / 'data' / 'facetrack.db'}")
    secret_key = os.getenv("SECRET_KEY", "change-me")
    admin_user = os.getenv("ADMIN_USER", "admin")
    admin_pass = os.getenv("ADMIN_PASS", "admin123")
    nvidia_key = os.getenv("NVIDIA_API_KEY", "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")
    # Official NVIDIA NIM model identifier
    nvidia_model = os.getenv("NVIDIA_MODEL", "nvidia/llama-3.1-nemotron-70b-instruct")
    match_threshold = float(os.getenv("MATCH_THRESHOLD", "0.33"))
    auto_enroll = os.getenv("AUTO_ENROLL", "false").lower() in ("true", "1", "yes")
    video_dir = ROOT / "data" / "videos"
    static_dir = APP_DIR / "static"
    face_dir = APP_DIR / "static" / "faces"
    crops_dir = APP_DIR / "static" / "crops"
    snapshots_dir = APP_DIR / "static" / "snapshots"


settings = Settings()
for p in (settings.video_dir, settings.face_dir, settings.crops_dir, settings.snapshots_dir):
    p.mkdir(parents=True, exist_ok=True)
