import datetime as dt
import math
import os
import shutil
import uuid
from pathlib import Path
import cv2
from fastapi import HTTPException
from sqlalchemy import delete, func, select
from app.core.config import settings
from app.db.models import Camera, Detection, Point, Video
from app.services import workers


def add_cctv(d, name: str, url: str, location: str = "", zone: str = "Public Area", is_restricted: bool = False, dwell_threshold_s: int = 90):
    c = Camera(
        name=name.strip(),
        source=url.strip(),
        type="cctv",
        location=location.strip(),
        zone=zone.strip() or "Public Area",
        is_restricted=bool(is_restricted),
        dwell_threshold_s=int(dwell_threshold_s or 90)
    )
    d.add(c)
    d.commit()
    workers.start(c.id)
    return c


def add_webcam(d, name: str = "Local Webcam", device_idx: int = 0, location: str = "Workstation", zone: str = "Security Checkpoint"):
    c = Camera(
        name=name.strip(),
        source=str(device_idx),
        type="cctv",
        location=location.strip(),
        zone=zone.strip() or "Security Checkpoint",
        is_restricted=False,
        dwell_threshold_s=90
    )
    d.add(c)
    d.commit()
    workers.start(c.id)
    return c


def add_video(d, name: str, started: str, file, location: str = "", loop: bool = True):
    fn = Path(file.filename).name
    path = settings.video_dir / f"{uuid.uuid4().hex[:8]}_{fn}"
    with open(path, "wb") as o:
        shutil.copyfileobj(file.file, o)
        
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    fr = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()

    if fr <= 0:
        if os.path.exists(path):
            os.remove(path)
        raise HTTPException(400, "Could not read this video file")

    try:
        rec = dt.datetime.fromisoformat(started) if started else dt.datetime.now().replace(microsecond=0)
    except ValueError:
        raise HTTPException(400, "Bad recording time")

    c = Camera(
        name=name.strip(),
        source=str(path),
        type="video",
        started=rec,
        location=location.strip(),
        loop=loop
    )
    d.add(c)
    d.flush()

    d.add(
        Video(
            camera_id=c.id,
            filename=fn,
            path=str(path),
            size_mb=round(path.stat().st_size / 1e6, 2),
            fps=fps,
            width=W,
            height=H,
            frames=fr,
            duration_s=round(fr / fps, 1) if fps > 0 else 0,
            recorded_at=rec
        )
    )
    d.commit()
    workers.start(c.id)
    return c


def restart_feed(cid: int):
    return workers.restart(cid)


def toggle_loop(d, cid: int):
    c = d.get(Camera, cid)
    if not c:
        raise HTTPException(404, "Camera not found")
    c.loop = not getattr(c, "loop", True)
    d.commit()
    if cid in workers.WORKERS:
        workers.WORKERS[cid].cam["loop"] = c.loop
    return {"loop": c.loop}


def remove(d, cid: int):
    workers.stop(cid)
    for v in d.scalars(select(Video).where(Video.camera_id == cid)):
        if os.path.exists(v.path):
            try:
                os.remove(v.path)
            except Exception:
                pass
    for m in (Detection, Point, Video):
        d.execute(delete(m).where(m.camera_id == cid))
    d.execute(delete(Camera).where(Camera.id == cid))
    d.commit()


def toggle_point(d, cid: int, x: float, y: float, label: str = ""):
    for p in d.scalars(select(Point).where(Point.camera_id == cid)):
        if math.hypot(p.x - x, p.y - y) < 0.04:
            removed_label = p.label
            d.delete(p)
            d.commit()
            if cid in workers.WORKERS:
                workers.WORKERS[cid].refresh(force=True)
            return {"action": "removed", "label": removed_label}
            
    if not label:
        n = d.scalar(select(func.count(Point.id)).where(Point.camera_id == cid))
        label = f"Zone {chr(65 + n % 26)}{n // 26 or ''}"

    d.add(Point(camera_id=cid, x=x, y=y, label=label.strip()))
    d.commit()
    if cid in workers.WORKERS:
        workers.WORKERS[cid].refresh(force=True)
    return {"action": "added", "label": label}
