# OCR over the Public API — how to use `api.makkyo.net` for sign-up-sheet extraction

> How-to guide. Scope: driving `https://api.makkyo.net` **exclusively as an OCR
> engine** for HCAO sign-up sheets — not as a general chat endpoint. Companion
> reference: [openapi-public.yaml](../architecture/openapi-public.yaml).
> Every number here traces to measured evidence in `SPIKE-RESULTS.md` (A2–A9e)
> and `docs/product/kpi-contracts.yaml` (measured blocks), accuracy on dataset v5
> (`ocr-eval/runs/pad-scale-v5-hdr`, `-r2`, `-r3` — the recipe's three scored
> sessions).
>
> **Recipe `pad-scale-v5-hdr` — ADR-009 Amendment A, RATIFIED by Sky
> 2026-10-08.** It is the recipe `pad-scale-v5-addr3` with the sheet's
> **header** — event, location, date — added to its second call.
> `pad-scale-v5-hdr` has **three scored sessions (2026-10-07)**: they are its
> run of record, and every accuracy figure below is **the median of the three
> sessions** (ADR-009 Amendment A §A7 and "Measured claims"; that a document
> restating the label's figures gives that median is the orchestrator's rule,
> 2026-10-08). The header's figures are the synthetic sheets': no real sheet
> has a labeled header yet — the gate's real-tier header lines print
> "missing" — so **nothing here is an accuracy claim for the header on real
> photographs**. The LAN service that implements this recipe is deployed by
> Sky 2026-10-12 (tag `deploy-pad-scale-v5-hdr-20261012` at `61bd99a`); since
> that deploy the running service reports `pad-scale-v5-hdr`.

**If you are on the home LAN or Sky's tailnet, stop here and use
`signup-ocr-api`** (`http://promaxgb10-c92c.tailb30dbf.ts.net:8891` — see
[openapi.yaml](../architecture/openapi.yaml)). It implements everything below
for you, with self-healing the public path cannot offer. This guide exists for
clients that only have the public internet route.

---

## The one rule

The public endpoint is a raw model API: it returns whatever the tool schema you
send asks for, and nothing more. Accuracy was measured for **one exact
configuration** — `pad-scale-v5-hdr` (dataset v5, three scored sessions,
2026-10-07; each figure the median of the three): zip exact **0.891**,
street & city exact **0.713** (synthetic sheets; real sheets 0.976 / 0.857),
identity fields (`legacy_field_f1`, all 33 sheets) **0.903**, name parts exact
**0.970**; the header's event and location exact **0.929** and its date exact
**0.929** (synthetic sheets with a header block — no real sheet has a labeled
header). Its first request is `pad-scale-v5-addr3`'s byte for byte (the
`pad-scale-v3-addr3` requests); its second is that recipe's with three header
properties added (see *Accuracy expectations*). Deviate from the
recipe (different prompt, no preprocessing, temperature > 0, a single
five-field tool instead of the two passes, free-text output instead of the tool
schema) and you are running an unmeasured configuration: expect the baseline
~0.86 or worse on the identity fields, an undecodable or truncated reply on a
single five-field call (SPIKE-01), plus row misses. Use the recipe verbatim —
the code block below is the reference implementation, and the repository's
doc-contract test executes it against the served service (K8).

## What to photograph

- **One sheet per image.** Each image must contain exactly one sign-up sheet
  (one HCAO page, at most 8 rows). Multi-page or multi-sheet photos are
  unsupported and not a planned feature (D10).
- A photo with two sheets is read as one sheet: the rows of both come back together with `rows_exceed_sheet_capacity` (expected, not a defect) — crop the photo to one sheet per image and resend (DD-19).
- The HCAO form is landscape — photograph it in landscape (hold the phone sideways). A portrait frame of the landscape form keeps ~25 % less linear resolution on every cell; the recipe accepts either orientation (the envelope rule below), but landscape framing is the measured one.
- Photograph the whole sheet — both the left and the right edge inside the frame, one sheet per image: a column cut off by the frame reads as blank, the same as one nobody filled in (OQ-5).

## The recipe (pad-scale-v5-hdr)

Preprocess the photo, then send **two sequential requests per sheet** with the
same image and the same prompt: pass 1 asks for the identity triple
(`name` / `email` / `phone` — byte-identical to the previous `pad-scale-v3`
request), pass 2 asks for the address triple (`name` / `street_city` / `zip`)
and for the sheet's header (`event` / `location` / `date`) with
the address tool. `merge()` pairs the two readings by the whole name
into five-field rows; `split_name()` and `format_phone()` present each merged
row (the name in `first_name` / `middle_name` / `last_name`, the phone in the
house style); `validate()` flags each presented row. `validate_header()` judges
the header exactly as pass 2 returned it, and only what it keeps is interpreted
(`iso_date()` for the date). There is no third request.

**Preprocessing** (Pillow, in this exact order — provenance
`src/signup_ocr/core.py` `normalize` + `preprocess_pad_scale_v2`, golden-pinned
to the eval harness; the repository's parity test holds `preprocess()` below to
them byte for byte):

1. EXIF-rotate (`ImageOps.exif_transpose`), convert to RGB.
2. Fit within the envelope of the upright orientation (`thumbnail` — shrink
   only, keep aspect): **1044 × 624** when the photo is landscape (width ≥
   height), **780 × 1040** when it is portrait (DD-13a).
3. Letterbox unless the fitted size is a proven one (ADR-007 Amendment C).
   Proven: **1044 × 624**, **998 × 624** and **832 × 624** for a landscape
   photo, **780 × 1040** for a portrait one — such a sheet goes on as fitted.
   Any other fitted size (a 16:9, 3:2, 2:1 or square photo, or one smaller
   than its envelope) is pasted, unchanged, at the top-left corner `(0, 0)` of
   a pure white `(255, 255, 255)` canvas the size of its envelope — never
   enlarged, never centered.
