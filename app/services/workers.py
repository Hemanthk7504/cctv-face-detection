import collections
import datetime as dt
import math
import os
import threading
import time
import uuid
import cv2
import numpy as np
from sqlalchemy import select
from app.core.config import settings
from app.db.models import Camera, Detection, Person, Point, Video, Alert
from app.db.session import S
from app.services import recognition as R
from app.services.events import publish_event

now = lambda: dt.datetime.now().replace(microsecond=0)
WORKERS: dict[int, "Worker"] = {}


class Worker(threading.Thread):
    """Real-time surveillance worker. Reads camera/webcam/video stream,

    runs YuNet face detection & SFace recognition, renders HUD & overlays,
    logs detections to DB, and broadcasts live events.
    """

    def __init__(s, cid: int):
        super().__init__(daemon=True)
        with S() as d:
            c = d.get(Camera, cid)
            v = d.scalar(select(Video).where(Video.camera_id == cid).order_by(Video.id.desc()))
            s.cam = dict(
                id=c.id,
                name=c.name,
                source=c.source,
                type=c.type,
                started=c.started,
                location=getattr(c, "location", "") or "",
                zone=getattr(c, "zone", "Public Area") or "Public Area",
                is_restricted=bool(getattr(c, "is_restricted", False)),
                dwell_threshold_s=int(getattr(c, "dwell_threshold_s", 90) or 90),
                loop=getattr(c, "loop", True)
            )
            s.vid = v.id if v else None
            
        s.cid = cid
        s.jpg = None
        s.stop = False
        s.pos = 0.0
        s.fno = 0
        s.status = "initializing"
        s.resolution = "N/A"
        s.fps = 0.0
        s.active_faces = 0
        s.total_detections_session = 0
        
        # Internal state & performance optimization
        s._frame_times = collections.deque(maxlen=30)
        s._frame_idx = 0
        s._cached_detected_faces = []
        s.det = R.new_detector()
        s.rec = R.new_recognizer()
        s.last_detection_t = {}
        s.last_alert_t = {}  # (alert_type, pid) -> timestamp
        s.dwell_tracker = {}  # pid -> {"first_seen": float, "last_seen": float, "alerted": bool}
        s.pts = []
        s.pts_t = 0.0
        s.nm = {}
        s.refresh(force=True)

    def capture_snapshot(s):
        """Saves current frame as a forensic snapshot with metadata timestamp."""
        if s.jpg:
            fn = f"snapshots/cam_{s.cid}_{int(time.time())}_{uuid.uuid4().hex[:6]}.jpg"
            full_path = settings.static_dir / fn
            with open(full_path, "wb") as f_out:
                f_out.write(s.jpg)
            return fn
        return None

    def _trigger_alert(s, alert_type: str, severity: str, title: str, message: str, pid: int | None = None):
        """Dispatches a structured security alert to the Alert Center and live events stream."""
        try:
            snap_path = s.capture_snapshot()
            with S() as d:
                alt = Alert(
                    camera_id=s.cam["id"],
                    person_id=pid,
                    alert_type=alert_type,
                    severity=severity,
                    title=title,
                    message=message,
                    snapshot_path=snap_path or ""
                )
                d.add(alt)
                d.commit()
                publish_event({
                    "event": "alert",
                    "id": alt.id,
                    "type": alert_type,
                    "severity": severity,
                    "title": title,
                    "message": message,
                    "camera_name": s.cam["name"],
                    "timestamp": dt.datetime.now().strftime("%H:%M:%S")
                })
        except Exception as err:
            print(f"[Worker Cam {s.cid}] Alert error: {err}")

    def refresh(s, force=False):
        if force or time.time() - s.pts_t > 2.0:
            with S() as d:
                s.pts = [
                    dict(id=p.id, x=p.x, y=p.y, label=p.label)
                    for p in d.scalars(select(Point).where(Point.camera_id == s.cam["id"]))
                ]
                s.nm = {
                    p.id: {
                        "uid": p.uid,
                        "name": p.name or p.uid,
                        "role": getattr(p, "role", "Visitor") or "Visitor",
                        "department": getattr(p, "department", "") or "",
                        "job_title": getattr(p, "job_title", "") or "",
                        "badge_number": getattr(p, "badge_number", "") or "",
                        "access_level": getattr(p, "access_level", "Standard Access") or "Standard Access",
                        "threat_level": getattr(p, "threat_level", "Low") or "Low",
                        "phone": getattr(p, "phone", "") or "",
                        "email": getattr(p, "email", "") or "",
                        "gender": getattr(p, "gender", "") or "",
                        "notes": getattr(p, "notes", "") or "",
                        "image": p.image or ""
                    }
                    for p in d.scalars(select(Person))
                }
            s.pts_t = time.time()

    def ts(s):
        if s.cam["type"] == "video" and s.cam["started"]:
            return s.cam["started"] + dt.timedelta(seconds=s.pos)
        return now()

    def progress(s, status):
        if not s.vid:
            return
        with S() as d:
            v = d.get(Video, s.vid)
            if v:
                v.status = status
                v.processed_frames = v.frames if status == "done" else s.fno
                d.commit()

    def run(s):
        src = s.cam["source"]
        is_num = str(src).strip().isdigit()
        cap_arg = int(src) if is_num else src

        def _open():
            try:
                c = cv2.VideoCapture(cap_arg)
                if c and c.isOpened():
                    return c
            except Exception as e:
                print(f"[Worker Cam {s.cid}] Stream open error: {e}")
            return None

        cap = _open()
        n = 0
        s.status = "live" if cap else "offline"
        fail_count = 0

        while not s.stop:
            if not cap or not cap.isOpened():
                fail_count += 1
                s.status = "reconnecting"
                time.sleep(3.0 if fail_count < 4 else 10.0)
                cap = _open()
                continue

            t0 = time.time()
            ok = False
            try:
                ok, f = cap.read()
            except Exception:
                ok = False

            if not ok or f is None:
                if s.cam["type"] == "video":
                    s.progress("done")
                    if s.cam.get("loop", True):
                        s.status = "looping"
                        try:
                            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        except Exception:
                            pass
                        time.sleep(0.04)
                        continue
                    else:
                        s.status = "completed"
                        time.sleep(0.5)
                        continue
                
                # Camera disconnection recovery
                s.status = "reconnecting"
                try:
                    cap.release()
                except Exception:
                    pass
                cap = None
                time.sleep(3.0)
                continue

            fail_count = 0
            s.status = "live"
            try:
                s.pos = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
                s.fno = int(cap.get(cv2.CAP_PROP_POS_FRAMES))
            except Exception:
                s.pos = 0.0
                s.fno = n
            n += 1

            # Process frame with efficient face recognition
            processed = s.process(f)
            
            # Encode frame to JPEG
            encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 80]
            s.jpg = cv2.imencode(".jpg", processed, encode_param)[1].tobytes()

            if n % 30 == 0:
                s.progress("processing")

            # FPS calculation
            t1 = time.time()
            s._frame_times.append(t1 - t0)
            if len(s._frame_times) >= 5:
                avg_dt = sum(s._frame_times) / len(s._frame_times)
                s.fps = round(1.0 / avg_dt, 1) if avg_dt > 0 else 0.0

            # Target ~25 FPS pacing smoothly
            elapsed = time.time() - t0
            sleep_needed = max(0.005, 0.040 - elapsed)
            time.sleep(sleep_needed)

        cap.release()
        s.status = "offline"

    def process(s, f):
        h, w = f.shape[:2]
        s.resolution = f"{w}x{h}"
        s.refresh()
        s._frame_idx += 1

        # Run face detection every 2 frames for responsive tracking with low CPU
        if s._frame_idx % 2 == 0 or not s._cached_detected_faces:
            s.det.setInputSize((w, h))
            _, faces = s.det.detect(f)
            s._cached_detected_faces = []
            
            face_list = faces if faces is not None else []
            s.active_faces = len(face_list)
            
            for face in face_list:
                feat = s.rec.feature(s.rec.alignCrop(f, face))
                pid, sim = R.identify(s.rec, feat)
                
                # NO automatic rogue enrollments! Only enroll if explicitly enabled in settings.auto_enroll
                if pid is None and settings.auto_enroll and face[14] > 0.95:
                    pid, sim = R.enroll(s.rec, f, face), 1.0
                    s.refresh(force=True)

                x, y, bw, bh = [int(v) for v in face[:4]]
                cx, cy = (x + bw / 2) / w, (y + bh) / h
                
                # Proximity to marked location points
                near = min(s.pts, key=lambda p: math.hypot(p["x"] - cx, p["y"] - cy), default=None)
                label = near["label"] if near and math.hypot(near["x"] - cx, near["y"] - cy) < 0.22 else None
                
                # Case A: Known Person
                if pid is not None:
                    person = s.nm.get(pid, {"uid": f"P-{pid:04d}", "name": f"P-{pid:04d}", "role": "Visitor", "image": ""})
                    role = person.get("role", "Visitor")
                    is_suspect = (role in ("Suspect", "Banned"))
                    now_t = time.time()

                    # 1. Dwell & Loitering Analytics
                    if pid not in s.dwell_tracker:
                        s.dwell_tracker[pid] = {"first_seen": now_t, "last_seen": now_t, "alerted": False}
                    else:
                        s.dwell_tracker[pid]["last_seen"] = now_t
                        dwell_sec = now_t - s.dwell_tracker[pid]["first_seen"]
                        dwell_threshold = s.cam.get("dwell_threshold_s", 90) or 90
                        if dwell_sec >= dwell_threshold and not s.dwell_tracker[pid]["alerted"]:
                            s.dwell_tracker[pid]["alerted"] = True
                            s._trigger_alert(
                                alert_type="loitering",
                                severity="warning",
                                title=f"Loitering Alert: {person.get('name')}",
                                message=f"{person.get('name')} ({person.get('uid')}) has been lingering at {s.cam['name']} for over {int(dwell_sec)}s.",
                                pid=pid
                            )

                    # 2. Security Watchlist Rule (Banned / Suspect Person)
                    if is_suspect and (now_t - s.last_alert_t.get(("banned", pid), 0)) > 45:
                        s.last_alert_t[("banned", pid)] = now_t
                        s._trigger_alert(
                            alert_type="banned_detected",
                            severity="critical",
                            title=f"BANNED INDIVIDUAL DETECTED: {person.get('name')}",
                            message=f"Banned person {person.get('name')} ({person.get('uid')}) identified at {s.cam['name']} [{s.cam.get('zone', 'Security Area')}] with {round(float(sim)*100, 1)}% match.",
                            pid=pid
                        )

                    # 3. Restricted Zone Breach Rule
                    if s.cam.get("is_restricted") and role not in ("Staff", "Security") and (now_t - s.last_alert_t.get(("restricted", pid), 0)) > 45:
                        s.last_alert_t[("restricted", pid)] = now_t
                        s._trigger_alert(
                            alert_type="restricted_zone",
                            severity="critical",
                            title=f"RESTRICTED ZONE BREACH: {s.cam.get('zone', 'Restricted Area')}",
                            message=f"Unauthorized person {person.get('name')} ({role}) entered {s.cam['name']} [{s.cam.get('zone')}].",
                            pid=pid
                        )

                    # 4. Face Crop Thumbnail Extraction & Detection Logging (Debounced: 5.0s per person)
                    if now_t - s.last_detection_t.get(pid, -99.0) > 5.0:
                        s.last_detection_t[pid] = now_t
                        s.total_detections_session += 1
                        
                        # Extract crisp face crop thumbnail for forensic evidence
                        crop_fn = None
                        try:
                            pad_x, pad_y = int(bw * 0.15), int(bh * 0.15)
                            x1, y1 = max(0, x - pad_x), max(0, y - pad_y)
                            x2, y2 = min(w, x + bw + pad_x), min(h, y + bh + pad_y)
                            face_crop = f[y1:y2, x1:x2]
                            if face_crop.size > 0:
                                crop_fn = f"crops/{uuid.uuid4().hex[:12]}.jpg"
                                cv2.imwrite(str(settings.static_dir / crop_fn), face_crop)
                        except Exception:
                            pass

                        det_record = None
                        try:
                            with S() as d:
                                cur_ts = s.ts()
                                det = Detection(
                                    person_id=pid,
                                    camera_id=s.cam["id"],
                                    video_id=s.vid,
                                    ts=cur_ts,
                                    frame_no=s.fno,
                                    video_sec=s.pos,
                                    bx=x / w, by=y / h, bw=bw / w, bh=bh / h,
                                    det_score=float(face[14]),
                                    similarity=float(sim),
                                    point=label,
                                    crop_path=crop_fn
                                )
                                d.add(det)
                                d.commit()
                                
                                det_record = {
                                    "id": det.id,
                                    "person_id": pid,
                                    "person_uid": person.get("uid", f"P-{pid:04d}"),
                                    "person_name": person.get("name", f"P-{pid:04d}"),
                                    "person_role": role,
                                    "job_title": person.get("job_title", ""),
                                    "department": person.get("department", ""),
                                    "badge_number": person.get("badge_number", ""),
                                    "access_level": person.get("access_level", "Standard Access"),
                                    "threat_level": person.get("threat_level", "Low"),
                                    "phone": person.get("phone", ""),
                                    "camera_id": s.cam["id"],
                                    "camera_name": s.cam.get("name", "Camera"),
                                    "point": label or "General Area",
                                    "similarity": round(float(sim) * 100, 1),
                                    "ts": cur_ts.strftime("%H:%M:%S"),
                                    "date": cur_ts.strftime("%Y-%m-%d"),
                                    "image": person.get("image", ""),
                                    "crop": crop_fn,
                                    "is_suspect": is_suspect
                                }
                        except Exception as dberr:
                            print(f"[Worker Cam {s.cid}] DB write skip (busy): {dberr}")

                        if det_record:
                            publish_event(det_record)

                    s._cached_detected_faces.append({
                        "box": (x, y, bw, bh),
                        "name": person.get("name", "Person"),
                        "role": role,
                        "job_title": person.get("job_title", ""),
                        "department": person.get("department", ""),
                        "badge_number": person.get("badge_number", ""),
                        "access_level": person.get("access_level", "Standard Access"),
                        "threat_level": person.get("threat_level", "Low"),
                        "sim": sim,
                        "is_suspect": is_suspect,
                        "is_known": True
                    })
                else:
                    # Case B: Unregistered Face - do NOT create a new person!
                    s._cached_detected_faces.append({
                        "box": (x, y, bw, bh),
                        "name": "Unregistered Face",
                        "role": "Unknown",
                        "sim": float(sim),
                        "is_suspect": False,
                        "is_known": False
                    })

        # Draw marked points / location zones
        for p in s.pts:
            px, py = int(p["x"] * w), int(p["y"] * h)
            cv2.circle(f, (px, py), 10, (0, 200, 255), 2)
            cv2.circle(f, (px, py), 4, (0, 200, 255), -1)
            lbl = p["label"]
            (tw, th), _ = cv2.getTextSize(lbl, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
            cv2.rectangle(f, (px + 14, py - 12), (px + 18 + tw, py + 6), (15, 23, 42), -1)
            cv2.rectangle(f, (px + 14, py - 12), (px + 18 + tw, py + 6), (0, 200, 255), 1)
            cv2.putText(f, lbl, (px + 16, py + 1), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)

        # Draw cached face boxes
        for f_info in s._cached_detected_faces:
            x, y, bw, bh = f_info["box"]
            role = f_info["role"]
            is_suspect = f_info["is_suspect"]
            is_known = f_info["is_known"]
            sim = f_info["sim"]
            name = f_info["name"]

            # Colors per role (BGR)
            if not is_known:
                box_color = (180, 160, 140)     # Neutral Slate
                tag_bg = (70, 70, 75)
                display_str = f"Unregistered [{int(sim*100)}%]"
            elif role in ("Banned", "Suspect"):
                box_color = (35, 35, 235)       # Crimson Red
                tag_bg = (30, 20, 200)
                display_str = f"BANNED: {name}"
            elif role == "VIP":
                box_color = (0, 215, 255)       # Gold
                tag_bg = (0, 160, 210)
                display_str = f"{name} [VIP] {int(sim*100)}%"
            elif role == "Staff":
                box_color = (235, 185, 30)      # Cyan/Blue
                tag_bg = (180, 130, 20)
                display_str = f"{name} [STAFF] {int(sim*100)}%"
            elif role == "Security":
                box_color = (200, 80, 180)      # Violet
                tag_bg = (140, 40, 130)
                display_str = f"{name} [SECURITY]"
            elif role == "Contractor":
                box_color = (40, 200, 220)      # Amber/Teal
                tag_bg = (30, 140, 160)
                display_str = f"{name} [CONTRACTOR] {int(sim*100)}%"
            else:
                box_color = (80, 215, 70)       # Emerald Green (Visitor)
                tag_bg = (40, 140, 35)
                display_str = f"{name} [{role}] {int(sim*100)}%"

            # Bounding Box with styled corner brackets
            cv2.rectangle(f, (x, y), (x + bw, y + bh), box_color, 2)
            corner_len = min(14, bw // 4, bh // 4)
            cv2.line(f, (x, y), (x + corner_len, y), box_color, 4)
            cv2.line(f, (x, y), (x, y + corner_len), box_color, 4)
            cv2.line(f, (x + bw, y), (x + bw - corner_len, y), box_color, 4)
            cv2.line(f, (x + bw, y), (x + bw, y + corner_len), box_color, 4)
            cv2.line(f, (x, y + bh), (x + corner_len, y + bh), box_color, 4)
            cv2.line(f, (x, y + bh), (x, y + bh - corner_len), box_color, 4)
            cv2.line(f, (x + bw, y + bh), (x + bw - corner_len, y + bh), box_color, 4)
            cv2.line(f, (x + bw, y + bh), (x + bw, y + bh - corner_len), box_color, 4)

            # Badge Label (Line 1: Name + Role + Score)
            (lw, lh), _ = cv2.getTextSize(display_str, cv2.FONT_HERSHEY_SIMPLEX, 0.48, 1)
            label_y = max(y - 8, lh + 6)
            cv2.rectangle(f, (x, label_y - lh - 4), (x + lw + 8, label_y + 4), tag_bg, -1)
            cv2.putText(f, display_str, (x + 4, label_y), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (255, 255, 255), 1, cv2.LINE_AA)

            # Sub-Badge (Line 2: Title / Department / Badge # / Access Level)
            if is_known:
                title_dept = f_info.get("job_title") or f_info.get("department") or ""
                badge_num = f_info.get("badge_number") or ""
                access = f_info.get("access_level") or ""
                
                parts = []
                if title_dept: parts.append(title_dept)
                if badge_num: parts.append(f"#{badge_num}")
                elif access and access != "Standard Access": parts.append(access)
                
                if parts:
                    sub_str = " · ".join(parts)
                    (slw, slh), _ = cv2.getTextSize(sub_str, cv2.FONT_HERSHEY_SIMPLEX, 0.38, 1)
                    sub_y = y + bh + slh + 8
                    if sub_y < h - 4:
                        cv2.rectangle(f, (x, sub_y - slh - 3), (x + slw + 8, sub_y + 3), (15, 23, 42), -1)
                        cv2.rectangle(f, (x, sub_y - slh - 3), (x + slw + 8, sub_y + 3), box_color, 1)
                        cv2.putText(f, sub_str, (x + 4, sub_y - 1), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (220, 230, 245), 1, cv2.LINE_AA)

        # Surveillance HUD Header Bar
        cv2.rectangle(f, (0, 0), (w, 30), (10, 14, 23), -1)
        cv2.line(f, (0, 30), (w, 30), (30, 41, 59), 1)

        # Left HUD: Cam Name + REC + FPS
        hud_left = f"{s.cam['name'].upper()} | REC "
        cv2.putText(f, hud_left, (10, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.46, (220, 230, 240), 1, cv2.LINE_AA)
        
        # Red pulsing dot next to REC
        if int(time.time() * 2) % 2 == 0:
            cv2.circle(f, (int(cv2.getTextSize(hud_left, cv2.FONT_HERSHEY_SIMPLEX, 0.46, 1)[0][0]) + 10, 15), 4, (0, 0, 255), -1)

        hud_mid = f"  {s.resolution} @ {s.fps} FPS"
        cv2.putText(f, hud_mid, (int(cv2.getTextSize(hud_left, cv2.FONT_HERSHEY_SIMPLEX, 0.46, 1)[0][0]) + 20, 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (140, 160, 180), 1, cv2.LINE_AA)

        # Right HUD: Real-time Date & Time
        time_str = dt.datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
        (tw, _), _ = cv2.getTextSize(time_str, cv2.FONT_HERSHEY_SIMPLEX, 0.46, 1)
        cv2.putText(f, time_str, (w - tw - 12, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.46, (200, 220, 235), 1, cv2.LINE_AA)

        return f


def start(cid: int):
    if cid in WORKERS and WORKERS[cid].is_alive():
        return WORKERS[cid]
    w = Worker(cid)
    WORKERS[cid] = w
    w.start()
    return w


def stop(cid: int):
    w = WORKERS.pop(cid, None)
    if w:
        w.stop = True


def restart(cid: int):
    stop(cid)
    time.sleep(0.2)
    return start(cid)


def stop_all():
    for cid in list(WORKERS):
        stop(cid)


def resume_all():
    """Start all active cameras/videos so surveillance is always live and running."""
    with S() as d:
        for c in d.scalars(select(Camera)):
            start(c.id)
