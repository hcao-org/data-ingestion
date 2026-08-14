# OCR over the Public API — how to use `api.makkyo.net` for sign-up-sheet extraction

> How-to guide. Scope: driving `https://api.makkyo.net` **exclusively as an OCR
> engine** for sign-up sheets — not as a general chat endpoint. Companion
> reference: [openapi-public.yaml](../architecture/openapi-public.yaml).
> Every number here traces to measured evidence in `SPIKE-RESULTS.md` (A2–A8).

**If you are on the home LAN or Sky's tailnet, stop here and use
`signup-ocr-api`** (`http://promaxgb10-c92c.tailb30dbf.ts.net:8891` — see
[openapi.yaml](../architecture/openapi.yaml)). It implements everything below
for you, with self-healing the public path cannot offer. This guide exists for
clients that only have the public internet route.

---

## The one rule

The public endpoint is a raw model API. Accuracy (field_f1 **0.948 ± 0.003**)
was measured for **one exact configuration** — `pad-scale-v2`. Deviate from the
recipe (different prompt, no preprocessing, temperature > 0, free-text output
instead of the tool schema) and you are running an unmeasured configuration:
expect the baseline ~0.86 or worse, plus row misses. Use the recipe verbatim.

## The recipe (pad-scale-v2, frozen)

Preprocess the photo, then send one fixed request per sheet.

**Preprocessing** (Pillow, in this exact order — provenance
`src/signup_ocr/core.py`, golden-pinned to the eval harness):

1. EXIF-rotate (`ImageOps.exif_transpose`), convert to RGB.
2. Fit within **1044 × 624** (`thumbnail` — shrink only, keep aspect). This is
   a validated crash-safe dimension class; skipping it risks wedging the
   engine (see "When it breaks").
3. Upscale **1.5× LANCZOS** — scale FIRST.
4. THEN pad **80 px on top** with paper color `(250, 250, 248)`.
5. Encode PNG, inline as a base64 `data:` URI. The server does **not** fetch
   remote image URLs.

Step 4 is not cosmetic: without the top pad the model deterministically drops
the first row of the sheet (a top-band vision artifact, SPIKE A7), and the
dropped row cascades into name/email confabulations in later rows.

**Request** (differences from a generic chat call are load-bearing):

- `temperature: 0`, `max_tokens: 1024` — the budget must cover reasoning
  tokens; the model thinks before answering and that spend comes out of
  `max_tokens`.
- Structured output via a **tool call with `tool_choice: "required"`** —
  XGrammar guarantees schema-valid JSON, so there is no fragile text parsing.
- Prompt, verbatim: `Extract every person from this handwritten sign-up sheet
  photo.` Do not "improve" it — instruction-stuffing measurably scored worse
  (SPIKE A7, `strict-verbatim` 0.825 vs 0.955).
- One image per request. Never batch sheets into one call.

## Complete client (Python, stdlib + Pillow)

