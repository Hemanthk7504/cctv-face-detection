import datetime as dt
from pydantic import BaseModel, ConfigDict, Field


class DetectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int | None = None
    ts: dt.datetime
    pid: int
    uid: str
    name: str
    role: str | None = "Visitor"
    threat_level: str | None = "Low"
    image: str | None = ""
    crop: str | None = ""
    cid: int | None = None
    cam: str
    point: str | None
    similarity: float
    video_id: int | None


class AskIn(BaseModel):
    question: str = Field(min_length=2, max_length=500)
    model: str | None = None


class AskOut(BaseModel):
    answer: str


class StatsOut(BaseModel):
    cams: int
    people: int
    sightings: int
    videos: int
    suspects: int = 0
    active_streams: int = 0