4. Upscale **1.5× LANCZOS** — scale FIRST.
5. THEN pad **80 px on top** with paper color `(250, 250, 248)`.
6. Encode PNG, inline as a base64 `data:` URI. The server does **not** fetch
   remote image URLs.

Step 3 is not optional: the engine can stop answering for everyone — every
caller of this shared endpoint, not only you — when it is sent an image size it
has not handled before, and the only way to learn whether a new size is safe is
to risk taking it down (SPIKE A2, A10: a 1044 × 531 image, the bare fit of a
2:1 photo, did exactly that). So a sheet is only ever sent at a size the
engine has already read one at: after steps 4–5 that is **1566 × 1016**,
**1497 × 1016**, **1248 × 1016** or **1170 × 1640**, whatever the photo's
shape.

**A retry sends the same picture again.** The picture of steps 1–6 is prepared
once per photo and every request `ocr_sheet` below makes for the sheet carries
those bytes. So every sending of a sheet has the size of its first one — a
retry cannot leave the four proven sizes. The repository's parity test holds
every sending to the service's own, byte for byte. This is ADR-007 Amendment D
as revised (2026-10-07), **ratified by Sky 2026-10-08**.

*When the client sends a sheet again, and how often.* `ocr_sheet` makes up to
three tries at the identity reading. It starts a new try after a 5xx — once the
engine answers the 2+2 probe again, or after 90 s — and, as the code is written,
after any other HTTP error status that is not a 429. A try is not one request:
inside it `read_pass` sends the pass again after each 429 (having waited
`Retry-After`) and once after a reading of more than 16 rows, three sendings at
most. So one picture can go out in up to 9 identity requests, and in up to 3
address requests — the address reading is one `read_pass` and is never tried
again. An engine that answers a 5xx every time is sent the sheet 3 times. These
loops are the client's own and older than the amendment; what the amendment
changes is that every one of those requests is the same bytes.

*What the client does not do.* It does **not** detect a cut-off reading:
`extract` never looks at `finish_reason`, so a reply that finished `length` is
not sent again. If such a reply carries no tool call, the sheet comes back as
empty (`zero_rows_detected`, no review recommended) after an address request
has been spent on it; if its arguments stop mid-JSON, `ocr_sheet` raises
`json.JSONDecodeError`; if they still decode, the partial rows are delivered as
a whole reading. `signup-ocr-api` discards such a reading, reads the sheet once
more and marks a delivered sheet for review. The same goes for the address
reading, which carries the header: a cut-off pass 2, or one whose `rows` is not
a list (read as no rows), is not detected. Beside signers it is a failed
address pass when it comes back without rows, and is delivered whole — header
included — when its arguments still decode with rows. On a sheet on which pass
1 read no signer it is taken as answered: the header it carried is delivered
as read and validated — three `""` when it carried none — with no
`header_unread` and no review recommended, where `signup-ocr-api` reports
`address_pass_failed` and `header_unread` and recommends review.

*What was measured* — two exploratory experiments, neither a scored run. The
amendment's first wording had a retry carry the sheet at 0.75× (then 0.5625×)
of its size on a white canvas: sheets sent that way did not read (E17). The
same request sent again at once returned another reply on about half of
back-to-back pairs, the cells no better and no worse for it; with a picture
that is often lost, the sending right after a lost one read 30 times in 76; by
the rule fixed before the run that experiment reads **MIXED** (E19). The one
cut-off it caught was read by the re-sending: one event — and a cut-off is what
this client does not send again. (Extended 2026-10-08: by then E19's catch
rounds hold 8 lost readings in 1,980, each read by the first same-bytes
re-sending — `SPIKE-RESULTS.md` A29.) **Nothing was measured after a 5xx**, the
failure this client does retry: neither experiment met an engine error. No
accuracy is claimed for a sheet read again.

Step 5 is not cosmetic: without the top pad the model deterministically drops
the first row of the sheet (a top-band vision artifact, SPIKE A7), and the
dropped row cascades into name/email confabulations in later rows.

**Requests** (differences from a generic chat call are load-bearing):

- `temperature: 0`; `max_tokens: 1024` on pass 1 and `1536` on pass 2 — the
  budget must cover reasoning tokens; the model thinks before answering and
  that spend comes out of `max_tokens`. Pass 1 is the unchanged `pad-scale-v3`
  request; pass 2's 1536 is 1.25 × the largest address reading observed
  (1178 completion tokens, ADR-007 §2).
- Structured output via a **tool call with `tool_choice: "required"`** —
  XGrammar guarantees schema-valid JSON, so there is no fragile text parsing.
  Pass 1 sends `TOOL`, pass 2 sends `ADDRESS_TOOL`; nothing else changes
  between the two calls.
- `ADDRESS_TOOL` declares `event`, `location` and `date` **before** `rows`, as
  three plain required strings and nothing more. Do not give them a
  `description`: any property description garbled the address reading when it
  was measured (undecodable and cut-off replies, zip exact down to 0.58–0.70 —
  SPIKE-RESULTS A14, A18). And do not add a "is there a header?" property:
  without a description it read the addresses level with the control and
  decided nothing — it said `yes` on 0 of 14 header blocks (A18).
- Prompt, verbatim on both passes: `Extract every person from this handwritten
  sign-up sheet photo.` Do not "improve" it — instruction-stuffing measurably
  scored worse (SPIKE A7, `strict-verbatim` 0.825 vs 0.955).
- One image per request. Never batch sheets into one call.

**Merge** (DD-11, mirrors the service): every pass-1 row becomes exactly one
five-field row, in pass-1 order. Rows pair by normalized name (strip, collapse
whitespace, casefold) with greedy best-first similarity `1 − lev / max(len)`
≥ 0.5; blank names never pair by name. Rows both passes left free pair by
position only when the two passes read the same number of rows. Pass-2 rows
without a partner are dropped (never invented); pass-1 rows without a partner
get `street_city: ""` / `zip: ""`.

