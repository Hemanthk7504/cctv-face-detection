import csv
import io
import os
import psutil
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.core.config import settings
from app.db.models import Alert, Camera, Detection, Person, Video
from app.db.session import get_db
from app.schemas.camera import CameraCreate, CameraOut, PointResult, PointToggle, VideoOut, WebcamCreate
from app.schemas.detection import AskIn, AskOut, DetectionOut, StatsOut
from app.schemas.person import PersonOut, PersonUpdate, PersonStats, Stay
from app.services import camera_service as cams, events, llm, queries, recognition as R, workers

router = APIRouter(prefix="/api/v1", tags=["REST API"])


def need(d, model, id_):
    obj = d.get(model, id_)
    if not obj:
        raise HTTPException(404, f"{model.__name__} not found")
    return obj


@router.get("/health")
def health():
    return {"status": "ok", "workers": len(workers.WORKERS)}


@router.get("/stats", response_model=StatsOut)
def stats(d: Session = Depends(get_db)):
    return queries.stats(d)


@router.get("/cameras", response_model=list[CameraOut])
def list_cameras(d: Session = Depends(get_db)):
    cam_list = d.scalars(select(Camera)).all()
    results = []
    for c in cam_list:
        w = workers.WORKERS.get(c.id)
        results.append(
            CameraOut(
                id=c.id,
                name=c.name,
                type=c.type,
                source=c.source,
                started=c.started,
                location=getattr(c, "location", "") or "",
                zone=getattr(c, "zone", "Public Area") or "Public Area",
                is_restricted=bool(getattr(c, "is_restricted", False)),
                dwell_threshold_s=int(getattr(c, "dwell_threshold_s", 90) or 90),
                loop=getattr(c, "loop", True),
                status=w.status if w else "offline",
                fps=w.fps if w else 0.0,
                resolution=w.resolution if w else "N/A",
                active_faces=w.active_faces if w else 0
            )
        )
    return results


@router.post("/cameras", response_model=CameraOut, status_code=201)
def create_camera(body: CameraCreate, d: Session = Depends(get_db)):
    c = cams.add_cctv(
        d,
        name=body.name,
        url=body.url,
        location=body.location,
        zone=body.zone,
        is_restricted=body.is_restricted,
        dwell_threshold_s=body.dwell_threshold_s
    )
    return CameraOut(
        id=c.id,
        name=c.name,
        type=c.type,
        source=c.source,
        started=c.started,
        location=c.location,
        zone=c.zone,
        is_restricted=c.is_restricted,
        dwell_threshold_s=c.dwell_threshold_s,
        loop=True,
        status="live"
    )


@router.post("/cameras/webcam", response_model=CameraOut, status_code=201)
def create_webcam(body: WebcamCreate, d: Session = Depends(get_db)):
    c = cams.add_webcam(d, body.name, body.device_index, body.location)
    return CameraOut(
        id=c.id,
        name=c.name,
        type=c.type,
        source=c.source,
        started=c.started,
        location=c.location,
        loop=True,
        status="live"
    )


@router.post("/cameras/{cid}/restart")
def restart_camera(cid: int, d: Session = Depends(get_db)):
    need(d, Camera, cid)
    cams.restart_feed(cid)
    return {"status": "restarted", "cid": cid}


@router.post("/cameras/{cid}/toggle_loop")
def toggle_loop(cid: int, d: Session = Depends(get_db)):
    need(d, Camera, cid)
    return cams.toggle_loop(d, cid)


@router.delete("/cameras/{cid}", status_code=204)
def delete_camera(cid: int, d: Session = Depends(get_db)):
    need(d, Camera, cid)
    cams.remove(d, cid)


@router.post("/cameras/{cid}/points/toggle", response_model=PointResult)
def toggle_point(cid: int, body: PointToggle, d: Session = Depends(get_db)):
    need(d, Camera, cid)
    return cams.toggle_point(d, cid, body.x, body.y, body.label)


@router.get("/videos", response_model=list[VideoOut])
def list_videos(d: Session = Depends(get_db)):
    return d.scalars(select(Video)).all()


@router.get("/people", response_model=list[PersonStats])
def list_people(d: Session = Depends(get_db)):
    return queries.people_rows(d)


