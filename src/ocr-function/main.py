import base64
import io
import json
import os
import logging
logging.basicConfig(level=logging.INFO)
import re
import time
import urllib.request
import urllib.error
import google.auth
import functions_framework

from flask import jsonify
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from googleapiclient.http import MediaIoBaseUpload

INGEST_FOLDER_ID = "1hrpUcdFpjfTee-AS2LZINXJ8r2iQOw_e"
REVIEW_FOLDER_ID = "152xJTWCOuabDVqkTwWY8uKatK-Z77Y91"
PROCESSED_FOLDER_ID = "1vtJp9upDYHT7jqq2-BMRXbG4X5cy9xKf"
MAX_FILES_PER_RUN = 5

API = "https://api.makkyo.net/v1/chat/completions"
MODEL = "Qwen/Qwen3.6-35B-A3B-FP8"

_creds, _ = google.auth.default(scopes=[
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
])
drive = build("drive", "v3", credentials=_creds)
sheets = build("sheets", "v4", credentials=_creds)

TOOL = {"type": "function", "function": {
    "name": "record_signups", "description": "Record the sign-up sheet rows",
    "parameters": {"type": "object", "required": ["rows"], "properties": {
        "rows": {"type": "array", "items": {"type": "object",
            "required": ["name", "email", "phone"], "properties": {
                "name": {"type": "string"}, "email": {"type": "string"},
                "phone": {"type": "string"}}}}}}}}


def get_api_key() -> str:
    # Pull from Secret Manager / env var — see step 4. Never hardcode.
    import os
    return os.environ["MAKKYO_API_KEY"]


def preprocess(photo_bytes: bytes, shrink: float = 1.0) -> bytes:
    """pad-scale-v2: EXIF-rotate -> fit 1044x624 -> 1.5x LANCZOS -> 80px top pad."""
    from PIL import Image, ImageOps
    sheet = ImageOps.exif_transpose(Image.open(io.BytesIO(photo_bytes))).convert("RGB")
    sheet.thumbnail((int(1044 * shrink), int(624 * shrink)))
    scaled = sheet.resize((int(sheet.width * 1.5), int(sheet.height * 1.5)), Image.LANCZOS)
    canvas = Image.new("RGB", (scaled.width, scaled.height + 80), (250, 250, 248))
    canvas.paste(scaled, (0, 80))
    out = io.BytesIO()
    canvas.save(out, "PNG")
    return out.getvalue()


