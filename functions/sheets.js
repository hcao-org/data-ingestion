const { google } = require('googleapis')

const IN_REVIEW_FOLDER_ID = process.env.IN_REVIEW_FOLDER_ID
const REVIEWED_FOLDER_ID = process.env.REVIEWED_FOLDER_ID

// Column order in every sheet (original and clones). Keep in sync with the
// field names used in public/js/reviewGrid.js and api.js.
const COLUMN_KEYS = ['name', 'email', 'address', 'city', 'zip', 'phone', 'volunteerInterest', 'flags']

let authClientPromise

// No key file: resolves via Application Default Credentials - your own
// `gcloud` login locally, or the Cloud Function's runtime service account
// once deployed. `drive` scope (not just `drive.file`) is needed because we
// have to search/copy/move pre-existing files the app didn't create itself;
// actual access is still gated by which folders are shared with that
// identity, regardless of how broad the requested scope is.
function getAuthClient() {
  if (!authClientPromise) {
    const auth = new google.auth.GoogleAuth({
      scopes: ['https://www.googleapis.com/auth/drive', 'https://www.googleapis.com/auth/spreadsheets'],
    })
    authClientPromise = auth.getClient()
  }
  return authClientPromise
}

async function getClients() {
  const authClient = await getAuthClient()
  return {
    drive: google.drive({ version: 'v3', auth: authClient }),
    sheets: google.sheets({ version: 'v4', auth: authClient }),
  }
}

// Finds a file inside `folderId` tagged with our custom `originalSheetId`
// property - this is how we avoid creating duplicate "In Review" clones for
// the same original sheet across repeated getReviewData() calls.
async function findTaggedFile(drive, folderId, originalSheetId) {
  const { data } = await drive.files.list({
    q: `'${folderId}' in parents and properties has { key='originalSheetId' and value='${originalSheetId}' } and trashed = false`,
    fields: 'files(id, name)',
    spaces: 'drive',
  })
  return data.files?.[0] ?? null
}

async function findOrCreateWorkingCopy(originalSheetId) {
  const { drive } = await getClients()

  const alreadyReviewed = await findTaggedFile(drive, REVIEWED_FOLDER_ID, originalSheetId)
  if (alreadyReviewed) return { fileId: alreadyReviewed.id, status: 'finished' }

  const existing = await findTaggedFile(drive, IN_REVIEW_FOLDER_ID, originalSheetId)
  if (existing) return { fileId: existing.id, status: 'pending' }

  const { data: original } = await drive.files.get({ fileId: originalSheetId, fields: 'name' })
  const { data: copy } = await drive.files.copy({
    fileId: originalSheetId,
    requestBody: {
      name: `${original.name} (In Review)`,
      parents: [IN_REVIEW_FOLDER_ID],
    },
  })
  await drive.files.update({
    fileId: copy.id,
    requestBody: { properties: { originalSheetId } },
  })

  return { fileId: copy.id, status: 'pending' }
}

function rowFromValues(values, index) {
  const row = { id: `row-${index + 1}` }
  COLUMN_KEYS.forEach((key, columnIndex) => {
    const raw = values[columnIndex] ?? ''
    if (key === 'volunteerInterest') {
      row[key] = raw === 'TRUE' || raw === 'true'
    } else if (key === 'flags') {
      row[key] = raw
        ? raw
            .split(',')
            .map((flag) => flag.trim())
            .filter(Boolean)
        : []
    } else {
      row[key] = raw
    }
  })
  return row
}

function valuesFromRow(row) {
  return COLUMN_KEYS.map((key) => {
    if (key === 'flags') return (row.flags ?? []).join(', ')
    if (key === 'volunteerInterest') return row.volunteerInterest ? 'TRUE' : 'FALSE'
    return row[key] ?? ''
  })
}

async function readRows(fileId) {
  const { sheets } = await getClients()
  const { data } = await sheets.spreadsheets.values.get({
    spreadsheetId: fileId,
    range: 'A2:H',
  })
  return (data.values ?? []).map(rowFromValues)
}

async function getReviewData(originalSheetId) {
  const { fileId, status } = await findOrCreateWorkingCopy(originalSheetId)
  if (status === 'finished') return { workingSheetId: fileId, status, rows: [] }
  return { workingSheetId: fileId, status, rows: await readRows(fileId) }
}

async function saveRowEdits(originalSheetId, rowId, patch) {
  const { sheets } = await getClients()
  const { fileId } = await findOrCreateWorkingCopy(originalSheetId)
  const rows = await readRows(fileId)
  const existing = rows.find((row) => row.id === rowId)
  if (!existing) throw new Error(`Unknown row: ${rowId}`)
  const updated = { ...existing, ...patch }

  // rowId is "row-N" where N is 1-indexed against the data rows; +1 more to
  // skip the header row when addressing the actual sheet row.
  const sheetRow = Number(rowId.replace('row-', '')) + 1
  await sheets.spreadsheets.values.update({
    spreadsheetId: fileId,
    range: `A${sheetRow}:H${sheetRow}`,
    valueInputOption: 'RAW',
    requestBody: { values: [valuesFromRow(updated)] },
  })
}

async function finishReview(originalSheetId) {
  const { drive } = await getClients()
  const { fileId, status } = await findOrCreateWorkingCopy(originalSheetId)
  if (status === 'finished') return // already moved - nothing to do

  await drive.files.update({
    fileId,
    addParents: REVIEWED_FOLDER_ID,
    removeParents: IN_REVIEW_FOLDER_ID,
    fields: 'id, parents',
  })
}

module.exports = { getReviewData, saveRowEdits, finishReview }
