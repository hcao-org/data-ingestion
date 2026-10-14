import io
import logging
logging.basicConfig(level=logging.INFO)
import time
import google.auth
import functions_framework

from flask import jsonify
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from googleapiclient.http import MediaIoBaseUpload

from ocr_recipe import DELIVERED_FIELDS, ocr_sheet

INGEST_FOLDER_ID = "1hrpUcdFpjfTee-AS2LZINXJ8r2iQOw_e"
REVIEW_FOLDER_ID = "152xJTWCOuabDVqkTwWY8uKatK-Z77Y91"
PROCESSED_FOLDER_ID = "1vtJp9upDYHT7jqq2-BMRXbG4X5cy9xKf"
MAX_FILES_PER_RUN = 5
# The function times out at 300s and a sheet takes ~30s p50 (worst seen ~55s) for the three passes,
# so don't start a new sheet once this much of the run has gone; the next scheduled run picks it up.
START_CUTOFF_SECONDS = 210

_creds, _ = google.auth.default(scopes=[
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
])
drive = build("drive", "v3", credentials=_creds)
sheets = build("sheets", "v4", credentials=_creds)

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

def create_review_sheet(source_name: str, source_file_id: str, result: dict) -> str:
    """Create a new Sheet directly inside the review folder (avoids service account storage quota issues)."""
    title = f"Review - {source_name}"
    if result["review_recommended"]:
        title = f"[CHECK] {title}"

    file_metadata = {
        "name": title,
        "mimeType": "application/vnd.google-apps.spreadsheet",
        "parents": [REVIEW_FOLDER_ID],
    }
    file = drive.files().create(
        body=file_metadata, fields="id", supportsAllDrives=True
    ).execute()
    sheet_id = file["id"]

    # A blank header with no warning can mean an empty block, a form without one, or a withheld
    # (likely invented) header — the recipe can't tell these apart, so we just show what came back.
    header = result["header"]
    values = [
        ["Event", header["event"]],
        ["Location", header["location"]],
        ["Date", header["date"]],
        ["Warnings", ", ".join(result["warnings"])],
        ["Review recommended", "yes" if result["review_recommended"] else "no"],
        [],
    ]
    columns = [*DELIVERED_FIELDS, "flags"]
    values.append(columns)
    for r in result["rows"]:
        values.append([r[c] if c != "flags" else ", ".join(r["flags"]) for c in columns])
    values.append([])
    values.append([f"Source image: https://drive.google.com/file/d/{source_file_id}/view"])
    values.append([f"OCR recipe: {result['config']}"])

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
    started = time.monotonic()

    files = list_new_images(INGEST_FOLDER_ID)[:MAX_FILES_PER_RUN]
    logging.info(f"Found {len(files)} files to process this run (capped at {MAX_FILES_PER_RUN})")
    for f in files:
        if time.monotonic() - started > START_CUTOFF_SECONDS:
            logging.info(f"{f['name']}: deferred to next run (time budget)")
            results.append({"file": f["name"], "status": "deferred"})
            continue
        photo_bytes = None
        try:
            photo_bytes = download_file(f["id"])
            logging.info(f"{f['name']}: downloaded {len(photo_bytes)} bytes")
            result = ocr_sheet(photo_bytes)
            logging.info(f"{f['name']}: {len(result['rows'])} rows, warnings={result['warnings']}, "
                         f"review_recommended={result['review_recommended']}")
            create_review_sheet(f["name"], f["id"], result)
            move_to_processed(f["id"])
            results.append({"file": f["name"], "status": "ok", "rows": len(result["rows"]),
                            "warnings": result["warnings"],
                            "review_recommended": result["review_recommended"]})
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