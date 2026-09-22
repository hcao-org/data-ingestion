# Sign-up sheet review app

Front-end for steps 8-11 of the pipeline in `img/design.drawio.png`: a volunteer
opens a review link, compares the sign-up sheet photo against the OCR'd data
side-by-side, corrects any errors, and clicks Finish.

Plain HTML/CSS/JS, no framework, no bundler. Browsers support `import`/`export`
between `<script type="module">` files natively, so the code is still split
into small files without needing a build step for the JS. The only build step
is CSS: styles are written in SCSS (`scss/styles.scss`) and compiled to
`public/css/styles.css`, since Sass needs a compiler.

## Running it locally

Opening `public/index.html` directly (double-click / `file://`) won't work -
browsers block `import` and `fetch` from the `file://` origin, so it needs to
be served over `http://`. If you have Node installed:

```
npm install
npm run css      # compiles scss/styles.scss -> public/css/styles.css, once
npm start
```

Then open the URL it prints (something like `http://localhost:3000`) with
`?token=demo-token` appended.

If you're editing styles, run `npm run css:watch` in a separate terminal
instead of `npm run css` - it recompiles automatically on save.

If you don't have Node, any other local static server works exactly the
same, since it's all plain files:

```
# Python (ships with macOS/most systems)
cd public && python3 -m http.server 5500

# MAMP Pro, XAMPP, etc: point a host's document root at public/
```

## Project layout

```
scss/styles.scss       style source - edit this, not public/css/styles.css

public/
  index.html          entry point
  css/styles.css       compiled output, don't edit directly (see scss/ above)
  js/
    app.js             orchestrates everything: loads data, renders, footer buttons
    api.js             picks mockApi or realApi - the one thing to flip
    mockApi.js          reads data/ocr-output.json + localStorage, no backend needed
    realApi.js          calls the Cloud Function in functions/ over fetch('/api/...')
    imagePane.js        pan/zoom image viewer
    reviewGrid.js       editable table + resizable columns
    flags.js            maps OCR flags (e.g. "phone_shape") to a field name
  data/ocr-output.json  sample OCR output, used by mockApi.js
  volunteer-sheet.jpg   sample sign-up sheet photo

functions/              Cloud Function backing realApi.js - see "Google Sheets
                        backend" below
```

## How it's wired up

- **Routing**: no router library - the reviewer's token comes from a query
  string, e.g. `/?token=abc123` (`app.js` reads it with
  `new URLSearchParams(window.location.search)`). This also means there's no
  server-side rewrite needed for routing, unlike path-based URLs would need.
- **Backend is swappable.** The contract is three async functions:
  `getReviewData(token)`, `saveRowEdits(token, rowId, patch)`,
  `finishReview(token)`. `mockApi.js` implements it against
  `data/ocr-output.json` + `localStorage` (no backend needed); `realApi.js`
  implements the same contract against the Cloud Function in `functions/`
  (see "Google Sheets backend" below). `js/api.js` just picks one via a
  `USE_REAL_BACKEND` flag - nothing in `app.js`, `reviewGrid.js`, etc. needs
  to change either way, since they only ever call `api.getReviewData(...)`
  etc.