def call(body: dict, timeout: int = 60) -> dict:
    req = urllib.request.Request(API, json.dumps(body).encode(), {
        "Authorization": f"Bearer {get_api_key()}", "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def extract(png: bytes) -> list[dict]:
    uri = "data:image/png;base64," + base64.b64encode(png).decode()
    resp = call({"model": MODEL, "temperature": 0, "max_tokens": 1024,
                 "tools": [TOOL], "tool_choice": "required",
                 "messages": [{"role": "user", "content": [
                     {"type": "text", "text":
                      "Extract every person from this handwritten sign-up sheet photo."},
                     {"type": "image_url", "image_url": {"url": uri}}]}]})
    calls = resp["choices"][0]["message"].get("tool_calls") or []
    return json.loads(calls[0]["function"]["arguments"]).get("rows", []) if calls else []


def engine_alive() -> bool:
    """Real health check — the doc warns /health lies while inference is dead."""
    try:
        r = call({"model": MODEL, "temperature": 0, "max_tokens": 200,
                  "messages": [{"role": "user", "content": "What is 2+2?"}]}, timeout=30)
        return "4" in (r["choices"][0]["message"]["content"] or "")
    except Exception:
        return False


def validate(row: dict) -> list[str]:
    flags = []
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", row.get("email", "")):
        flags.append("email_shape")
    digits = re.sub(r"\D", "", row.get("phone", ""))
    if len(digits) != 10:
        flags.append("phone_shape")
    return flags


def ocr_sheet(photo_bytes: bytes) -> dict:
    shrink, degenerate_seen = 1.0, False
    for attempt in range(3):
        try:
            rows = extract(preprocess(photo_bytes, shrink))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(int(e.headers.get("Retry-After", 15)))
                continue
            if e.code >= 500:
                deadline = time.time() + 90
                while time.time() < deadline and not engine_alive():
                    time.sleep(10)
                shrink *= 0.75  # never resend identical bytes after a 5xx
                continue
            raise
        if len(rows) > 50:  # degeneration guard
            if degenerate_seen:
                raise RuntimeError("degenerate output twice - give up")
            degenerate_seen = True
            continue
        return {"rows": [dict(r, flags=validate(r)) for r in rows],
                "warnings": ["zero_rows_detected"] if not rows else []}
    raise RuntimeError("retries exhausted")

def list_new_images(folder_id: str) -> list[dict]:
    """List image files directly in the ingest folder (no subfolders)."""
    resp = drive.files().list(
        q=f"'{folder_id}' in parents and trashed=false and "
          f"(mimeType='image/jpeg' or mimeType='image/png')",
        fields="files(id, name)",
        supportsAllDrives=True, 
        includeItemsFromAllDrives=True
    ).execute()
    return resp.get("files", [])


def download_file(file_id: str) -> bytes:
    request = drive.files().get_media(fileId=file_id, supportsAllDrives=True)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buf.getvalue()

def create_review_sheet(source_name: str, source_file_id: str, rows: list[dict]) -> str:
    """Create a new Sheet directly inside the review folder (avoids service account storage quota issues)."""
    title = f"Review - {source_name}"

    file_metadata = {
        "name": title,
        "mimeType": "application/vnd.google-apps.spreadsheet",
        "parents": [REVIEW_FOLDER_ID],
    }
    file = drive.files().create(
        body=file_metadata, fields="id", supportsAllDrives=True
    ).execute()
    sheet_id = file["id"]

    header = ["name", "email", "phone", "flags"]
    values = [header]
    for r in rows:
        row = []
        for h in header:
            val = r.get(h, "")
            if isinstance(val, list):
                val = ", ".join(val)
            row.append(val)
        values.append(row)
    values.append([])
    values.append([f"Source image: https://drive.google.com/file/d/{source_file_id}/view"])

    sheets.spreadsheets().values().update(
        spreadsheetId=sheet_id, range="Sheet1!A1",
        valueInputOption="RAW", body={"values": values},
    ).execute()

    return sheet_id

def move_to_processed(file_id: str):
    file = drive.files().get(fileId=file_id, fields="parents", supportsAllDrives=True).execute()
    prev_parents = ",".join(file.get("parents", []))
    drive.files().update(
        fileId=file_id, addParents=PROCESSED_FOLDER_ID,
        removeParents=prev_parents, fields="id, parents",
        supportsAllDrives=True
    ).execute()

def debug_upload(data: bytes, name: str):
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype="image/jpeg")
    drive.files().create(
        body={"name": name, "parents": [REVIEW_FOLDER_ID]},
        media_body=media, fields="id", supportsAllDrives=True,
    ).execute()

def poll_and_process():
    results = []

    files = list_new_images(INGEST_FOLDER_ID)[:MAX_FILES_PER_RUN]
    logging.info(f"Found {len(files)} files to process this run (capped at {MAX_FILES_PER_RUN})")
    for f in files:
        photo_bytes = None
        try:
            photo_bytes = download_file(f["id"])
            logging.info(f"{f['name']}: downloaded {len(photo_bytes)} bytes")
            result = ocr_sheet(photo_bytes)
            create_review_sheet(f["name"], f["id"], result["rows"])
            move_to_processed(f["id"])
            results.append({"file": f["name"], "status": "ok", "rows": len(result["rows"])})
        except Exception as e:
            if photo_bytes:
                try:
                    debug_upload(photo_bytes, f"debug-{f['name']}")
                except Exception as upload_err:
                    logging.error(f"debug upload also failed: {upload_err}")
            results.append({"file": f["name"], "status": "error", "error": str(e)})
    return results

@functions_framework.http
def ocr_signup_sheet(request):
    """Now poll-driven: ignores request body, scans ingest folder for new images."""
    try:
        results = poll_and_process()
        return jsonify({"processed": results}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 502