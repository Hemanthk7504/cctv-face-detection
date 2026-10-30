import datetime as dt
from sqlalchemy import ForeignKey, String, Float, Integer, LargeBinary, DateTime, Text, Boolean
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

_now = lambda: dt.datetime.now().replace(microsecond=0)


class Base(DeclarativeBase): pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password: Mapped[str] = mapped_column(String(200))
    created: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class Camera(Base):
    __tablename__ = "cameras"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    source: Mapped[str] = mapped_column(Text)
    type: Mapped[str] = mapped_column(String(10))  # cctv | video
    started: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    location: Mapped[str] = mapped_column(String(120), default="")
    zone: Mapped[str] = mapped_column(String(80), default="Public Area")
    is_restricted: Mapped[bool] = mapped_column(Boolean, default=False)
    dwell_threshold_s: Mapped[int] = mapped_column(Integer, default=90)
    loop: Mapped[bool] = mapped_column(Boolean, default=True)
    created: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class Video(Base):
    __tablename__ = "videos"
    id: Mapped[int] = mapped_column(primary_key=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    path: Mapped[str] = mapped_column(Text)
    size_mb: Mapped[float] = mapped_column(Float, default=0)
    fps: Mapped[float] = mapped_column(Float, default=0)
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    frames: Mapped[int] = mapped_column(Integer, default=0)
    duration_s: Mapped[float] = mapped_column(Float, default=0)
    recorded_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    uploaded_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    status: Mapped[str] = mapped_column(String(20), default="processing")
    processed_frames: Mapped[int] = mapped_column(Integer, default=0)


class Person(Base):
    __tablename__ = "persons"
    id: Mapped[int] = mapped_column(primary_key=True)
    uid: Mapped[str] = mapped_column(String(20), default="")
    name: Mapped[str] = mapped_column(String(120), default="")
    image: Mapped[str] = mapped_column(String(255))
    emb: Mapped[bytes] = mapped_column(LargeBinary)
    role: Mapped[str] = mapped_column(String(50), default="Visitor")  # Visitor | Staff | VIP | Suspect | Banned | Contractor | Security
    department: Mapped[str] = mapped_column(String(100), default="")
    job_title: Mapped[str] = mapped_column(String(100), default="")
    badge_number: Mapped[str] = mapped_column(String(50), default="")
    access_level: Mapped[str] = mapped_column(String(80), default="Standard Access")  # All Access | Level 1 | Level 2 | Escort Required | Restricted
    phone: Mapped[str] = mapped_column(String(50), default="")
    email: Mapped[str] = mapped_column(String(120), default="")
    gender: Mapped[str] = mapped_column(String(30), default="")
    threat_level: Mapped[str] = mapped_column(String(20), default="Low")  # Low | Medium | High | Critical
    notes: Mapped[str] = mapped_column(Text, default="")
    created: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class PersonEmbedding(Base):
    """Stores multiple facial embeddings and angle samples for a single person to boost detection precision."""
    __tablename__ = "person_embeddings"
    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("persons.id", ondelete="CASCADE"), index=True)
    emb: Mapped[bytes] = mapped_column(LargeBinary)
    source_image: Mapped[str] = mapped_column(String(255), default="")
    label: Mapped[str] = mapped_column(String(80), default="Additional Angle")
    created: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class Point(Base):
    __tablename__ = "points"
    id: Mapped[int] = mapped_column(primary_key=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id"), index=True)
    x: Mapped[float] = mapped_column(Float)
    y: Mapped[float] = mapped_column(Float)
    label: Mapped[str] = mapped_column(String(40))


class Detection(Base):
    __tablename__ = "detections"
    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("persons.id"), index=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id"), index=True)
    video_id: Mapped[int | None] = mapped_column(ForeignKey("videos.id"), nullable=True, index=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime, index=True)
    frame_no: Mapped[int] = mapped_column(Integer, default=0)
    video_sec: Mapped[float] = mapped_column(Float, default=0)
    bx: Mapped[float] = mapped_column(Float)  # face box, 0-1 of frame size
    by: Mapped[float] = mapped_column(Float)
    bw: Mapped[float] = mapped_column(Float)
    bh: Mapped[float] = mapped_column(Float)
    det_score: Mapped[float] = mapped_column(Float, default=0)
    similarity: Mapped[float] = mapped_column(Float, default=0)
    point: Mapped[str | None] = mapped_column(String(40), nullable=True)
    crop_path: Mapped[str | None] = mapped_column(String(255), nullable=True)


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(primary_key=True)
    camera_id: Mapped[int | None] = mapped_column(ForeignKey("cameras.id"), nullable=True, index=True)
    person_id: Mapped[int | None] = mapped_column(ForeignKey("persons.id"), nullable=True, index=True)
    alert_type: Mapped[str] = mapped_column(String(50), default="security")  # banned_detected | restricted_zone | loitering | unknown | anomaly
    severity: Mapped[str] = mapped_column(String(20), default="warning")  # critical | warning | info
    title: Mapped[str] = mapped_column(String(120))
    message: Mapped[str] = mapped_column(Text)
    snapshot_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    dismissed: Mapped[bool] = mapped_column(Boolean, default=False)
    created: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, index=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    action: Mapped[str] = mapped_column(String(80))
    details: Mapped[str] = mapped_column(Text, default="")
    created: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, index=True)


class QueryLog(Base):
    __tablename__ = "query_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80))
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    created: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