- **Data model**: each row has the OCR fields (name, email, address, city,
  zip, phone, volunteer interest) plus `flags` from OCR and a `reviewed`
  boolean set by the reviewer in this app. `data/ocr-output.json` has
  placeholder address/city/zip/volunteerInterest values for now (the
  original sample didn't include them); `api.js` falls back to an empty
  string / `false` for any row missing one, so real OCR output that's still
  missing a field won't break anything.
- **Flag → field mapping** (`js/flags.js`): assumes flags follow a
  `"{field}_{reason}"` convention, matching the one example we have
  (`phone_shape`). If the real flag vocabulary ends up different, this is the
  one place to update.
- **Editing**: the grid builds its field elements once from the initial data
  and never re-renders them - typing fires a callback but doesn't touch the
  DOM element itself, so you never lose cursor position or focus mid-edit.
  Edits autosave 600ms after the last keystroke per row.
- **Fields are `<textarea>`s, not `<input>`s**, so long content (e.g. a full
  street address) wraps and grows the box vertically instead of scrolling
  horizontally out of view. `autoGrow()` in `reviewGrid.js` resizes each one
  to fit its content on every keystroke (and once on load, via
  `requestAnimationFrame`, since `scrollHeight` isn't accurate until the
  element is actually laid out in the page).
- **"Check all"**: sits in the footer next to Finish, marks every row as
  checked without clicking each box individually. `reviewGrid.js` doesn't
  render this button itself - `createReviewGrid()` returns
  `{ element, checkAll }`, and `app.js` wires `checkAll` up to its own button
  so it can live in the footer rather than by the grid.
- **Footer is pinned to the bottom of the screen**: `.review-page` is
  `height: 100vh` (not `min-height`) with `flex-direction: column`, so the
  header/panes/footer always add up to exactly one screen - the footer never
  scrolls out of view, and the grid/image panes scroll internally instead.
- **Resizable columns**: drag a column's right edge to widen/narrow it. The
  table renders normally on first paint (so initial sizing looks the same as
  before), then `makeColumnsResizable()` in `reviewGrid.js` measures those
  natural widths on the next frame, locks them in as explicit `<col>` widths,
  and switches to `table-layout: fixed` - after that, dragging one column
  never reflows the others. Widening columns enough can make the table wider
  than its container; the surrounding `.review-page__grid-wrap` already
  scrolls, so that's expected. Mouse-only for now, no touch-drag support.

## Google Sheets backend (proof of concept)

`functions/` is a real backend: a Firebase Cloud Function (`functions/index.js`)
that reads/writes Google Sheets via `functions/sheets.js`, using the
`googleapis` package. It implements the same three-function contract as
`mockApi.js`, so flipping `USE_REAL_BACKEND` in `public/js/api.js` to `true` is
the only front-end change needed.

**Design**: three Drive folders, matching the diagram's ingest/review/reviewed
folders more closely than a single-spreadsheet shortcut would.

- **"OCR originals"** - one untouched sheet per photographed sign-up sheet.
  Never written to by this app.
- **"In Review"** - working clones, one per active review session. Created
  lazily: the first time `getReviewData(token)` is called for a given
  original, `sheets.js` copies it here and tags the copy with a private Drive
  file property (`originalSheetId`) pointing back at the original.
  `saveRowEdits` writes into this clone's cells live, so watching the actual
  file in Drive during a demo shows edits landing as a reviewer types.
- **"Reviewed"** - `finishReview` *moves* (not copies) the "In Review" clone
  here via Drive's `addParents`/`removeParents`, leaving the original in "OCR
  originals" untouched.

**The review link's `token` is the original file's Drive ID.** Rather than
building separate token-generation infrastructure, the ID Drive already
assigned to the "OCR originals" file doubles as the unguessable link secret -
`/?token=<that file's ID>`. Good enough for a POC; a production version might
want a token that isn't literally a Drive file ID, so leaking a review link
doesn't also reveal a real Drive resource ID.

**Avoiding duplicate clones.** Before creating a new "In Review" clone,
`findOrCreateWorkingCopy()` in `sheets.js` searches both the "Reviewed" and
"In Review" folders for a file already tagged with that `originalSheetId` -
if found, it's reused (and if it's already in "Reviewed", `getReviewData`
returns `status: 'finished'` immediately, which `app.js` already knows how to
render). This makes repeatedly opening the same review link safe. It is
**not** race-safe: two requests for the same token arriving within
milliseconds of each other, before either has finished tagging its clone,
could each create one. Acceptable for a single-reviewer demo; a real fix
would be a Firestore-backed lock keyed by `originalSheetId`.

**No credential files in `sheets.js` itself, on purpose.** Auth is via
Application Default Credentials (ADC) - the code never opens or parses a key
file directly, it just asks `GoogleAuth` for a client and trusts whatever
identity that resolves to. Once deployed, that's the Cloud Function's own
runtime service account, with no key file involved anywhere. Locally, that's
usually a downloaded service-account key (see setup step 5 below) - a real
file that does need to stay out of git, which is exactly what `_dev/` being
gitignored is for; `gcloud auth application-default login` is a key-file-free
alternative, but see the callout in step 7 for why it may not work on a
Workspace-managed account. Note the scopes requested are broad (`drive`, not
`drive.file`), since the app has to search/copy/move pre-existing files it
didn't create - but actual access is still gated by which folders are shared
with whichever identity is in play, regardless of how broad the requested
scope is.

### One-time Google Cloud setup (you'll need to do this - it's your account)

1. **Create or pick a GCP project.** If Firebase Hosting is already set up for
   this app, you likely already have one - check https://console.firebase.google.com
   and note the project ID for `.firebaserc`.
2. **Enable the Sheets and Drive APIs** for that project:
   https://console.cloud.google.com/apis/library/sheets.googleapis.com and
   https://console.cloud.google.com/apis/library/drive.googleapis.com
   (select your project first, then click Enable on each).
3. **Create three Drive folders**: "OCR originals", "In Review", "Reviewed".
   Copy the folder IDs for "In Review" and "Reviewed" out of their URLs
   (`https://drive.google.com/drive/folders/`**`THIS_PART`**) - "OCR
   originals" doesn't need its ID recorded anywhere, since the app only ever
   reads one file from it at a time, by that file's own ID.
4. **Create a sample sheet in "OCR originals"** and paste in the sample data
   given earlier in this conversation. Copy its file ID out of the URL
   (`https://docs.google.com/spreadsheets/d/`**`THIS_PART`**`/edit`) - that's
   the `token` you'll put in the review link.
5. **Create a service account for local dev** (IAM & Admin -> Service
   Accounts -> Create Service Account; no project IAM roles needed - access
   to Drive files is governed entirely by sharing, not GCP IAM). Under that
   service account's Keys tab, Add Key -> Create new key -> JSON, and save
   the downloaded file somewhere outside this repo's tracked files - `_dev/`
   (already gitignored) is fine, e.g. `_dev/sheets-dev-key.json`.