**Present** (ADR-010, mirrors the service's `split_names` / `format_phones`;
one shared fixture table pins the service, the harness reference and this
copy): after the merge — which still keys on the whole name — each row loses
`name` and gains `first_name` / `middle_name` / `last_name`. `split_name()`
collapses whitespace; peels a trailing `Jr.` / `Sr.` / `II` / `III` / `IV`
(while another word remains) and appends it to the surname; reads a comma as
`Last, First Middle`; otherwise takes the first word as the first name and the
last word as the surname, extended leftwards over particles (`de`, `la`, `van`,
`von`, `St.`, `bin`, …), with the words between as the middle name. Case is
kept as read; a hyphenated name is one word; a compound first name without a
comma (`Mary Ann Lee`) splits as first `Mary`, middle `Ann` — a stated
limitation. `format_phone()` formats only a reading made solely of phone
characters (ASCII digits, space, `( ) - .`, a leading `+`) with 10 digits, or
11 led by the country code 1 (dropped), as `503-555-0142`; anything else —
stray ink such as `503-555-0142 (h)`, a letter, another digit count — stays as
read (trimmed), so no ink is ever silently dropped, and `validate()` flags it
`phone_shape`, as it does a blank phone. Presentation runs before `validate()`,
so every flag judges the delivered row.

**Failed address pass**: when pass 2 errors, comes back empty or degenerate
(more than 16 rows), deliver the pass-1 rows anyway with blank addresses — every
row then carries `address_missing` meaning *unread*, not zip-only — add the
`address_pass_failed` warning and review the sheet by hand. Never fail the
whole reading because of the address pass. A sheet on which pass 1 read no
signer is asked for pass 2 like any other; an empty pass 2 is no failure
there — there is nobody whose address it could have missed.

**Header** (ADR-009 Amendment A, RATIFIED by Sky 2026-10-08; mirrors the service's
`validate_header` / `interpret_header`; one shared fixture table pins the
service, the harness reference and this copy). The 2026 revision of the form
prints an Event / Location / Date block at the top right; the 2025 revision
prints none — and the engine, asked for a header anyway, **makes one up**: the
form's printed title as the event, a signer's city as the location, a date no
hand wrote. A made-up header attached to real contacts is worse than none, so:

1. Take `event` / `location` / `date` from pass 2's arguments **exactly as
   returned** (`coerce_header()`: a missing or non-string cell is `""`).
2. `validate_header()` — validator V3 — judges those cells, each trimmed, and
   only ever withholds: a cell comes out exactly as read, or `""`. The whole
   header is withheld when the date line gives it away — it is ISO-formatted
   (`2023-10-15`), a placeholder (`Unknown`, `Not specified`), or ink that is
   no date at all, not even a month and a day (`TBD`) — when the location or
   the date merely repeats the event, or when the event or the location holds
   what no organiser writes there: a line break, an e-mail, five digits in a
   row or seven in all, more than 60 characters, `<` `>` `{` `}` or a
   backtick, a placeholder. A date that breaks one of those cell rules but
   still reads as a date loses only itself.
3. Only then interpret what was kept: `event` and `location` trimmed onto one
   line; `date` through `iso_date()` — `9/14/26`, `Sept. 14th, '26`,
   `Mon 9/14/26` all become `2026-09-14`, month first by rule, a two-digit
   year read as 20YY — or `""`. Never the other way round: on the interpreted
   form every date is ISO, and step 2 would withhold every dated header.

Signals, all sheet-level: `header_unread` when pass 2 failed
(three `""`; beside `address_pass_failed`, never instead of it — a sheet on
which pass 1 read no signer is sent pass 2 like any other, as the served
recipe does, and its header is delivered like any other: ADR-009 A8-11);
`header_date_unparsed` when a kept header's date line is not a full date — a
date without its year (`9/14`) is never guessed, the event and the location
are still delivered. A **withheld** header raises **no warning**: on a form
with no block it would fire on every sheet. So a blank header with no warning
means one of three things you cannot tell apart from the reading — the block
was left empty, the form has none, or the header was withheld. The header
never moves `review_recommended`, and never a row.

What this costs, stated: an organiser who writes `TBD` or `Fall 2026` on the
date line, or really writes an ISO date, loses the whole header; so does an
event or a location read on two lines, a location with a zip code in it, or an
event longer than 60 characters. These costs are derived from the rules, not
measured on real sheets — no real sheet has a labeled header yet.

## Complete client (Python, stdlib + Pillow)

