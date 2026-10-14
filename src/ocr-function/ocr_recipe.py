"""The pad-scale-v5-hdr-vol client from ocr-over-public-api.md ("Complete client"), copied verbatim.

The doc requires the recipe be used verbatim, so the only edits are: the API key is read from the
MAKKYO_API_KEY env var instead of a hardcoded KEY, and the doc's CLI main() is left out.
When the doc changes, re-copy its code block here rather than editing this file by hand.
"""
import base64, datetime, io, json, os, re, time, urllib.error, urllib.request

API = "https://api.makkyo.net/v1/chat/completions"
MODEL = "Qwen/Qwen3.6-35B-A3B-FP8"
CONFIG = "pad-scale-v5-hdr-vol"        # the recipe label — record it beside every reading (ADR-008 Amendment A, RATIFIED by Sky 2026-10-08)
PROMPT = "Extract every person from this handwritten sign-up sheet photo."
MAX_TOKENS = 1024                      # pass 1 (identity)
ADDRESS_MAX_TOKENS = 1536              # pass 2 (address)
VOLUNTEER_MAX_TOKENS = 1024            # pass 3 (volunteer)
FIELDS = ("name", "email", "phone", "street_city", "zip")
IDENTITY_FIELDS = ("name", "email", "phone")          # pass 1
ADDRESS_FIELDS = ("name", "street_city", "zip")       # pass 2
VOLUNTEER_FIELDS = ("name", "volunteer")              # pass 3
HEADER_FIELDS = ("event", "location", "date")         # the sheet's header block, also read by pass 2
VOLUNTEER_DESCRIPTION = (               # the ONE description of the recipe, on `volunteer` — send it word for word
    "The mark in this signer's Volunteer Interest cell at the far right of the row: a check mark as ✓, "
    "a circle as O, an X as X, a written word as written; an empty string if the cell is blank.")
SHEET_ROWS = 8                          # one HCAO sheet
ROW_CEILING = 2 * SHEET_ROWS            # > 16 rows is a degenerate reading, never a sheet
NAME_MATCH_THRESHOLD = 0.5

def string_property(description=None) -> dict:
    return {"type": "string"} if description is None else {"type": "string", "description": description}

def record_signups_tool(fields, header_fields=(), descriptions=None) -> dict:
    """The pass's tool. Header fields go first, as BARE strings: never describe them (it garbles the pass).
    `descriptions` puts a description on exactly the properties it names — pass 3's `volunteer`, nothing else."""
    described = descriptions or {}
    return {"type": "function", "function": {
        "name": "record_signups", "description": "Record the sign-up sheet rows",
        "parameters": {"type": "object", "properties": {
            **{field: string_property(described.get(field)) for field in header_fields},
            "rows": {"type": "array", "items": {
                "type": "object",
                "properties": {field: string_property(described.get(field)) for field in fields},
                "required": list(fields)}}}, "required": [*header_fields, "rows"]}}}

TOOL = record_signups_tool(IDENTITY_FIELDS)                         # pass 1: the identity triple
ADDRESS_TOOL = record_signups_tool(ADDRESS_FIELDS, HEADER_FIELDS)   # pass 2: the address triple + the header
VOLUNTEER_TOOL = record_signups_tool(VOLUNTEER_FIELDS, descriptions={"volunteer": VOLUNTEER_DESCRIPTION})   # pass 3

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
        "Authorization": f"Bearer {os.environ['MAKKYO_API_KEY']}", "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())

class LostReading(RuntimeError):
    """A reply that is no reading: cut off by its budget, or not the tool call's JSON object. Never use part of it."""

def reading_of(resp) -> dict:
    """The reading one reply carries — the tool call's arguments, `rows` as returned — or LostReading: the reply
    ran out of its budget (finish_reason "length", whatever its arguments look like), or its arguments are not a
    decodable JSON object. A reply without a tool call read nothing: {"rows": []}."""
    try:
        choice = resp["choices"][0]
        calls = choice["message"].get("tool_calls") or []
        reading = json.loads(calls[0]["function"]["arguments"]) if calls else {}
    except (LookupError, TypeError, ValueError, AttributeError) as not_a_reading:
        raise LostReading("the reply is not a reading") from not_a_reading
    if choice.get("finish_reason") == "length" or not isinstance(reading, dict):
        raise LostReading("the reply was cut off by its budget, or is not a reading")
    rows = reading.get("rows", [])
    return {**reading, "rows": rows if isinstance(rows, list) else []}

