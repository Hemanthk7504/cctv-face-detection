import datetime as dt
import json
import re
from sqlalchemy import func, or_, select
from app.core.config import settings
from app.db.models import Camera, Detection, Person, QueryLog
from app.services.queries import journey, query

# Active verified models on NVIDIA API Catalog
NVIDIA_MODELS_PRIORITY = [
    "nvidia/nemotron-3-super-120b-a12b",
    "nvidia/nemotron-3.5-lightning-30b-a3b",
    "meta/llama-3.2-11b-vision-instruct",
]


def _chat_nvidia(system: str, user: str, tokens: int = 450, preferred_model: str | None = None) -> str:
    from openai import OpenAI
    client = OpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=settings.nvidia_key)

    models_to_try = []
    if preferred_model:
        models_to_try.append(preferred_model)
    if settings.nvidia_model and settings.nvidia_model not in models_to_try:
        models_to_try.append(settings.nvidia_model)
    for m in NVIDIA_MODELS_PRIORITY:
        if m not in models_to_try:
            models_to_try.append(m)

    last_error = None
    for model_name in models_to_try:
        try:
            r = client.chat.completions.create(
                model=model_name,
                temperature=0.1,
                max_tokens=tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user}
                ]
            )
            return r.choices[0].message.content
        except Exception as e:
            last_error = e
            continue

    raise last_error or Exception("All candidate NVIDIA models failed.")


def _local_nlp_answer(d, question: str) -> str:
    """Intelligent built-in security analyst that directly queries the surveillance database."""
    q_lower = question.lower().strip()
    
    # Check for person mentions
    persons = d.scalars(select(Person)).all()
    matched_person = None
    for p in persons:
        if (p.name and p.name.lower() in q_lower) or (p.uid and p.uid.lower() in q_lower):
            matched_person = p
            break
            
    # Check for camera mentions
    cameras = d.scalars(select(Camera)).all()
    matched_camera = None
    for c in cameras:
        if c.name and c.name.lower() in q_lower:
            matched_camera = c
            break

    # Case 1: Specific person query
    if matched_person:
        p = matched_person
        role_badge = f"[{p.role}]" if getattr(p, "role", None) else ""
        
        # Last seen / location query
        if any(w in q_lower for w in ("where", "last seen", "location", "latest")):
            last_det = (
                d.execute(
                    select(Detection, Camera.name.label("cname"))
                    .join(Camera, Camera.id == Detection.camera_id)
                    .where(Detection.person_id == p.id)
                    .order_by(Detection.ts.desc())
                ).first()
            )
            if last_det:
                det, cname = last_det[0], last_det[1]
                point_info = f" (Zone: **{det.point}**)" if det.point else ""
                sim_pct = f"{round(det.similarity * 100, 1)}%"
                return (
                    f"🎯 **Identity Located**: **{p.name}** ({p.uid}) {role_badge}\n\n"
                    f"- **Last Seen**: {det.ts.strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"- **Camera Feed**: **{cname}**{point_info}\n"
                    f"- **Match Confidence**: {sim_pct}\n"
                    f"- **Detection ID**: #{det.id}\n\n"
                    f"View full timeline dossier on [Profile](/people/{p.id})."
                )
            else:
                return f"ℹ️ Person **{p.name}** ({p.uid}) is registered in the database, but has no recorded camera sightings yet."

        # Timeline / Journey / "What did X do"
        stays = journey(d, p.id)
        if stays:
            lines = [f"📋 **Movement Timeline for {p.name} ({p.uid})** {role_badge}:\n"]
            for i, s in enumerate(stays[-8:], 1):
                t_range = f"{s['start'].strftime('%H:%M:%S')}"
                if s['end'] != s['start']:
                    t_range += f" - {s['end'].strftime('%H:%M:%S')}"
                zone_str = f" [{s['point']}]" if s.get('point') else ""
                lines.append(f"{i}. **{t_range}** | Camera **{s['cam']}**{zone_str} ({s['n']} detections)")
            
            lines.append(f"\nTotal movements recorded: **{len(stays)}**. View full journey at [Profile](/people/{p.id}).")
            return "\n".join(lines)

    # Case 2: Camera activity query
    if matched_camera:
        c = matched_camera
        recent_on_cam = query(d, camera=c.name, limit=10)
        if recent_on_cam:
            lines = [f"📹 **Activity Report for Camera '{c.name}'** ({c.type.upper()}):\n"]
            for r in recent_on_cam[:6]:
                zone_str = f" [Zone: {r.point}]" if r.point else ""
                lines.append(f"- **{r.ts.strftime('%H:%M:%S')}**: {r.name} ({r.uid}){zone_str} (Match: {int(r.similarity*100)}%)")
            return "\n".join(lines)
        else:
            return f"ℹ️ Camera **{c.name}** has no recorded sightings in the database."

    # Case 3: Suspect / Alerts / Watchlist
    if any(w in q_lower for w in ("suspect", "alert", "threat", "watchlist", "suspicious", "danger")):
        suspects = d.scalars(select(Person).where(Person.role == "Suspect")).all()
        if not suspects:
            return "✅ **No Suspect Alerts**: There are currently no persons flagged under the 'Suspect' or 'Watchlist' category in the registry."
        
        lines = [f"🚨 **Security Watchlist ({len(suspects)} persons flagged)**:\n"]
        for s in suspects:
            last_seen = d.scalar(select(func.max(Detection.ts)).where(Detection.person_id == s.id))
            last_str = last_seen.strftime('%Y-%m-%d %H:%M:%S') if last_seen else "Never sighted"
            lines.append(f"- ⚠️ **{s.name}** ({s.uid}) - Last seen: {last_str} ([View Profile](/people/{s.id}))")
        return "\n".join(lines)

    # Case 4: General summary / stats / "Who was seen"
    recent = query(d, limit=8)
    total_persons = d.scalar(select(func.count(Person.id))) or 0
    total_sightings = d.scalar(select(func.count(Detection.id))) or 0
    total_cameras = d.scalar(select(func.count(Camera.id))) or 0
    
    if any(w in q_lower for w in ("who", "today", "summary", "activity", "overview", "what happened", "list")):
        if recent:
            lines = [
                f"📊 **Surveillance Operations Summary**\n",
                f"- **Active Cameras**: {total_cameras} | **Registered Identities**: {total_persons} | **Total Detections**: {total_sightings}\n",
                f"\n**Latest Sighted Individuals**:"
            ]
            seen_uids = set()
            for r in recent:
                if r.uid not in seen_uids:
                    seen_uids.add(r.uid)
                    zone_str = f" @ {r.point}" if r.point else ""
                    lines.append(f"- **{r.ts.strftime('%H:%M:%S')}** - **{r.name}** ({r.uid}) on **{r.cam}**{zone_str}")
            return "\n".join(lines)

    # Fallback response
    return (
        f"🤖 **Security AI Analyst Response**:\n\n"
        f"I analyzed the surveillance logs across **{total_cameras} cameras** and **{total_sightings} detections**.\n\n"
        f"You can ask me specific questions such as:\n"
        f"- *'Where was P-0001 last seen?'*\n"
        f"- *'What did P-0002 do today?'*\n"
        f"- *'Who was detected on Entrance Gate CCTV?'*\n"
        f"- *'Show me all suspect alerts'*\n"
        f"- *'Give me an activity summary'*"
    )