@router.patch("/people/{pid}", response_model=PersonOut)
def update_person(pid: int, body: PersonUpdate, d: Session = Depends(get_db)):
    p = need(d, Person, pid)
    if body.name is not None:
        p.name = body.name.strip()
    if body.role is not None:
        p.role = body.role.strip()
    if body.department is not None:
        p.department = body.department.strip()
    if body.job_title is not None:
        p.job_title = body.job_title.strip()
    if body.badge_number is not None:
        p.badge_number = body.badge_number.strip()
    if body.access_level is not None:
        p.access_level = body.access_level.strip()
    if body.phone is not None:
        p.phone = body.phone.strip()
    if body.email is not None:
        p.email = body.email.strip()
    if body.gender is not None:
        p.gender = body.gender.strip()
    if body.threat_level is not None:
        p.threat_level = body.threat_level.strip()
    if body.notes is not None:
        p.notes = body.notes.strip()
    d.commit()
    # Refresh worker cached names
    for w in workers.WORKERS.values():
        w.refresh(force=True)
    return p


@router.post("/people/{pid}/add-sample")
async def add_sample(
    pid: int,
    file: UploadFile = File(...),
    label: str = Form("Additional Angle"),
    d: Session = Depends(get_db)
):
    """Enrolls an additional face sample/angle photo for this person to maximize CCTV detection accuracy."""
    need(d, Person, pid)
    content = await file.read()
    if not content:
        raise HTTPException(400, "Empty photo file uploaded")
    try:
        sample = R.add_face_sample(pid, content, label=label.strip())
        for w in workers.WORKERS.values():
            w.refresh(force=True)
        return {"status": "success", "sample": sample}
    except ValueError as ve:
        raise HTTPException(400, str(ve))
    except Exception as e:
        raise HTTPException(500, f"Error adding face sample: {str(e)}")


@router.post("/people/enroll-photo")
async def enroll_photo(
    file: UploadFile = File(...),
    name: str = Form(""),
    role: str = Form("Visitor"),
    department: str = Form(""),
    job_title: str = Form(""),
    badge_number: str = Form(""),
    access_level: str = Form("Standard Access"),
    phone: str = Form(""),
    email: str = Form(""),
    gender: str = Form(""),
    threat_level: str = Form("Low"),
    notes: str = Form("")
):
    """Enrolls a new person from an uploaded portrait image file with full identity metadata."""
    content = await file.read()
    if not content:
        raise HTTPException(400, "Empty photo file uploaded")
    try:
        res = R.enroll_from_image(
            content,
            name=name.strip(),
            role=role.strip() or "Visitor",
            department=department.strip(),
            job_title=job_title.strip(),
            badge_number=badge_number.strip(),
            access_level=access_level.strip() or "Standard Access",
            phone=phone.strip(),
            email=email.strip(),
            gender=gender.strip(),
            threat_level=threat_level.strip() or "Low",
            notes=notes.strip()
        )
        # Refresh worker references
        for w in workers.WORKERS.values():
            w.refresh(force=True)
        return {"status": "success", "person": res}
    except ValueError as ve:
        raise HTTPException(400, str(ve))
    except Exception as e:
        raise HTTPException(500, f"Enrollment error: {str(e)}")


@router.post("/cameras/{cid}/snapshot")
def capture_camera_snapshot(cid: int, d: Session = Depends(get_db)):
    need(d, Camera, cid)
    w = workers.WORKERS.get(cid)
    if not w:
        raise HTTPException(400, "Camera worker is not currently active")
    fn = w.capture_snapshot()
    if not fn:
        raise HTTPException(500, "Failed to capture snapshot from camera stream")
    return {"status": "ok", "path": fn, "url": f"/static/{fn}"}


@router.get("/people/{pid}/dossier")
def get_person_dossier(pid: int, d: Session = Depends(get_db)):
    need(d, Person, pid)
    dossier = queries.person_dossier(d, pid)
    if not dossier:
        raise HTTPException(404, "Dossier could not be compiled")
    p = dossier["person"]
    return {
        "person": {
            "id": p.id,
            "uid": p.uid,
            "name": p.name,
            "role": p.role,
            "department": p.department,
            "job_title": p.job_title,
            "badge_number": p.badge_number,
            "access_level": p.access_level,
            "phone": p.phone,
            "email": p.email,
            "gender": p.gender,
            "threat_level": p.threat_level,
            "image": p.image,
            "notes": p.notes,
            "created": p.created
        },
        "total_sightings": dossier["total_sightings"],
        "first_seen": dossier["first_seen"],
        "last_seen": dossier["last_seen"],
        "avg_confidence": dossier["avg_confidence"],
        "camera_distribution": dossier["camera_distribution"],
        "recent_crops": dossier["recent_crops"],
        "samples": dossier.get("samples", []),
        "total_embeddings": dossier.get("total_embeddings", 1)
    }


