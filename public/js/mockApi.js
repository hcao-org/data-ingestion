const STORAGE_KEY_PREFIX = 'review-draft:'

function loadFromStorage(token) {
  const raw = localStorage.getItem(STORAGE_KEY_PREFIX + token)
  return raw ? JSON.parse(raw) : null
}

function saveToStorage(data) {
  localStorage.setItem(STORAGE_KEY_PREFIX + data.token, JSON.stringify(data))
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

async function buildInitialData(token) {
  const response = await fetch('data/ocr-output.json')
  const raw = await response.json()

  const rows = raw.rows.map((row, index) => ({
    id: `row-${index + 1}`,
    name: row.name ?? '',
    email: row.email ?? '',
    phone: row.phone ?? '',
    address: row.address ?? '',
    city: row.city ?? '',
    zip: row.zip ?? '',
    volunteerInterest: row.volunteerInterest ?? false,
    flags: row.flags ?? [],
    reviewed: false,
  }))

  return {
    token,
    sheetId: 'demo-sheet-id',
    sourceImageUrl: 'volunteer-sheet.jpg',
    status: 'pending',
    rows,
    warnings: raw.warnings ?? [],
  }
}

/**
 * Stands in for a real backend so the app is fully usable without one - it
 * persists edits to localStorage (keyed by token) so a refresh doesn't lose
 * in-progress work. See api.js for the contract this implements.
 */
export const mockApi = {
  async getReviewData(token) {
    await delay(300)
    return loadFromStorage(token) ?? (await buildInitialData(token))
  },

  async saveRowEdits(token, rowId, patch) {
    await delay(200)
    const data = loadFromStorage(token) ?? (await buildInitialData(token))
    data.rows = data.rows.map((row) => (row.id === rowId ? { ...row, ...patch } : row))
    saveToStorage(data)
  },

  async finishReview(token) {
    await delay(300)
    const data = loadFromStorage(token) ?? (await buildInitialData(token))
    data.status = 'finished'
    saveToStorage(data)
  },
}