```python
import base64, csv, datetime, io, json, re, sys, time, urllib.error, urllib.request

API = "https://api.makkyo.net/v1/chat/completions"
KEY = "<edge key>"          # OpenAI SDK users: base_url=https://api.makkyo.net/v1, api_key=KEY
MODEL = "Qwen/Qwen3.6-35B-A3B-FP8"
CONFIG = "pad-scale-v5-hdr"            # the recipe label — record it beside every reading (ADR-009 Amendment A, RATIFIED by Sky 2026-10-08)
PROMPT = "Extract every person from this handwritten sign-up sheet photo."
MAX_TOKENS = 1024                      # pass 1 (identity)
ADDRESS_MAX_TOKENS = 1536              # pass 2 (address)
FIELDS = ("name", "email", "phone", "street_city", "zip")
IDENTITY_FIELDS = ("name", "email", "phone")          # pass 1
ADDRESS_FIELDS = ("name", "street_city", "zip")       # pass 2
HEADER_FIELDS = ("event", "location", "date")         # the sheet's header block, also read by pass 2
SHEET_ROWS = 8                          # one HCAO sheet
ROW_CEILING = 2 * SHEET_ROWS            # > 16 rows is a degenerate reading, never a sheet
NAME_MATCH_THRESHOLD = 0.5

def record_signups_tool(fields, header_fields=()) -> dict:
    """The pass's tool. Header fields go first, as BARE strings: never describe them (it garbles the pass)."""
    return {"type": "function", "function": {
        "name": "record_signups", "description": "Record the sign-up sheet rows",
        "parameters": {"type": "object", "properties": {
            **{field: {"type": "string"} for field in header_fields},
            "rows": {"type": "array", "items": {
                "type": "object",
                "properties": {field: {"type": "string"} for field in fields},
                "required": list(fields)}}}, "required": [*header_fields, "rows"]}}}

TOOL = record_signups_tool(IDENTITY_FIELDS)                         # pass 1: the identity triple
ADDRESS_TOOL = record_signups_tool(ADDRESS_FIELDS, HEADER_FIELDS)   # pass 2: the address triple + the header

def envelope(width: int, height: int) -> tuple:
    """Landscape photo -> 1044x624; portrait photo of the landscape form -> 780x1040."""
    return (1044, 624) if width >= height else (780, 1040)

PROBED = {                             # per envelope: the fitted sizes the engine has already read a sheet at
    (1044, 624): {(1044, 624), (998, 624), (832, 624)},
    (780, 1040): {(780, 1040)}}
CANVAS_WHITE = (255, 255, 255)         # the letterbox canvas: pure white, not the top pad's paper color

def letterboxed(fitted, canvas_size: tuple):
    """The fitted sheet, unchanged, at the top-left (0, 0) of a new white canvas — never enlarged or centered."""
    from PIL import Image
    canvas = Image.new("RGB", canvas_size, CANVAS_WHITE)
    canvas.paste(fitted, (0, 0))
    return canvas

def normalize(photo_bytes: bytes):
    """EXIF-rotate -> fit the orientation's envelope -> letterbox unless the fitted size is proven."""
    from PIL import Image, ImageOps
    sheet = ImageOps.exif_transpose(Image.open(io.BytesIO(photo_bytes))).convert("RGB")
    bounds = envelope(sheet.width, sheet.height)
    sheet.thumbnail(bounds)             # shrinks only, keeps aspect
    return sheet if sheet.size in PROBED[bounds] else letterboxed(sheet, bounds)

def preprocess(photo_bytes: bytes) -> bytes:
    """normalize -> 1.5x LANCZOS -> 80px top pad. ONE picture per photo, always at a proven size."""
    from PIL import Image
    sheet = normalize(photo_bytes)
    scaled = sheet.resize((int(sheet.width * 1.5), int(sheet.height * 1.5)), Image.LANCZOS)
    canvas = Image.new("RGB", (scaled.width, scaled.height + 80), (250, 250, 248))
    canvas.paste(scaled, (0, 80))
    out = io.BytesIO(); canvas.save(out, "PNG"); return out.getvalue()

def call(body: dict, timeout: int = 60) -> dict:
    req = urllib.request.Request(API, json.dumps(body).encode(), {
        "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())

def extract(png: bytes, tool: dict, max_tokens: int) -> dict:
    """One pass: same prompt and image, the pass's tool and budget, tool_choice required.
    Returns the pass's reading — the tool call's arguments: `rows`, and on pass 2 the header cells as read."""
    uri = "data:image/png;base64," + base64.b64encode(png).decode()
    resp = call({"model": MODEL, "temperature": 0, "max_tokens": max_tokens,
                 "tools": [tool], "tool_choice": "required",
                 "messages": [{"role": "user", "content": [
                     {"type": "text", "text": PROMPT},
                     {"type": "image_url", "image_url": {"url": uri}}]}]})
    calls = resp["choices"][0]["message"].get("tool_calls") or []
    reading = json.loads(calls[0]["function"]["arguments"]) if calls else {}
    rows = reading.get("rows", [])
    return {**reading, "rows": rows if isinstance(rows, list) else []}

def engine_alive() -> bool:
    """Real health check. NEVER trust GET /health - it stays 'ready' while inference is dead."""
    try:
        r = call({"model": MODEL, "temperature": 0, "max_tokens": 200,
                  "messages": [{"role": "user", "content": "What is 2+2?"}]}, timeout=30)
        return "4" in (r["choices"][0]["message"]["content"] or "")
    except Exception:
        return False

# --- merge: the service's DD-11 rule, ported 1:1 ------------------------------------------

def as_cell(value) -> str:
    return "" if value is None else value if isinstance(value, str) else str(value)

def coerce_row(row) -> dict:
    """One row carrying exactly FIELDS as strings, whatever shape the engine returned."""
    cells = row if isinstance(row, dict) else (
        dict(zip(FIELDS, (part.strip() for part in row.split("|")))) if isinstance(row, str) else {})
    return {field: as_cell(cells.get(field)) for field in FIELDS}

def normalized_name(row: dict) -> str:
    return " ".join(str(row.get("name") or "").split()).casefold()

def edit_distance(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, 1):
        current = [i]
        for j, char_b in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (char_a != char_b)))
        previous = current
    return previous[-1]

def similarity(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    return 1.0 - edit_distance(a, b) / max(len(a), len(b), 1)

def name_pairs(pass1: list, pass2: list) -> dict:
    """Greedy best-first {pass-1 index: pass-2 index} by name similarity; blank names never pair."""
    names1 = [normalized_name(row) for row in pass1]
    names2 = [normalized_name(row) for row in pass2]
    candidates = sorted(((similarity(a, b), i, j) for i, a in enumerate(names1) if a
                         for j, b in enumerate(names2) if b), key=lambda candidate: -candidate[0])
    pairs = {}
    for score, i, j in candidates:
        if score < NAME_MATCH_THRESHOLD or i in pairs or j in pairs.values():
            continue
        pairs[i] = j
    return pairs

def positional_pairs(by_name: dict, n_pass1: int, n_pass2: int) -> dict:
    """{i: i} for the indices both passes left free — only when they read the same number of rows."""
    if n_pass1 != n_pass2:
        return {}
    taken = set(by_name.values())
    return {i: i for i in range(n_pass1) if i not in by_name and i not in taken}

def with_address_of(identity_row: dict, partner) -> dict:
    address = partner or {}
    return {**{field: identity_row.get(field, "") for field in IDENTITY_FIELDS},
            **{field: address.get(field, "") for field in ("street_city", "zip")}}

def merge(pass1_rows: list, pass2_rows: list) -> list:
    """Exactly one five-field row per pass-1 row, in pass-1 order, with its pass-2 partner's address."""
    pass1 = [coerce_row(row) for row in pass1_rows]
    pass2 = [coerce_row(row) for row in pass2_rows]
    by_name = name_pairs(pass1, pass2)
    partner_of = {**positional_pairs(by_name, len(pass1), len(pass2)), **by_name}
    return [with_address_of(row, pass2[partner_of[i]] if i in partner_of else None)
            for i, row in enumerate(pass1)]

# --- validate: the service's deterministic flags (ADR-004), same rules, same order ---------

def phone_is_well_shaped(phone: str) -> bool:
    """The delivered phone is in the house style; a blank or as-read phone is not."""
    return re.fullmatch(r"[0-9]{3}-[0-9]{3}-[0-9]{4}", phone) is not None

def validate(row: dict) -> list:
    """email_shape, phone_shape, zip_shape (malformed value) then address_missing (blank cell)."""
    flags = []
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", as_cell(row.get("email"))):
        flags.append("email_shape")
    if not phone_is_well_shaped(as_cell(row.get("phone"))):
        flags.append("phone_shape")
    if not re.fullmatch(r"97[0-9]{3}", as_cell(row.get("zip")).strip()):
        flags.append("zip_shape")
    if as_cell(row.get("street_city")).strip() == "":
        flags.append("address_missing")
    return flags

# --- present: the service's row presentation (ADR-010), same rules — after merge, before validate

NAME_SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv"}
SURNAME_PARTICLES = {"de", "del", "della", "la", "le", "van", "von", "der", "den", "da",
                     "di", "du", "dos", "das", "st", "st.", "bin", "ibn"}
DELIVERED_FIELDS = ("first_name", "middle_name", "last_name", "email", "phone", "street_city", "zip")

def split_name(raw) -> tuple:
    """(first, middle, last), case as read: trailing Jr./Sr./II/III/IV join the surname; a comma
    means `Last, First Middle`; else first word, middle words, surname extended left over particles."""
    tokens, suffixes = (raw or "").split(), []
    while len(tokens) > 1 and tokens[-1].rstrip(",").casefold() in NAME_SUFFIXES:
        suffixes.insert(0, tokens.pop().rstrip(","))
    if tokens:
        tokens[-1] = tokens[-1].rstrip(",")
    tokens = [token for token in tokens if token]
    text = " ".join(tokens)
    if "," in text:
        before, _, after = text.partition(",")
        given = after.replace(",", " ").split()
        first, middle, last = (given[0] if given else ""), " ".join(given[1:]), before.strip()
    elif len(tokens) < 2:
        first, middle, last = (tokens[0] if tokens else ""), "", ""
    else:
        start = len(tokens) - 1
        while start > 1 and tokens[start - 1].casefold() in SURNAME_PARTICLES:
            start -= 1
        first, middle, last = tokens[0], " ".join(tokens[1:start]), " ".join(tokens[start:])
    return first, middle, " ".join(part for part in (last, *suffixes) if part)

def format_phone(raw) -> str:
    """xxx-xxx-xxxx when the reading is only phone characters (ASCII digits, space, ( ) - . and a
    leading +) with 10 digits, or 11 led by 1 (dropped); anything else is the trimmed reading."""
    trimmed = (raw or "").strip()
    if not re.fullmatch(r"\+?[0-9\s().-]+", trimmed):
        return trimmed
    digits = "".join(re.findall(r"[0-9]", trimmed))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}" if len(digits) == 10 else trimmed

def present(row: dict) -> dict:
    """A merged row as delivered: the whole name in parts, the phone in the house style."""
    first, middle, last = split_name(row["name"])
    return {"first_name": first, "middle_name": middle, "last_name": last, "email": row["email"],
            "phone": format_phone(row["phone"]), "street_city": row["street_city"], "zip": row["zip"]}

# --- header: the service's validator and date rule (ADR-009), same rules, same order — validate
# --- the header AS READ, then interpret it; never the other way round

MONTHS = {name: number for number, names in enumerate((
    ("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"), ("may",),
    ("june", "jun"), ("july", "jul"), ("august", "aug"), ("september", "sep", "sept"),
    ("october", "oct"), ("november", "nov"), ("december", "dec")), 1) for name in names}
WEEKDAYS = ("monday", "mon", "tuesday", "tues", "tue", "wednesday", "weds", "wed", "thursday", "thurs",
            "thur", "thu", "friday", "fri", "saturday", "sat", "sunday", "sun")
YEAR = r"(?P<year>'?[0-9]{2}|[0-9]{4})"
NUMERIC_DATE = re.compile(r"(?P<month>[0-9]{1,2})(?P<sep>[/.-])(?P<day>[0-9]{1,2})(?P=sep)" + YEAR)
MONTH_FIRST_DATE = re.compile(r"(?P<month>[a-z]+)\.?\s*(?P<day>[0-9]{1,2})(?:st|nd|rd|th)?(?:\s*,\s*|\s+)" + YEAR)
DAY_FIRST_DATE = re.compile(r"(?P<day>[0-9]{1,2})(?:st|nd|rd|th)?\s*(?P<month>[a-z]+)\.?(?:\s*,\s*|\s+)" + YEAR)

def calendar_iso(year: int, month: int, day: int) -> str:
    try:
        return datetime.date(year, month, day).isoformat()
    except ValueError:                              # 2/30, month 13, year 0: no such day
        return ""

def iso_date(raw) -> str:
    """A written date as YYYY-MM-DD, or "" — never a guess. US month first; YY and 'YY are 20YY; one of
    / - . throughout; month names (Sept too), ordinals, one leading weekday; an ISO date passes through.
    No year, an impossible day, a range or anything else is ""."""
    text = " ".join((raw or "").split()).casefold()
    text = re.sub(r"^(?:" + "|".join(WEEKDAYS) + r")[.,\s]+", "", text)
    iso = re.fullmatch(r"([0-9]{4})-([0-9]{2})-([0-9]{2})", text)
    if iso:
        return calendar_iso(*map(int, iso.groups()))
    numeric = NUMERIC_DATE.fullmatch(text)
    written = numeric or MONTH_FIRST_DATE.fullmatch(text) or DAY_FIRST_DATE.fullmatch(text)
    if not written:
        return ""
    month = int(written["month"]) if numeric else MONTHS.get(written["month"], 0)
    year = written["year"].lstrip("'")
    return calendar_iso(2000 + int(year) if len(year) == 2 else int(year), month, int(written["day"]))

STOCK_PLACEHOLDERS = {"value>", "0.0", "n/a", "na", "none", "null", "unknown", "-", "--"}
PLACEHOLDERS = {"unknown", "not specified", "not provided", "not listed", "not given", "not available",
                "unspecified", "none", "n a", "na", "null"}
CELL_RULES = (                                      # (rule id, the trimmed cell breaks it), asked in this order
    ("a", lambda cell: "\n" in cell or "\r" in cell),                                          # a line break
    ("b", lambda cell: re.search(r"[^\s@]+@[^\s@]+\.[A-Za-z]{2,}", cell) is not None),         # an e-mail
    ("c", lambda cell: re.search(r"[0-9]{5,}", cell) is not None or len(re.findall(r"[0-9]", cell)) >= 7),
    ("d", lambda cell: len(cell) > 60),
    ("e", lambda cell: any(mark in cell for mark in ("<", ">", "`", "{", "}", "tool_call"))),   # leaked markup
    ("f", lambda cell: cell.casefold() in STOCK_PLACEHOLDERS))

