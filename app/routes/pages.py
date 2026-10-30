import csv
import datetime as dt
import io
import os
import time
import cv2
import numpy as np
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session
from app.core.templating import render
from app.db.models import Camera, Detection, Person, Video
from app.db.session import get_db
from app.services import camera_service as cams, queries, recognition as R, workers

router = APIRouter()
go = lambda url: RedirectResponse(url, 303)


@router.get("/")
def home(req: Request, d: Session = Depends(get_db)):
    import psutil
    from app.core.config import settings
    st = queries.stats(d)
    hours = queries.hourly_breakdown(d)
    recent = queries.recent_detections(d, limit=12)
    cam_list = d.scalars(select(Camera)).all()
    # Resume any idle workers so dashboard has live previews
    for c in cam_list:
        if c.id not in workers.WORKERS:
            workers.start(c.id)

    db_path = settings.root / "data" / "facetrack.db"
    db_size_mb = round(os.path.getsize(db_path) / (1024 * 1024), 2) if db_path.exists() else 0.0
    telemetry = {
        "cpu": psutil.cpu_percent(),
        "ram": psutil.virtual_memory().percent,
        "db_size": db_size_mb,
        "model": settings.nvidia_model.split("/")[-1]
    }
    return render(
        req,
        "dashboard.html",
        st=st,
        hours=hours,
        now_hour=dt.datetime.now().hour,
        recent=recent,
        cams=cam_list,
        telemetry=telemetry,
        active="dashboard"
    )


@router.get("/dashboard")
def dashboard_alias(req: Request, d: Session = Depends(get_db)):
    return home(req, d)


@router.get("/live")
def live(req: Request, d: Session = Depends(get_db)):
    cam_list = d.scalars(select(Camera)).all()
    for c in cam_list:
        if c.id not in workers.WORKERS:
            workers.start(c.id)
    return render(
        req,
        "live.html",
        cams=cam_list,
        active="live",
        workers=workers.WORKERS
    )


@router.get("/stream/{cid}")
def stream(cid: int):
    def gen():
        if cid not in workers.WORKERS:
            workers.start(cid)
            time.sleep(0.3)
        while cid in workers.WORKERS:
            w = workers.WORKERS[cid]
            j = w.jpg
            if j:
                yield b"--f\r\nContent-Type: image/jpeg\r\n\r\n" + j + b"\r\n"
            time.sleep(0.035)
    return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=f")


@router.get("/cameras")
def cameras_page(req: Request, d: Session = Depends(get_db)):
    rows = d.execute(select(Camera, Video).outerjoin(Video, Video.camera_id == Camera.id)).all()
    return render(
        req,
        "cameras.html",
        cams=rows,
        workers=workers.WORKERS,
        active="cameras"
    )


@router.post("/cameras/add")
def add_cam(
    name: str = Form(...),
    url: str = Form(...),
    location: str = Form(""),
    zone: str = Form("Public Area"),
    is_restricted: str = Form(""),
    dwell_threshold_s: int = Form(90),
    d: Session = Depends(get_db)
):
    cams.add_cctv(
        d,
        name=name,
        url=url,
        location=location,
        zone=zone,
        is_restricted=(is_restricted == "on" or is_restricted == "1" or is_restricted == "true"),
        dwell_threshold_s=dwell_threshold_s
    )
    return go("/live")


@router.post("/cameras/webcam")
def add_webcam(
    name: str = Form("Local Webcam"),
    device_index: int = Form(0),
    location: str = Form("Local Station"),
    zone: str = Form("Security Checkpoint"),
    d: Session = Depends(get_db)
):
    cams.add_webcam(d, name, device_index, location, zone=zone)
    return go("/live")


@router.post("/cameras/upload")
def upload(
    name: str = Form(...),
    started: str = Form(""),
    location: str = Form(""),
    loop: str = Form("on"),
    file: UploadFile = File(...),
    d: Session = Depends(get_db)
):
    is_loop = (loop == "on")
    cams.add_video(d, name, started, file, location=location, loop=is_loop)
    return go("/live")


@router.post("/cameras/{cid}/restart")
def restart_cam(cid: int):
    cams.restart_feed(cid)
    return go("/cameras")


@router.post("/cameras/{cid}/toggle_loop")
def toggle_cam_loop(cid: int, d: Session = Depends(get_db)):
    cams.toggle_loop(d, cid)
    return go("/cameras")


@router.post("/cameras/{cid}/delete")
def delete_cam(cid: int, d: Session = Depends(get_db)):
    cams.remove(d, cid)
    return go("/cameras")


@router.get("/people")
def people_page(req: Request, d: Session = Depends(get_db)):
    return render(req, "people.html", people=queries.people_rows(d), active="people")


@router.post("/people/enroll")
def enroll(
    name: str = Form(...),
    role: str = Form("Visitor"),
    department: str = Form(""),
    job_title: str = Form(""),
    badge_number: str = Form(""),
    access_level: str = Form("Standard Access"),
    phone: str = Form(""),
    email: str = Form(""),
    gender: str = Form(""),
    threat_level: str = Form("Low"),
    notes: str = Form(""),
    file: UploadFile = File(...)
):
    content = file.file.read()
    if content:
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
            for w in workers.WORKERS.values():
                w.refresh(force=True)
            return go(f"/people/{res['id']}")
        except Exception as e:
            print(f"Web enrollment warning: {e}")
    return go("/people")


@router.post("/people/{pid}/add-sample")
def add_angle_sample(
    pid: int,
    file: UploadFile = File(...),
    label: str = Form("Additional Angle"),
    d: Session = Depends(get_db)
):
    p = d.get(Person, pid)
    if not p:
        return go("/people")
    content = file.file.read()
    if content:
        try:
            R.add_face_sample(pid, content, label=label.strip())
            for w in workers.WORKERS.values():
                w.refresh(force=True)
        except Exception as e:
            print(f"Error adding angle sample: {e}")
    return go(f"/people/{pid}")