6. **Share the "In Review" and "Reviewed" folders** with that service
   account's email (the `client_email` field in the downloaded JSON, looks
   like `...@your-project.iam.gserviceaccount.com`) as an Editor - this is
   what actually grants access.
7. **Point the emulator at that key** when you run it:
   ```
   GOOGLE_APPLICATION_CREDENTIALS=_dev/sheets-dev-key.json npm run emulate
   ```
   `sheets.js` doesn't need to change for this - `GoogleAuth` picks up
   `GOOGLE_APPLICATION_CREDENTIALS` automatically when it's set, no code
   path difference from the deployed-function case. Don't put this variable
   in `functions/.env` - that file also gets read at deploy time, and you
   don't want a path to a local-only key file (or the key file itself)
   anywhere near a deployed function, which should keep using its own
   runtime identity instead (see "Deploying" below).

   **Why not just `gcloud auth application-default login`?** That's the
   simpler path if it works, and is still fine to try - see the callout
   below. But many Google Workspace orgs restrict "sensitive"/"restricted"
   OAuth scopes (full Drive access is one) to admin-approved apps only, and
   `gcloud`'s shared OAuth client usually isn't on that allowlist. If you hit
   "This app is blocked" in the consent screen, that's this policy, not a
   misconfiguration on your end - fixing it means asking whoever administers
   your Workspace, which is why the service account path above is the
   documented default: it uses a different, non-interactive auth flow that
   this kind of org policy doesn't gate.

   <details>
   <summary>The plain ADC approach, if your Workspace allows it</summary>

   ```
   gcloud auth application-default login --scopes=openid,https://www.googleapis.com/auth/userinfo.email,https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/drive,https://www.googleapis.com/auth/spreadsheets
   gcloud auth application-default set-quota-project YOUR_PROJECT_ID
   ```

   The `--scopes` flag matters: plain `gcloud auth application-default login`
   only grants the default scope set (`cloud-platform`, `userinfo.email`,
   `openid`), which isn't enough for the Drive/Sheets APIs `sheets.js` calls
   - and passing `scopes` to `GoogleAuth` in code has no effect on an
   already-minted *user* credential (only service-account credentials honor
   that). Skipping `--scopes` gets you a 403 `insufficientPermissions`
   instead of the blocked-app screen. Either way, share the "In Review" and
   "Reviewed" folders with your own account (step 6 above, substituting your
   email), and restart the emulator after logging in - it caches its auth
   client for the life of the process.
   </details>

### Local testing (Firebase Emulator Suite)

The emulator runs Hosting + Functions together, so the `/api/**` rewrite
works exactly like it will in production - this is different from `npm start`
(the plain `serve` static server), which has no function to rewrite to.

```
npm install
cp functions/env.template functions/.env    # then fill in the two folder IDs
npm run css
GOOGLE_APPLICATION_CREDENTIALS=_dev/sheets-dev-key.json npm run emulate
```

(Drop the `GOOGLE_APPLICATION_CREDENTIALS=...` prefix if you're using the
plain `gcloud auth application-default login` path instead.)

Open the URL it prints, with `?token=<the OCR originals file ID from step 4>`
appended. Set `USE_REAL_BACKEND = true` in `public/js/api.js` first, or
you'll just be exercising the mock client again.

### Deploying

```
npm run css                     # make sure public/css/styles.css is current
npx firebase-tools login        # once
firebase deploy
```

Make sure `functions/.env` has real values before this - the Firebase CLI
reads that file at deploy time and configures the function's environment
from it. The file itself is never bundled into the deployed function's
source (it's gitignored, and `firebase deploy` treats it specially, same as
the emulator does); it's a local-only source of config, not something served
or checked in.

Before any of this, replace `REPLACE_WITH_YOUR_PROJECT_ID` in `.firebaserc`
with your actual Firebase/GCP project ID. After the first deploy, share the
"In Review" and "Reviewed" folders with the deployed function's runtime
service account (shown in the deploy output, or under Cloud Functions -> your
function -> Details -> "Runtime service account" in the console) as an
Editor - that identity is what ADC resolves to in production, separate from
your own local `gcloud` login.

A teammate without IAM access on the GCP project can use this same
service-account-key approach for local dev - they'd just need their own key
created for them (or a shared dev-only service account, if that's acceptable
for your team) and the folders shared with its email, same as above.

## Known gaps / not built

- No pinch-to-zoom on touch for the image pane (mouse wheel + buttons only).
- No auth - relies entirely on the token in the URL being unguessable.
- No indication of *why* Finish should be disabled/blocked vs. just warning
  (currently: confirm dialog if not all rows are checked, but Finish always
  works).
- The Google Sheets backend (see that section above) has two known gaps: no
  protection against two near-simultaneous requests for the same token both
  creating an "In Review" clone, and no step 13 equivalent - nothing emails
  Robyn or anyone else when a file lands in "Reviewed".
- `realApi.js` doesn't handle concurrent edits to the same working copy -
  `saveRowEdits` re-reads the whole sheet before writing one row, so two
  reviewers editing the same clone at once could clobber each other. Fine for
  a single-reviewer demo, not for production.
