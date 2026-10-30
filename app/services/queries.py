import datetime as dt
from sqlalchemy import func, or_, select
from app.db.models import Camera, Detection, Person, PersonEmbedding, Video
from app.services import workers


def parse_date(v: str, end: bool = False):
    if not v:
        return None
    try:
        t = dt.datetime.fromisoformat(v)
    except (ValueError, TypeError):
        return None
    return t + dt.timedelta(days=1) if end and len(v) == 10 else t


def query(d, person="", camera="", date_from="", date_to="", limit=500, min_sim=0.0):
    q = (
        select(
            Detection.id,
            Detection.ts,
            Person.id.label("pid"),
            Person.uid,
            Person.name,
            Person.role,
            Person.threat_level,
            Person.image,
            Detection.crop_path.label("crop"),
            Camera.id.label("cid"),
            Camera.name.label("cam"),
            Detection.point.label("point"),
            Detection.similarity,
            Detection.video_id
        )
        .join(Person, Person.id == Detection.person_id)
        .join(Camera, Camera.id == Detection.camera_id)
    )
    if person:
        q = q.where(or_(Person.name.ilike(f"%{person}%"), Person.uid.ilike(f"%{person}%")))
    if camera:
        q = q.where(Camera.name.ilike(f"%{camera}%"))
    if parse_date(date_from):
        q = q.where(Detection.ts >= parse_date(date_from))
    if parse_date(date_to, True):
        q = q.where(Detection.ts <= parse_date(date_to, True))
    if min_sim > 0.0:
        q = q.where(Detection.similarity >= min_sim)

    return d.execute(q.order_by(Detection.ts.desc()).limit(limit)).all()


def people_rows(d):
    sub = (
        select(
            Detection.person_id,
            func.count().label("n"),
            func.max(Detection.ts).label("last")
        )
        .group_by(Detection.person_id)
        .subquery()
    )
    return d.execute(
        select(
            Person.id,
            Person.uid,
            Person.name,
            Person.image,
            Person.role,
            Person.department,
            Person.job_title,
            Person.badge_number,
            Person.access_level,
            Person.threat_level,
            Person.notes,
            Person.created,
            func.coalesce(sub.c.n, 0).label("n"),
            sub.c.last.label("last")
        ).outerjoin(sub, sub.c.person_id == Person.id).order_by(sub.c.last.desc().nullslast())
    ).all()


def journey(d, pid: int):
    rows = d.execute(
        select(
            Detection.ts,
            Camera.name.label("cam"),
            Detection.point
        )
        .join(Camera, Camera.id == Detection.camera_id)
        .where(Detection.person_id == pid)
        .order_by(Detection.ts)
    ).all()
    
    stays = []
    for r in rows:
        if stays and stays[-1]["cam"] == r.cam and stays[-1]["point"] == r.point:
            stays[-1]["end"] = r.ts
            stays[-1]["n"] += 1
        else:
            stays.append(dict(cam=r.cam, point=r.point, start=r.ts, end=r.ts, n=1))
    return stays


def hourly_breakdown(d):
    """Counts detections per hour for today (0 to 23)."""
    today_start = dt.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + dt.timedelta(days=1)
    
    counts = [0] * 24
    rows = d.execute(
        select(Detection.ts)
        .where(Detection.ts >= today_start, Detection.ts < today_end)
    ).scalars().all()
    
    for ts in rows:
        if 0 <= ts.hour < 24:
            counts[ts.hour] += 1
            
    # If today is empty, get previous 24 hours of detections
    if sum(counts) == 0:
        all_recent = d.execute(
            select(Detection.ts).order_by(Detection.ts.desc()).limit(300)
        ).scalars().all()
        for ts in all_recent:
            counts[ts.hour] += 1

    return counts


def recent_detections(d, limit=12):
    return query(d, limit=limit)


def stats(d):
    c = lambda m: d.scalar(select(func.count(m.id))) or 0
    suspects = d.scalar(select(func.count(Person.id)).where(Person.role.in_(["Suspect", "Banned"]))) or 0
    active_workers_count = len([w for w in workers.WORKERS.values() if w.status in ("live", "looping")])
    return dict(
        cams=c(Camera),
        people=c(Person),
        sightings=c(Detection),
        videos=c(Video),
        suspects=suspects,
        active_streams=active_workers_count
    )


def person_dossier(d, pid: int):
    """Compiles a complete intelligence dossier on a person."""
    person = d.get(Person, pid)
    if not person:
        return None

    # Sightings count & times
    s_stats = d.execute(
        select(
            func.count(Detection.id).label("total"),
            func.min(Detection.ts).label("first_seen"),
            func.max(Detection.ts).label("last_seen"),
            func.avg(Detection.similarity).label("avg_sim")
        ).where(Detection.person_id == pid)
    ).first()

    # Camera breakdown
    cam_counts = d.execute(
        select(Camera.name, func.count(Detection.id))
        .join(Camera, Camera.id == Detection.camera_id)
        .where(Detection.person_id == pid)
        .group_by(Camera.name)
        .order_by(func.count(Detection.id).desc())
    ).all()

    # Recent face crops
    recent_crops = d.execute(
        select(Detection.crop_path, Detection.ts, Camera.name)
        .join(Camera, Camera.id == Detection.camera_id)
        .where(Detection.person_id == pid, Detection.crop_path.isnot(None))
        .order_by(Detection.ts.desc())
        .limit(6)
    ).all()

    # Enrolled additional angle / face samples
    samples = d.scalars(select(PersonEmbedding).where(PersonEmbedding.person_id == pid)).all()

    return {
        "person": person,
        "total_sightings": s_stats.total if s_stats else 0,
        "first_seen": s_stats.first_seen if s_stats else None,
        "last_seen": s_stats.last_seen if s_stats else None,
        "avg_confidence": round(float(s_stats.avg_sim or 0) * 100, 1),
        "camera_distribution": [{"camera": r[0], "count": r[1]} for r in cam_counts],
        "recent_crops": [{"path": r[0], "ts": r[1], "camera": r[2]} for r in recent_crops if r[0]],
        "samples": [{"id": s.id, "image": s.source_image, "label": s.label} for s in samples],
        "total_embeddings": 1 + len(samples)
    }