@router.get("/export/sightings.csv")
def export_sightings_csv(
    person: str = "",
    camera: str = "",
    date_from: str = "",
    date_to: str = "",
    d: Session = Depends(get_db)
):
    """Exports filtered forensic sightings to standard CSV format."""
    rows = queries.query(d, person=person, camera=camera, date_from=date_from, date_to=date_to, limit=5000)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Detection ID", "Timestamp", "Person UID", "Person Name", "Role", "Threat Level", "Camera Name", "Zone / Proximity Point", "Confidence Score", "Crop Thumbnail"])
    for r in rows:
        writer.writerow([
            r.id,
            r.ts.strftime("%Y-%m-%d %H:%M:%S") if r.ts else "",
            r.uid,
            r.name,
            r.role,
            getattr(r, "threat_level", "Low"),
            r.cam,
            r.point or "General Area",
            f"{round(r.similarity * 100, 1)}%",
            r.crop or ""
        ])
    output.seek(0)
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=sightings_export.csv"}
    )


@router.get("/system/telemetry")
def system_telemetry(d: Session = Depends(get_db)):
    """Live system resource and surveillance operational metrics."""
    db_path = settings.root / "data" / "facetrack.db"
    db_size_mb = round(os.path.getsize(db_path) / (1024 * 1024), 2) if db_path.exists() else 0.0
    
    # Active camera metrics
    cam_stats = []
    for cid, w in workers.WORKERS.items():
        cam_stats.append({
            "id": cid,
            "name": w.cam.get("name", f"Cam {cid}"),
            "status": w.status,
            "fps": w.fps,
            "resolution": w.resolution,
            "active_faces": w.active_faces,
            "session_detections": w.total_detections_session
        })

    # Recent alerts count
    unread_alerts = d.scalar(select(func.count(Alert.id)).where(Alert.dismissed == False)) or 0

    return {
        "cpu_percent": psutil.cpu_percent(),
        "memory_percent": psutil.virtual_memory().percent,
        "db_size_mb": db_size_mb,
        "active_workers": len([w for w in workers.WORKERS.values() if w.status in ("live", "looping")]),
        "total_workers": len(workers.WORKERS),
        "unread_alerts": unread_alerts,
        "cameras": cam_stats,
        "nvidia_model": settings.nvidia_model
    }


@router.delete("/people/{pid}", status_code=204)
def delete_person(pid: int, d: Session = Depends(get_db)):
    p = need(d, Person, pid)
    d.query(Detection).filter(Detection.person_id == pid).delete()
    d.delete(p)
    d.commit()
    R.KNOWN.pop(pid, None)
    for w in workers.WORKERS.values():
        w.refresh(force=True)


@router.get("/people/{pid}/journey", response_model=list[Stay])
def journey(pid: int, d: Session = Depends(get_db)):
    need(d, Person, pid)
    return queries.journey(d, pid)


@router.get("/detections", response_model=list[DetectionOut])
def detections(
    person: str = "",
    camera: str = "",
    date_from: str = "",
    date_to: str = "",
    limit: int = 200,
    min_sim: float = 0.0,
    d: Session = Depends(get_db)
):
    return queries.query(d, person, camera, date_from, date_to, min(limit, 5000), min_sim=min_sim)


@router.get("/events/recent")
def recent_events(limit: int = 40):
    return events.get_recent_events(limit)


@router.get("/events/stream")
async def event_stream():
    """Server-Sent Events (SSE) endpoint providing live real-time detection alerts."""
    return StreamingResponse(
        events.subscribe_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@router.get("/alerts")
def list_alerts(limit: int = 50, severity: str = ""):
    return events.get_alerts(limit=limit, severity=severity or None)


@router.post("/alerts/{alert_id}/dismiss")
def dismiss_alert(alert_id: str):
    ok = events.dismiss_alert(alert_id)
    return {"status": "ok" if ok else "not_found", "id": alert_id}


@router.post("/alerts/clear")
def clear_alerts():
    events.clear_all_alerts()
    return {"status": "cleared"}


@router.post("/ask", response_model=AskOut)
def ask(body: AskIn, d: Session = Depends(get_db)):
    return {"answer": llm.answer(d, body.question, "operator", preferred_model=body.model)}
