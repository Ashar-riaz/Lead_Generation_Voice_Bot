"""Prepare the two .env files without overwriting existing credentials.

Run from any folder: python path/to/wtd-lead-gen/setup_local.py
Uses only the Python standard library. Secrets are saved locally, never printed.
"""
from pathlib import Path
import secrets
import base64

ROOT = Path(__file__).resolve().parent


def values(path):
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines() if path.exists() else []:
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def put_missing(path, updates):
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    current = values(path)
    for key, value in updates.items():
        if current.get(key):
            continue
        for i, line in enumerate(lines):
            if line.startswith(key + "="):
                lines[i] = f"{key}={value}"
                break
        else:
            lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def main():
    backend, frontend = ROOT / ".env", ROOT / "frontend" / ".env.local"
    if not backend.exists():
        backend.write_text((ROOT / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")
    if not frontend.exists():
        frontend.write_text((ROOT / "frontend" / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")
    existing_backend, existing_frontend = values(backend), values(frontend)
    key = existing_backend.get("API_KEY") or existing_frontend.get("API_KEY") or secrets.token_urlsafe(32)
    put_missing(backend, {"API_KEY": key, "TOKEN_ENCRYPTION_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
                          "MICROSOFT_TENANT_ID": "", "MICROSOFT_CLIENT_ID": "", "MICROSOFT_CLIENT_SECRET": "",
                          "MICROSOFT_REDIRECT_URI": "http://localhost:3000/api/auth/microsoft/callback"})
    put_missing(frontend, {"BACKEND_URL": "http://127.0.0.1:8000", "API_KEY": key, "DASHBOARD_ORIGIN": "http://localhost:3000", "COOKIE_SECURE": "false"})
    if values(backend).get("API_KEY") != values(frontend).get("API_KEY"):
        print("The API_KEY values are different. Set frontend/.env.local API_KEY to match backend .env.")
    else:
        print("Environment files are ready. Add live ZoomInfo credentials to the root .env.")
    print("Microsoft setup is optional for email sending: see docs/MICROSOFT_SETUP.md.")
    print("Open http://localhost:3000. The dashboard opens directly without login.")
    print("Backend:  python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000")
    print("Frontend: cd frontend, then npm install and npm run dev")


if __name__ == "__main__":
    main()