```python
import base64, io, json, re, time, urllib.request

API = "https://api.makkyo.net/v1/chat/completions"
KEY = "<edge key>"          # OpenAI SDK users: base_url=https://api.makkyo.net/v1, api_key=KEY
MODEL = "Qwen/Qwen3.6-35B-A3B-FP8"

TOOL = {"type": "function", "function": {
    "name": "record_signups", "description": "Record the sign-up sheet rows",
    "parameters": {"type": "object", "required": ["rows"], "properties": {
        "rows": {"type": "array", "items": {"type": "object",
            "required": ["name", "email", "phone"], "properties": {
                "name": {"type": "string"}, "email": {"type": "string"},
                "phone": {"type": "string"}}}}}}}}

def preprocess(photo_bytes: bytes, shrink: float = 1.0) -> bytes:
    """pad-scale-v2: EXIF-rotate -> fit 1044x624 -> 1.5x LANCZOS -> 80px top pad."""
    from PIL import Image, ImageOps
    sheet = ImageOps.exif_transpose(Image.open(io.BytesIO(photo_bytes))).convert("RGB")
    sheet.thumbnail((int(1044 * shrink), int(624 * shrink)))
    scaled = sheet.resize((int(sheet.width * 1.5), int(sheet.height * 1.5)), Image.LANCZOS)
    canvas = Image.new("RGB", (scaled.width, scaled.height + 80), (250, 250, 248))
    canvas.paste(scaled, (0, 80))
    out = io.BytesIO(); canvas.save(out, "PNG"); return out.getvalue()

def call(body: dict, timeout: int = 60) -> dict:
    req = urllib.request.Request(API, json.dumps(body).encode(), {
        "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
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
    """Real health check. NEVER trust GET /health - it stays 'ready' while inference is dead."""
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
            if e.code == 429:                       # edge rate limit or busy
                time.sleep(int(e.headers.get("Retry-After", 15))); continue
            if e.code >= 500:                       # possible engine wedge
                deadline = time.time() + 90         # recovery is ~21-36s
                while time.time() < deadline and not engine_alive():
                    time.sleep(10)
                shrink *= 0.75                      # NEVER resend identical bytes
                continue
            raise
        if len(rows) > 50:                          # degeneration guard (~0.5% of calls)
            if degenerate_seen:
                raise RuntimeError("degenerate output twice - give up")
            degenerate_seen = True; continue        # stochastic: plain retry is fine
        return {"rows": [dict(r, flags=validate(r)) for r in rows],
                "warnings": ["zero_rows_detected"] if not rows else []}
    raise RuntimeError("retries exhausted")
```

## Guard rails you must keep (and why)

| Guard | Why |
|---|---|
| Row ceiling (> 50 rows → discard and retry) | ~0.5 % of calls emit a schema-valid repetition loop (362 rows observed, SPIKE A8). It is stochastic — a plain retry works. Never return a degenerate reading. |
| Resize before any retry after a 5xx | The engine-crash trigger is **dimension-deterministic** (SPIKE A2). Resending identical bytes can never succeed and re-wedges the engine for everyone. |
| Health = tiny inference call, never `/health` | `/health` reports `ready` while inference is fully dead (SPIKE A2). |
| Shape validators on every row | The API reports no confidence scores. `email_shape` / `phone_shape` flags catch structural errors deterministically. |
| **Do not** re-run the same request as a confidence check | Measured near-useless at this operating point: identical re-runs agree 98 % of the time *including on the errors* (SPIKE A7/A8 — errors are systematic, not noise). An ensemble of 10 runs scored no better than one run. |

## Accuracy expectations — what remains wrong

At field_f1 0.948, residual errors are concentrated and predictable:

- **Phone digits are the weak field** (exact ≈ 0.91). The dominant confusions
  are shape-similar glyph pairs — **5→6 and 3→8** — in cursive/grungy
  handwriting. These pass the `phone_shape` validator by construction (right
  digit count, wrong digit), so flag-free rows are *not* guaranteed correct.
- Names ≈ 100 % after preprocessing; emails occasionally lose `_` → `.`.
- Practical consequence: if downstream use is contact-critical, human-review
  the phone column, or verify by out-of-band means (e.g. SMS confirmation).

## Etiquette and limits (shared, personal infrastructure)

- **Concurrency 1.** The GPU serves Sky's coding agents concurrently
  (`max_batch_size 2`); one in-flight OCR request is both the measured
  configuration and the good-neighbor policy. Expect **7–12 s per sheet** —
  budget client timeouts at ≥ 60 s per attempt.
- Edge rate limit is 120 req/min/IP (429 + `Retry-After`); irrelevant at
  concurrency 1 unless you tight-loop retries — don't.
- Keep uploads lean: after preprocessing, sheets are a few hundred KB of PNG.
  Egress past 1 GB/month is metered; this endpoint is for token streams, not
  file serving.
- A sustained 500 on *every* request (including the 2+2 probe) means the
  engine is down for all clients. Back off minutes, not seconds; if it
  persists, contact Sky rather than hammering the retry loop.
