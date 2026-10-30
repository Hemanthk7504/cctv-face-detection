import datetime as dt
from pydantic import BaseModel, ConfigDict, Field


class PersonOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    uid: str
    name: str
    image: str
    role: str | None = "Visitor"
    department: str | None = ""
    job_title: str | None = ""
    badge_number: str | None = ""
    access_level: str | None = "Standard Access"
    phone: str | None = ""
    email: str | None = ""
    gender: str | None = ""
    threat_level: str | None = "Low"
    notes: str | None = ""
    created: dt.datetime


class PersonStats(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    uid: str
    name: str
    image: str
    role: str | None = "Visitor"
    department: str | None = ""
    job_title: str | None = ""
    badge_number: str | None = ""
    access_level: str | None = "Standard Access"
    threat_level: str | None = "Low"
    notes: str | None = ""
    created: dt.datetime | None = None
    n: int
    last: dt.datetime | None = None


class PersonRename(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class PersonUpdate(BaseModel):
    name: str | None = None
    role: str | None = None
    department: str | None = None
    job_title: str | None = None
    badge_number: str | None = None
    access_level: str | None = None
    phone: str | None = None
    email: str | None = None
    gender: str | None = None
    threat_level: str | None = None
    notes: str | None = None


class Stay(BaseModel):
    cam: str
    point: str | None
    start: dt.datetime
    end: dt.datetime
    n: int
