async function request(path, options) {
  const response = await fetch(`/api${path}`, options)
  if (!response.ok) throw new Error(`Request failed: ${response.status}`)
  if (response.status === 204) return undefined
  return response.json()
}

/**
 * Talks to the Cloud Function in functions/ (see functions/index.js and
 * functions/sheets.js), which reads/writes the "OCR original" and "Reviewed"
 * tabs of a Google Sheet. See api.js for the contract this implements.
 */
export const realApi = {
  getReviewData(token) {
    return request(`/review?token=${encodeURIComponent(token)}`)
  },

  saveRowEdits(token, rowId, patch) {
    return request('/review/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token, rowId, patch }),
    })
  },

  finishReview(token) {
    return request('/review/finish', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token }),
    })
  },
}
