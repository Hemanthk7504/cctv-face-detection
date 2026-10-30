from sqlalchemy import create_engine, select, event, text
from sqlalchemy.orm import sessionmaker
from app.core.config import settings

connect_args = {"check_same_thread": False, "timeout": 30.0} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, pool_pre_ping=True, connect_args=connect_args)

if settings.database_url.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

S = sessionmaker(engine, expire_on_commit=False)


def get_db():
    with S() as d:
        yield d


def init_db():
    from app.db.models import Base, User, Alert, AuditLog
    from app.core.security import hash_password
    Base.metadata.create_all(engine)
    
    # Safe migration for new columns in SQLite
    with engine.connect() as con:
        try:
            cols_p = [r[1] for r in con.execute(text("PRAGMA table_info(persons)")).fetchall()]
            if cols_p and "role" not in cols_p:
                con.execute(text("ALTER TABLE persons ADD COLUMN role VARCHAR(50) DEFAULT 'Visitor'"))
            if cols_p and "notes" not in cols_p:
                con.execute(text("ALTER TABLE persons ADD COLUMN notes TEXT DEFAULT ''"))
            if cols_p and "department" not in cols_p:
                con.execute(text("ALTER TABLE persons ADD COLUMN department VARCHAR(100) DEFAULT ''"))
            if cols_p and "threat_level" not in cols_p:
                con.execute(text("ALTER TABLE persons ADD COLUMN threat_level VARCHAR(20) DEFAULT 'Low'"))
            if cols_p and "job_title" not in cols_p:
                con.execute(text("ALTER TABLE persons ADD COLUMN job_title VARCHAR(100) DEFAULT ''"))
            if cols_p and "badge_number" not in cols_p:
                con.execute(text("ALTER TABLE persons ADD COLUMN badge_number VARCHAR(50) DEFAULT ''"))
            if cols_p and "access_level" not in cols_p:
                con.execute(text("ALTER TABLE persons ADD COLUMN access_level VARCHAR(80) DEFAULT 'Standard Access'"))
            if cols_p and "phone" not in cols_p:
                con.execute(text("ALTER TABLE persons ADD COLUMN phone VARCHAR(50) DEFAULT ''"))
            if cols_p and "email" not in cols_p:
                con.execute(text("ALTER TABLE persons ADD COLUMN email VARCHAR(120) DEFAULT ''"))
            if cols_p and "gender" not in cols_p:
                con.execute(text("ALTER TABLE persons ADD COLUMN gender VARCHAR(30) DEFAULT ''"))

            cols_c = [r[1] for r in con.execute(text("PRAGMA table_info(cameras)")).fetchall()]
            if cols_c and "location" not in cols_c:
                con.execute(text("ALTER TABLE cameras ADD COLUMN location VARCHAR(120) DEFAULT ''"))
            if cols_c and "loop" not in cols_c:
                con.execute(text("ALTER TABLE cameras ADD COLUMN loop BOOLEAN DEFAULT 1"))
            if cols_c and "zone" not in cols_c:
                con.execute(text("ALTER TABLE cameras ADD COLUMN zone VARCHAR(80) DEFAULT 'Public Area'"))
            if cols_c and "is_restricted" not in cols_c:
                con.execute(text("ALTER TABLE cameras ADD COLUMN is_restricted BOOLEAN DEFAULT 0"))
            if cols_c and "dwell_threshold_s" not in cols_c:
                con.execute(text("ALTER TABLE cameras ADD COLUMN dwell_threshold_s INTEGER DEFAULT 90"))

            cols_d = [r[1] for r in con.execute(text("PRAGMA table_info(detections)")).fetchall()]
            if cols_d and "crop_path" not in cols_d:
                con.execute(text("ALTER TABLE detections ADD COLUMN crop_path VARCHAR(255)"))

            con.commit()
        except Exception as e:
            print(f"Migration note: {e}")

    with S() as d:
        if not d.scalar(select(User)):
            d.add(User(username=settings.admin_user, password=hash_password(settings.admin_pass)))
            d.commit()