def extract(png: bytes, tool: dict, max_tokens: int) -> dict:
    """One pass: same prompt and image, the pass's tool and budget, tool_choice required.
    Returns the pass's reading — the tool call's arguments: `rows` as returned, and on pass 2 the header cells as
    read — or raises LostReading (reading_of) for a reply that is none."""
    uri = "data:image/png;base64," + base64.b64encode(png).decode()
    return reading_of(call({"model": MODEL, "temperature": 0, "max_tokens": max_tokens,
                            "tools": [tool], "tool_choice": "required",
                            "messages": [{"role": "user", "content": [
                                {"type": "text", "text": PROMPT},
                                {"type": "image_url", "image_url": {"url": uri}}]}]}))

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

def row_cells(row) -> dict:
    """The cells a raw engine row offers: a dict as-is, a pipe-string split over FIELDS, nothing otherwise."""
    if isinstance(row, dict):
        return row
    return dict(zip(FIELDS, (part.strip() for part in row.split("|")))) if isinstance(row, str) else {}

def coerce_row(row) -> dict:
    """One row carrying exactly FIELDS as strings, whatever shape the engine returned."""
    cells = row_cells(row)
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

# --- volunteer: the service's join and interpretation (ADR-008), same rules — after merge, before present

VOLUNTEER_NO_WORDS = {"no", "n", "none", "nope", "not interested"}
VOLUNTEER_YES_WORDS = {"yes", "y", "yeah", "yep", "sure", "ok"}
VOLUNTEER_MARK_GLYPHS = {"✓", "✔", "√", "O", "o", "0", "()", "x", "X", "☑", "*", "•"}

def merge_marks(rows: list, volunteer_rows: list) -> list:
    """Every merged row plus `volunteer`: the cell of its pass-3 partner exactly as returned ("" without a
    partner) — paired by the whole name exactly as merge() pairs the addresses; a row nobody signed is dropped."""
    read = [row_cells(row) for row in volunteer_rows]
    by_name = name_pairs(rows, read)
    partner_of = {**positional_pairs(by_name, len(rows), len(read)), **by_name}
    return [dict(row, volunteer=read[partner_of[i]].get("volunteer", "") if i in partner_of else "")
            for i, row in enumerate(rows)]

def mark_as_read(cell) -> str:
    """volunteer_raw: the cell as returned, trimmed; a missing or non-text cell is "" — never str() it (0 is not "0")."""
    return cell.strip() if isinstance(cell, str) else ""

def interpret_volunteer(raw) -> tuple:
    """(value, unclear): "" for an empty cell; "no" for a `no` word; "yes" for a listed mark or a `yes` word;
    any other ink is "yes" too — and unclear. Words compare casefolded, punctuation a separator; marks exactly."""
    text = mark_as_read(raw)
    if not text:
        return "", False
    word = re.sub(r"[\W_]+", " ", text).strip().casefold()
    if word in VOLUNTEER_NO_WORDS:
        return "no", False
    return "yes", not (text in VOLUNTEER_MARK_GLYPHS or word in VOLUNTEER_YES_WORDS)

def answer(row: dict) -> dict:
    """A merged row with its mark decided: `volunteer` the answer, `volunteer_raw` the mark as read."""
    mark = mark_as_read(row.get("volunteer"))
    return dict(row, volunteer=interpret_volunteer(mark)[0], volunteer_raw=mark)

# --- validate: the service's deterministic flags (ADR-004), same rules, same order ---------

def phone_is_well_shaped(phone: str) -> bool:
    """The delivered phone is in the house style; a blank or as-read phone is not."""
    return re.fullmatch(r"[0-9]{3}-[0-9]{3}-[0-9]{4}", phone) is not None

