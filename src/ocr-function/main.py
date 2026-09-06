import base64
import io
import json
import re
import time
import urllib.request
import urllib.error

import functions_framework
from flask import jsonify

API = "https://api.makkyo.net/v1/chat/completions"
MODEL = "Qwen/Qwen3.6-35B-A3B-FP8"

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


@functions_framework.http
def ocr_signup_sheet(request):
    """HTTP Cloud Function entry point.
    Expects JSON body: {"image_base64": "<raw image bytes, base64>"}
    """
    body = request.get_json(silent=True) or {}
    img_b64 = body.get("image_base64")
    if not img_b64:
        return jsonify({"error": "missing image_base64"}), 400

    try:
        photo_bytes = base64.b64decode(img_b64)
        result = ocr_sheet(photo_bytes)
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 502