@router.post("/people/{pid}/update")
def update_person_meta(
    pid: int,
    name: str = Form(...),
    role: str = Form("Visitor"),
    department: str = Form(""),
    job_title: str = Form(""),
    badge_number: str = Form(""),
    access_level: str = Form("Standard Access"),
    phone: str = Form(""),
    email: str = Form(""),
    gender: str = Form(""),
    threat_level: str = Form("Low"),
    notes: str = Form(""),
    d: Session = Depends(get_db)
):
    p = d.get(Person, pid)
    if p:
        p.name = name.strip()
        p.role = role.strip()
        p.department = department.strip()
        p.job_title = job_title.strip()
        p.badge_number = badge_number.strip()
        p.access_level = access_level.strip() or "Standard Access"
        p.phone = phone.strip()
        p.email = email.strip()
        p.gender = gender.strip()
        p.threat_level = threat_level.strip()
        p.notes = notes.strip()
        d.commit()
        for w in workers.WORKERS.values():
            w.refresh(force=True)
    return go(f"/people/{pid}")


@router.post("/people/{pid}/rename")
def rename(pid: int, name: str = Form(...), d: Session = Depends(get_db)):
    p = d.get(Person, pid)
    if p:
        p.name = name.strip()
        d.commit()
        for w in workers.WORKERS.values():
            w.refresh(force=True)
    return go(f"/people/{pid}")


@router.post("/people/{pid}/delete")
def delete_person(pid: int, d: Session = Depends(get_db)):
    p = d.get(Person, pid)
    if p:
        d.query(Detection).filter(Detection.person_id == pid).delete()
        d.delete(p)
        d.commit()
        R.KNOWN.pop(pid, None)
        for w in workers.WORKERS.values():
            w.refresh(force=True)
    return go("/people")


@router.post("/people/{pid}/merge")
def merge(pid: int, into: str = Form(...), d: Session = Depends(get_db)):
    into_clean = into.strip()
    t = d.scalar(select(Person).where(or_(Person.uid == into_clean, Person.name == into_clean)))
    if t and t.id != pid:
        d.execute(update(Detection).where(Detection.person_id == pid).values(person_id=t.id))
        d.delete(d.get(Person, pid))
        d.commit()
        R.KNOWN.pop(pid, None)
        for w in workers.WORKERS.values():
            w.refresh(force=True)
    return go(f"/people/{t.id if t else pid}")


@router.get("/people/{pid}")
def person_detail(req: Request, pid: int, d: Session = Depends(get_db)):
    p = d.get(Person, pid)
    if not p:
        return go("/people")
    stays = queries.journey(d, pid)
    recent_dets = queries.query(d, person=p.uid, limit=30)
    dossier = queries.person_dossier(d, pid)
    return render(
        req,
        "person.html",
        p=p,
        trips=stays,
        recent=recent_dets,
        dossier=dossier,
        active="people"
    )


@router.get("/people/{pid}/dossier")
def person_dossier_view(req: Request, pid: int, d: Session = Depends(get_db)):
    p = d.get(Person, pid)
    if not p:
        return go("/people")
    stays = queries.journey(d, pid)
    recent_dets = queries.query(d, person=p.uid, limit=50)
    dossier = queries.person_dossier(d, pid)
    return render(
        req,
        "person.html",
        p=p,
        trips=stays,
        recent=recent_dets,
        dossier=dossier,
        is_printable=True,
        active="people"
    )


@router.get("/sightings")
def sightings(
    req: Request,
    person: str = "",
    cam: str = "",
    d1: str = "",
    d2: str = "",
    min_sim: float = 0.0,
    d: Session = Depends(get_db)
):
    rows = queries.query(d, person=person, camera=cam, date_from=d1, date_to=d2, min_sim=min_sim, limit=300)
    cams_list = d.scalars(select(Camera)).all()
    return render(
        req,
        "sightings.html",
        rows=rows,
        cams=cams_list,
        q=dict(person=person, cam=cam, d1=d1, d2=d2, min_sim=min_sim),
        active="sightings"
    )


@router.get("/sightings.csv")
def sightings_csv(
    person: str = "",
    cam: str = "",
    d1: str = "",
    d2: str = "",
    min_sim: float = 0.0,
    d: Session = Depends(get_db)
):
    o = io.StringIO()
    w = csv.writer(o)
    w.writerow(["Timestamp", "Person_UID", "Name", "Role", "Camera", "Zone_Point", "Similarity_Pct"])
    for r in queries.query(d, person=person, camera=cam, date_from=d1, date_to=d2, min_sim=min_sim, limit=10000):
        sim_pct = round(r.similarity * 100, 1)
        w.writerow([r.ts, r.uid, r.name, getattr(r, "role", "Visitor"), r.cam, r.point or "N/A", sim_pct])
    return Response(
        o.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=cctv_detections.csv"}
    )


@router.get("/ask")
def ask_page(req: Request, d: Session = Depends(get_db)):
    persons = d.scalars(select(Person).limit(10)).all()
    cams_list = d.scalars(select(Camera).limit(6)).all()
    from app.core.config import settings
    return render(
        req,
        "ask.html",
        active="ask",
        persons=persons,
        cams=cams_list,
        current_model=settings.nvidia_model,
        has_key=bool(settings.nvidia_key)
    )


@router.get("/alerts")
def alerts_page(req: Request, severity: str = ""):
    from app.services import events
    alert_items = events.get_alerts(limit=60, severity=severity or None)
    return render(
        req,
        "alerts.html",
        alerts=alert_items,
        active="alerts",
        selected_severity=severity
    )