def answer(d, question: str, username: str = "", preferred_model: str | None = None) -> str:
    """Answers surveillance intelligence queries using verified NVIDIA Nemotron AI

    or falls back seamlessly to the built-in Security Analyst engine.
    """
    question = question.strip()
    if not question:
        return "Please specify a surveillance question."

    ans = None

    # Try NVIDIA LLM first if key is configured
    if settings.nvidia_key and len(settings.nvidia_key.strip()) > 5:
        try:
            # Step 1: Pull filters from the question
            raw = _chat_nvidia(
                f"Today is {dt.datetime.now():%Y-%m-%d %H:%M:%S}. Reply with JSON only: "
                '{"person":"name or empty","camera":"name or empty","from":"YYYY-MM-DD HH:MM:SS or empty","to":"YYYY-MM-DD HH:MM:SS or empty"}. '
                "If only a clock time is given, use today with a window of one hour around it.",
                question,
                150,
                preferred_model=preferred_model
            )
            f = {}
            try:
                f = json.loads(re.search(r"\{.*\}", raw, re.S).group())
            except Exception:
                pass
            
            rows = query(d, f.get("person", ""), f.get("camera", ""), f.get("from", ""), f.get("to", ""), 200)
            note = ""
            if not rows:
                rows = query(d, f.get("person", ""), limit=200)
                note = "No sightings in that specific time window; showing closest sightings: "
            
            log = "\n".join(f'{r.ts} | {r.uid} {r.name} | {r.cam} | {r.point or "no point"}' for r in reversed(rows))
            ans = note + _chat_nvidia(
                "You are an expert CCTV Security AI Analyst. Answer using ONLY this CCTV log (time | person | camera | location point). "
                "State clearly if information is missing. Reply concisely and professionally in chronological order.\n" + (log or "(empty log)"),
                question,
                tokens=350,
                preferred_model=preferred_model
            )
        except Exception:
            # Seamless fallback on error
            ans = _local_nlp_answer(d, question)
    else:
        ans = _local_nlp_answer(d, question)

    try:
        d.add(QueryLog(username=username or "operator", question=question, answer=ans))
        d.commit()
    except Exception:
        pass

    return ans
