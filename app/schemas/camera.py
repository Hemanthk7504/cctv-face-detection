import datetime as dt
from pydantic import BaseModel, ConfigDict, Field


class CameraCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=1, description="RTSP/HTTP link, or 0 for a webcam")
    location: str = Field(default="", max_length=120)
    zone: str = Field(default="Public Area", max_length=80)
    is_restricted: bool = Field(default=False)
    dwell_threshold_s: int = Field(default=90, ge=10)


class WebcamCreate(BaseModel):
    name: str = Field(default="Local Webcam", max_length=120)
    device_index: int = Field(default=0, ge=0)
    location: str = Field(default="Workstation", max_length=120)
    zone: str = Field(default="Security Checkpoint", max_length=80)


class CameraOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    type: str
    source: str
    started: dt.datetime | None = None
    location: str | None = ""
    zone: str | None = "Public Area"
    is_restricted: bool | None = False
    dwell_threshold_s: int | None = 90
    loop: bool | None = True
    status: str | None = "offline"
    fps: float | None = 0.0
    resolution: str | None = "N/A"
    active_faces: int | None = 0


class VideoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    camera_id: int
    filename: str
    size_mb: float
    fps: float
    width: int
    height: int
    frames: int
    duration_s: float
    recorded_at: dt.datetime
    uploaded_at: dt.datetime
    status: str
    processed_frames: int


class PointToggle(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    label: str = Field(default="", max_length=60)


class PointResult(BaseModel):
    action: str
    label: str | None = None
