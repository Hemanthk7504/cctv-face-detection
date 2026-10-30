import threading, uuid
import cv2, numpy as np
from huggingface_hub import hf_hub_download
from sqlalchemy import select
from app.core.config import settings
from app.db.models import Person, PersonEmbedding
from app.db.session import S

DET_MODEL = hf_hub_download("opencv/face_detection_yunet", "face_detection_yunet_2023mar.onnx")
REC_MODEL = hf_hub_download("opencv/face_recognition_sface", "face_recognition_sface_2021dec.onnx")
LOCK = threading.Lock()
KNOWN: dict[int, list[np.ndarray]] = {}  # person id -> list of face embeddings (multi-sample support)


def new_detector(): return cv2.FaceDetectorYN.create(DET_MODEL, "", (320, 320), 0.85)
def new_recognizer(): return cv2.FaceRecognizerSF.create(REC_MODEL, "")


def load_known():
    """Loads all primary person embeddings and secondary angle samples into high-speed memory."""
    with S() as d:
        KNOWN.clear()
        for p in d.scalars(select(Person)):
            KNOWN[p.id] = [np.frombuffer(p.emb, np.float32).reshape(1, 128)]
        for pe in d.scalars(select(PersonEmbedding)):
            if pe.person_id in KNOWN:
                KNOWN[pe.person_id].append(np.frombuffer(pe.emb, np.float32).reshape(1, 128))
            else:
                KNOWN[pe.person_id] = [np.frombuffer(pe.emb, np.float32).reshape(1, 128)]


def identify(rec, feat):
    """Compares feature vector against all enrolled angle samples for every known identity."""
    best, bid = 0.0, None
    for pid, embs in list(KNOWN.items()):
        for k in embs:
            sc = rec.match(feat, k, cv2.FaceRecognizerSF_FR_COSINE)
            if sc > best:
                best, bid = sc, pid
    return (bid, best) if best >= settings.match_threshold else (None, best)


def enroll(
    rec, frame, face, name=None, role="Visitor", notes="",
    department="", threat_level="Low", job_title="", badge_number="",
    access_level="Standard Access", phone="", email="", gender=""
) -> int:
    feat = rec.feature(rec.alignCrop(frame, face))
    fh, fw = frame.shape[:2]
    x, y, w, h = [int(v) for v in face[:4]]
    # Add a little padding for a better portrait crop
    pad_x, pad_y = int(w * 0.15), int(h * 0.15)
    x1, y1 = max(0, x - pad_x), max(0, y - pad_y)
    x2, y2 = min(fw, x + w + pad_x), min(fh, y + h + pad_y)
    crop = frame[y1:y2, x1:x2]
    
    fn = f"faces/{uuid.uuid4().hex[:10]}.jpg"
    cv2.imwrite(str(settings.static_dir / fn), crop)
    with LOCK, S() as d:
        p = Person(
            image=fn,
            emb=feat.tobytes(),
            role=role or "Visitor",
            notes=notes or "",
            department=department or "",
            job_title=job_title or "",
            badge_number=badge_number or "",
            access_level=access_level or "Standard Access",
            phone=phone or "",
            email=email or "",
            gender=gender or "",
            threat_level=threat_level or "Low"
        )
        d.add(p)
        d.flush()
        p.uid = f"P-{p.id:04d}"
        p.name = name or p.uid
        d.commit()
        KNOWN[p.id] = [feat]
        return p.id


def add_face_sample(pid: int, image_bytes: bytes, label: str = "Additional Angle") -> dict:
    """Enrolls an extra facial photo/angle for an existing person, boosting multi-angle detection precision."""
    nparr = np.frombuffer(image_bytes, np.uint8)
    frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError("Invalid image file format")

    h, w = frame.shape[:2]
    det = new_detector()
    det.setInputSize((w, h))
    _, faces = det.detect(frame)
    if faces is None or len(faces) == 0:
        raise ValueError("No face detected in the reference photo. Ensure face is well-lit.")

    faces_sorted = sorted(faces, key=lambda f: f[2] * f[3], reverse=True)
    best_face = faces_sorted[0]
    rec = new_recognizer()
    feat = rec.feature(rec.alignCrop(frame, best_face))

    # Save cropped sample
    x, y, bw, bh = [int(v) for v in best_face[:4]]
    pad_x, pad_y = int(bw * 0.15), int(bh * 0.15)
    x1, y1 = max(0, x - pad_x), max(0, y - pad_y)
    x2, y2 = min(w, x + bw + pad_x), min(h, y + bh + pad_y)
    crop = frame[y1:y2, x1:x2]
    fn = f"faces/{uuid.uuid4().hex[:10]}.jpg"
    cv2.imwrite(str(settings.static_dir / fn), crop)

    with LOCK, S() as d:
        p = d.get(Person, pid)
        if not p:
            raise ValueError("Person not found")
        pe = PersonEmbedding(
            person_id=pid,
            emb=feat.tobytes(),
            source_image=fn,
            label=label or "Additional Angle"
        )
        d.add(pe)
        d.commit()
        if pid in KNOWN:
            KNOWN[pid].append(feat)
        else:
            KNOWN[pid] = [feat]
        return {"id": pe.id, "image": fn, "label": pe.label}


def enroll_from_image(
    image_bytes: bytes, name: str = "", role: str = "Visitor",
    department: str = "", threat_level: str = "Low", notes: str = "",
    job_title: str = "", badge_number: str = "", access_level: str = "Standard Access",
    phone: str = "", email: str = "", gender: str = ""
) -> dict:
    """Enrolls a person from an uploaded image file with comprehensive identity details."""
    nparr = np.frombuffer(image_bytes, np.uint8)
    frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError("Invalid image file format. Please upload a valid JPG or PNG.")

    h, w = frame.shape[:2]
    det = new_detector()
    det.setInputSize((w, h))
    _, faces = det.detect(frame)

    if faces is None or len(faces) == 0:
        raise ValueError("No face detected in the uploaded photo. Please ensure face is clearly visible and well-lit.")

    faces_sorted = sorted(faces, key=lambda f: f[2] * f[3], reverse=True)
    best_face = faces_sorted[0]

    rec = new_recognizer()
    pid = enroll(
        rec, frame, best_face,
        name=name, role=role, notes=notes,
        department=department, threat_level=threat_level,
        job_title=job_title, badge_number=badge_number,
        access_level=access_level, phone=phone, email=email, gender=gender
    )
    
    with S() as d:
        p = d.get(Person, pid)
        return {
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
            "notes": p.notes
        }
