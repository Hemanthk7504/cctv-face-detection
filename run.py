import os
import socket
import uvicorn


def is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('127.0.0.1', port)) == 0


if __name__ == "__main__":
    default_port = 8000 if not is_port_in_use(8000) else 8001
    port = int(os.getenv("PORT", default_port))
    print(f"Starting FaceTrack CCTV Surveillance Engine on port {port}...")
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=False)