def coerce_header(header) -> dict:
    """Three strings: a missing or non-string cell is "" (nothing was read there)."""
    given = header if isinstance(header, dict) else {}
    return {field: given[field] if isinstance(given.get(field), str) else "" for field in HEADER_FIELDS}

def is_placeholder(cell: str) -> bool:
    return re.sub(r"[\W_]+", " ", cell).strip().casefold() in PLACEHOLDERS

def text_rules(cell: str) -> list:
    """The rules an event or a location breaks: a-f, then `placeholder`."""
    text = cell.strip()
    return [rule for rule, breaks in CELL_RULES if breaks(text)] + (["placeholder"] if is_placeholder(text) else [])

def date_tells(cell: str) -> list:
    """What gives an INVENTED header away on its date line: tell(i) an ISO date (no hand writes one here),
    tell(ii) a placeholder, tell(5) ink that is no date — not even a month and a day (`9/14` is one)."""
    text = cell.strip()
    month_and_day = any(iso_date(text + join + "2000") for join in ("/", "-", ".", " "))
    return ((["tell(i)"] if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", text) else [])
            + (["tell(ii)"] if is_placeholder(text) else [])
            + (["tell(5)"] if text and not iso_date(text) and not month_and_day else []))

def echoes(cell: str, event: str) -> bool:
    said = " ".join(cell.split()).casefold()
    return bool(said) and said == " ".join(event.split()).casefold()

def header_withholding(cells: dict) -> tuple:
    """(the fields withheld, the rule named): a date tell or an echo of the event -> all three; else a
    broken event or location -> all three (an invented header arrives as a set); else a broken date ->
    the date alone (rule c is not asked of a date); else nothing."""
    echo = any(echoes(cells[field], cells["event"]) for field in ("location", "date"))
    tells = date_tells(cells["date"]) + (["tell(6)"] if echo else [])
    if tells:
        return HEADER_FIELDS, tells[0]
    broken = text_rules(cells["event"]) + text_rules(cells["location"])
    if broken:
        return HEADER_FIELDS, broken[0]
    broken = [rule for rule, breaks in CELL_RULES if rule != "c" and breaks(cells["date"].strip())]
    return (("date",), broken[0]) if broken else ((), None)

def validate_header(as_read) -> tuple:
    """(header, tell) for a header AS READ: each cell exactly as read or "" where withheld, and the id of
    the rule that withheld (None when nothing is). It only ever withholds — never rewrites a cell."""
    cells = coerce_header(as_read)
    withheld, tell = header_withholding(cells)
    return {field: "" if field in withheld else cell for field, cell in cells.items()}, tell

def on_one_line(cell: str) -> str:
    """An event or a location as delivered: trimmed, its lines joined by single spaces."""
    return " ".join(line.strip() for line in cell.splitlines() if line.strip())

def deliver_header(reading) -> tuple:
    """(the header delivered, its warnings) from pass 2's reading — None when that pass failed.
    Withheld whole: three "" and NO warning (on a form with no block it would fire every time)."""
    blank = dict.fromkeys(HEADER_FIELDS, "")
    if reading is None:
        return blank, ["header_unread"]
    cells = coerce_header(reading)
    withheld, _ = header_withholding(cells)
    if withheld == HEADER_FIELDS:
        return blank, []
    kept = {field: "" if field in withheld else cell for field, cell in cells.items()}
    header = {"event": on_one_line(kept["event"]), "location": on_one_line(kept["location"]),
              "date": iso_date(kept["date"])}
    date_unparsed = bool(cells["date"].strip()) and not header["date"]      # there was ink, and no date came of it
    return header, (["header_date_unparsed"] if date_unparsed else [])

# --- the sheet loop ---------------------------------------------------------------------------

def read_pass(png: bytes, tool: dict, max_tokens: int) -> dict:
    """One pass with the retry policy: 429 waits, 5xx waits for the engine, degenerate retries once."""
    degenerate_seen = False
    for attempt in range(3):
        try:
            reading = extract(png, tool, max_tokens)
        except urllib.error.HTTPError as e:
            if e.code == 429:                       # edge rate limit or busy
                time.sleep(int(e.headers.get("Retry-After", 15))); continue
            if e.code >= 500:                       # possible engine wedge
                deadline = time.time() + 90         # recovery is ~21-36s
                while time.time() < deadline and not engine_alive():
                    time.sleep(10)
                raise                               # the caller tries again with the same picture: two retries at most
            raise
        if len(reading["rows"]) > ROW_CEILING:      # degeneration guard (~0.5% of calls)
            if degenerate_seen:
                raise RuntimeError("degenerate output twice - give up")
            degenerate_seen = True; continue        # stochastic: plain retry is fine
        return reading
    raise RuntimeError("retries exhausted")

def ocr_sheet(photo_bytes: bytes) -> dict:
    png = preprocess(photo_bytes)                   # ONE picture per photo: every sending below carries it
    for sending in range(3):                        # the first try, then at most two retries (a try can send more than once: read_pass)
        try:
            identity_rows = read_pass(png, TOOL, MAX_TOKENS)["rows"]
        except urllib.error.HTTPError:
            continue                                # after a 5xx (or any HTTP error read_pass raised): the same picture again, the SAME proven size
        warnings, address = [], None                # address: pass 2's reading — its rows, and the header it carries
        try:                                        # asked whatever pass 1 read: a sheet with no rows is sent pass 2 too
            address = read_pass(png, ADDRESS_TOOL, ADDRESS_MAX_TOKENS)
        except (urllib.error.HTTPError, RuntimeError):
            pass
        if address is None or (identity_rows and not address["rows"]):   # errored, degenerate twice, or empty beside signers: a failed address pass
            address = None                          # identity rows are still delivered; the header it carried is unread
            warnings.append("address_pass_failed")
        merged = merge(identity_rows, address["rows"] if address else [])
        rows = [dict(r, flags=validate(r)) for r in map(present, merged)]
        if not rows:
            warnings.append("zero_rows_detected")
        if len(rows) > SHEET_ROWS:
            warnings.append("rows_exceed_sheet_capacity")   # two sheets in one photo? crop and resend
        header, header_warnings = deliver_header(address)   # the header never moves the rows or the review advice
        shape_flagged = any(f != "address_missing" for r in rows for f in r["flags"])
        return {"config": CONFIG, "header": header, "rows": rows, "warnings": warnings + header_warnings,
                "review_recommended": shape_flagged or "address_pass_failed" in warnings}
    raise RuntimeError("retries exhausted")

def main(argv: list) -> int:
    """python ocr.py photo.jpg > rows.csv — the delivered columns plus the flags; the header goes to stderr."""
    with open(argv[1], "rb") as photo:
        reading = ocr_sheet(photo.read())
    writer = csv.writer(sys.stdout)
    writer.writerow(DELIVERED_FIELDS + ("flags",))
    for row in reading["rows"]:
        writer.writerow([row[field] for field in DELIVERED_FIELDS] + [" ".join(row["flags"])])
    print(json.dumps({k: reading[k] for k in ("config", "header", "warnings", "review_recommended")}), file=sys.stderr)
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv))
```

A reading of a two-row sheet, as the CSV above prints it (fictional values;
the sheet's header is on the summary line the client writes to stderr —
`{"config": "pad-scale-v5-hdr", "header": {"event": "Harvest Fair",
"location": "Grange Hall", "date": "2026-09-14"}, "warnings": [],
"review_recommended": false}`):

```csv
first_name,middle_name,last_name,email,phone,street_city,zip,flags
Nadia,,Okoro,nadia.okoro@example.com,503-555-0142,"1420 SE Maple St, Portland",97214,
Theo,,Lindqvist,theo.l@example.net,503-555-0178,,97202,address_missing
```

## Guard rails you must keep (and why)

| Guard | Why |
|---|---|
| Row ceiling (> 16 rows → discard and retry that pass) | ~0.5 % of calls emit a schema-valid repetition loop (362 rows observed, SPIKE A8). It is stochastic — a plain retry works. Never return a degenerate reading. One sheet holds 8 rows; 9–16 rows is delivered with `rows_exceed_sheet_capacity` (DD-6). |
| On a retry after a 5xx, send the same picture again — never change the size sent, and give up after the second retry | The engine-crash trigger is **dimension-deterministic** (SPIKE A2): a size nobody has proven can wedge the engine for everyone, so a retry must not leave the four proven sizes. `ocr_sheet` prepares one picture per photo and re-sends those bytes, which keeps the size by construction (ADR-007 Amendment D as revised, ratified by Sky 2026-10-08). A try is not one request — a 429 and a reading of more than 16 rows re-send inside it — so the one picture can go out in up to 9 identity requests and up to 3 address requests. A retry that carried the sheet smaller on the same canvas was measured and did not read; nothing was measured after a 5xx. The client does not detect a cut-off (`length`) reading and does not send it again (see the recipe). No accuracy is claimed for a sheet read again. |
| Health = tiny inference call, never `/health` | `/health` reports `ready` while inference is fully dead (SPIKE A2). |
| Two passes, never one five-field tool | A single five-field call was measured undecodable / truncated within budget (SPIKE-01, archived label `pad-scale-v3-addr`). The identity pass is the unchanged `pad-scale-v3` request, so it cannot regress beyond run-to-run engine variance (K3 0.903 vs baseline 0.877 on dataset v5, each the median of three sessions; tolerance 0.01). |
| Deliver identity rows when the address pass fails | The address pass is additive. Blank addresses + `address_missing` + `address_pass_failed` + review is the served behaviour (DD-4a); a 502 for the whole sheet is not. |
| Shape validators on every row | The API reports no confidence scores. `email_shape` / `phone_shape` / `zip_shape` flags catch structural errors deterministically; `address_missing` only says the cell was blank (a zip-only signer is valid) and never by itself means review. |
| Merge on the whole name, present after | `merge()` pairs the passes on `name`; splitting first would leave the pairing nothing to match on and hand out addresses by position. `format_phone()` never drops a character it cannot account for — an odd phone is delivered as read and flagged. |
| Validate the header as read, before interpreting it | With no header block on the form the engine invents one (every such reading on record). `validate_header()` withheld every invented header on 40 unseen synthetic sheets and no correct cell there (exploratory, SPIKE-RESULTS A20); asked after `iso_date()` it would withhold every dated header. Unmeasured on real photographs. |
| Bare header properties in pass 2 — no description, no presence field | Any property description in pass 2 garbled the address reading; a presence field alone decided nothing (SPIKE-RESULTS A14, A18). |
| **Do not** re-run the same request as a confidence check | Measured near-useless at this operating point: identical re-runs agree 98 % of the time *including on the errors* (SPIKE A7/A8 — errors are systematic, not noise). An ensemble of 10 runs scored no better than one run. That 98 % counts fields — cell by cell, two runs agree; it is not a count of whole replies, which to one request sent twice running differ on about half of the pairs (E19), the cells no better for it. The two figures measure different things and do not contradict each other. |

## Accuracy expectations — what remains wrong

Measured on dataset v5 (20 synthetic sheets + 13 real HCAO sheets, 33 × 2 passes)
in the recipe's three scored sessions (`pad-scale-v5-hdr`, 2026-10-07;
`ocr-eval/runs/pad-scale-v5-hdr`, `-r2`, `-r3`). They are its run of record, and
every figure below is **the median of the three sessions** (ADR-009 Amendment A
§A7, RATIFIED by Sky 2026-10-08; "Measured claims" there sets the three side by
side; that a document restating the label's figures gives that median is the
orchestrator's rule, 2026-10-08). Pass 1 is the `pad-scale-v5-addr3` request
byte for byte; pass 2 carries three more properties than that recipe's. What
that costs the address reading on real photographs was measured in paired
sessions, and accepted by Sky 2026-10-08 (ADR-009 A8-1): real-sheet street &
city exact −0.040 [−0.073, −0.007] over nine sessions, −0.012 — "unsettled" —
over six more, about −0.03 pooled over those 15, that is 1 to 3 rows of 84;
the synthetic sheets level, real zip level or +0.024, no latency cost. The
real sheets are printed beside the gates and do not gate; their run-to-run
noise is about 0.07. **No accuracy figure is claimed for the header on real
sheets**: no real sheet has a labeled header yet, so the header figures below
are the synthetic sheets' and the gate's real-tier header lines print
"missing".

- **Zip exact 0.891** on the synthetic sheets (real sheets 0.976). Residual
  errors are the shape-similar glyph pairs — **5→6 and 3→8** — that also
  dominate phone digits (phone exact ≈ 0.87 on the synthetic sheets, ≈ 0.94
  on the real ones). These pass the
  `zip_shape` / `phone_shape` validators by construction (right digit count,
  wrong digit), so flag-free rows are *not* guaranteed correct.
- **Street & city exact 0.713** on the synthetic sheets (real sheets 0.857):
  the weak field. Misses are mostly a dropped or extra token in a two-line
  cell, not a wrong street. Review this column when the address is
  contact-critical.
- **Identity fields are the baseline request**: `legacy_field_f1` 0.903 over
  all 33 sheets versus 0.877 for the single-pass baseline on the same dataset
  (`pad-scale-v5`, the median of its own three sessions). Pass 1 *is* the
  baseline request, so the
  difference is run-to-run engine variance, inside K3's rule. Names 0.970 exact
  on the synthetic sheets, 0.893 on the real ones; emails occasionally lose
  `_` → `.`.
- **Name parts exact 0.970** (`split_name()` on the reading against the truth
  parts, synthetic sheets; K15 ≥ 0.95).
- **Header, as delivered** (after `validate_header()`; synthetic sheets only):
  event and location exact **0.929** (event 0.857, location 1.000) and date
  exact **0.929** on the 14 sheets with a header block (K12 ≥ 0.80, K13 ≥
  0.85); on the 6 sheets that print no block no header was delivered, in every
  session (K14 = 0). Unmeasured on real photographs.
- Practical consequence: if downstream use is contact-critical, human-review
  the phone and street columns, or verify by out-of-band means (e.g. SMS
  confirmation).

## Etiquette and limits (shared, personal infrastructure)

- **Concurrency 1.** The GPU serves Sky's coding agents concurrently
  (`max_batch_size 2`); one in-flight OCR request is both the measured
  configuration and the good-neighbor policy. Expect **~21 s per sheet for the
  two passes** (p50 20.9 s, the median of the three sessions' 20.9 / 21.6 /
  20.4 s; on the one run of `pad-scale-v5-addr3`, 2026-09-23 — the same first
  request, the second without the header properties — p50 24.1 s, pass 1 p50
  9.7 s, pass 2 p50 11.9 s, worst sheet 36.2 s) — budget client timeouts at
  ≥ 60 s per attempt.
- Edge rate limit is 120 req/min/IP (429 + `Retry-After`); irrelevant at
  concurrency 1 unless you tight-loop retries — don't.
- Keep uploads lean: after preprocessing, sheets are a few hundred KB of PNG,
  sent twice. Egress past 1 GB/month is metered; this endpoint is for token
  streams, not file serving.
- A sustained 500 on *every* request (including the 2+2 probe) means the
  engine is down for all clients. Back off minutes, not seconds; if it
  persists, contact Sky rather than hammering the retry loop.
