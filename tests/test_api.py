import os, sys, types
os.environ["DATABASE_URL"] = "sqlite:///data/test.db"
sys.modules.setdefault("huggingface_hub", types.SimpleNamespace(hf_hub_download=lambda *a, **k: "x.onnx"))
from fastapi.testclient import TestClient
from app.main import app


def test_flow():
    with TestClient(app) as c:
        # Health check
        assert c.get("/api/v1/health").json()["status"] == "ok"
        # Direct access without authentication
        assert c.get("/api/v1/stats").status_code == 200
        assert c.get("/").status_code == 200
        # Login redirects to /
        assert c.get("/login").status_code in (200, 303)
        assert c.get("/api/v1/detections").status_code == 200
        assert c.get("/api/v1/system/telemetry").status_code == 200
        telemetry = c.get("/api/v1/system/telemetry").json()
        assert "cpu_percent" in telemetry
        assert "memory_percent" in telemetry
        assert "db_size_mb" in telemetry

        # Export sightings CSV
        export_res = c.get("/api/v1/export/sightings.csv")
        assert export_res.status_code == 200
        assert "text/csv" in export_res.headers.get("content-type", "")

        # Alerts endpoints
        assert c.get("/api/v1/alerts").status_code == 200
        assert c.post("/api/v1/alerts/clear").status_code == 200

        # Ask endpoint responds without auth
        ask_res = c.post("/api/v1/ask", json={"question": "Who was seen today?"})
        assert ask_res.status_code == 200
        assert "answer" in ask_res.json()
        assert c.post("/api/v1/cameras/999/points/toggle", json={"x": .5, "y": .5}).status_code == 404
        for p in ("/cameras", "/live", "/people", "/sightings", "/ask", "/alerts"):
            assert c.get(p).status_code == 200, p