def validate(row: dict) -> list:
    """email_shape, phone_shape, zip_shape (malformed value), address_missing (blank cell), then
    volunteer_unclear (the mark as read is ink the table does not name — information, never review)."""
    flags = []
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", as_cell(row.get("email"))):
        flags.append("email_shape")
    if not phone_is_well_shaped(as_cell(row.get("phone"))):
        flags.append("phone_shape")
    if not re.fullmatch(r"97[0-9]{3}", as_cell(row.get("zip")).strip()):
        flags.append("zip_shape")
    if as_cell(row.get("street_city")).strip() == "":
        flags.append("address_missing")
    if interpret_volunteer(as_cell(row.get("volunteer_raw")))[1]:
        flags.append("volunteer_unclear")
    return flags

# --- present: the service's row presentation (ADR-010), same rules — after merge, before validate

NAME_SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv"}
SURNAME_PARTICLES = {"de", "del", "della", "la", "le", "van", "von", "der", "den", "da",
                     "di", "du", "dos", "das", "st", "st.", "bin", "ibn"}
DELIVERED_FIELDS = ("first_name", "middle_name", "last_name", "email", "phone", "street_city", "zip",
                    "volunteer", "volunteer_raw")

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
    """A merged, answered row as delivered: the whole name in parts, the phone in the house style."""
    first, middle, last = split_name(row["name"])
    return {"first_name": first, "middle_name": middle, "last_name": last, "email": row["email"],
            "phone": format_phone(row["phone"]), "street_city": row["street_city"], "zip": row["zip"],
            "volunteer": row["volunteer"], "volunteer_raw": row["volunteer_raw"]}

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
            identity_rows = read_pass(png, TOOL, MAX_TOKENS)["rows"]    # a LostReading here is not caught: no first reading, no sheet
        except urllib.error.HTTPError:
            continue                                # after a 5xx (or any HTTP error read_pass raised): the same picture again, the SAME proven size
        warnings, address = [], None                # address: pass 2's reading — its rows, and the header it carries
        try:                                        # asked whatever pass 1 read: a sheet with no rows is sent pass 2 too
            address = read_pass(png, ADDRESS_TOOL, ADDRESS_MAX_TOKENS)
        except (OSError, RuntimeError):             # OSError: an HTTP error, a timeout, a dropped connection; RuntimeError: a lost reply
            pass
        if address is None or (identity_rows and not address["rows"]):   # errored, lost, degenerate twice, or empty beside signers: a failed address pass
            address = None                          # identity rows are still delivered; the header it carried is unread
            warnings.append("address_pass_failed")
        marks = None                                # marks: pass 3's reading — every signer's Volunteer Interest cell
        if identity_rows:                           # a sheet with no rows has no mark to read: no pass 3, and no warning for it
            try:
                marks = read_pass(png, VOLUNTEER_TOOL, VOLUNTEER_MAX_TOKENS)
            except (OSError, RuntimeError):         # the same two kinds: nothing a third call does may fail the sheet
                pass
            if marks is None or not marks["rows"]:  # errored, lost, degenerate twice or empty: the column was not read
                marks = None                        # every row is still delivered, its answer blank
                warnings.append("volunteer_unread")
        merged = merge(identity_rows, address["rows"] if address else [])
        answered = map(answer, merge_marks(merged, marks["rows"] if marks else []))
        rows = [dict(r, flags=validate(r)) for r in map(present, answered)]
        if not rows:
            warnings.append("zero_rows_detected")
        if len(rows) > SHEET_ROWS:
            warnings.append("rows_exceed_sheet_capacity")   # two sheets in one photo? crop and resend
        header, header_warnings = deliver_header(address)   # the header never moves the rows or the review advice
        shape_flagged = any(f in ("email_shape", "phone_shape", "zip_shape") for r in rows for f in r["flags"])
        return {"config": CONFIG, "header": header, "rows": rows, "warnings": warnings + header_warnings,
                "review_recommended": shape_flagged or "address_pass_failed" in warnings}
    raise RuntimeError("retries exhausted")
