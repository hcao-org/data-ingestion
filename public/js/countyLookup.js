let zipCountyMapPromise

/**
 * Fetches data/oregon-zip-counties.json (see that file's sibling in
 * functions/ for the backend's copy of the same mapping - kept in sync by
 * hand, see ocr-review-web-app.md). Cached so repeated calls (e.g. one per
 * row) don't refetch.
 */
export function loadZipCountyMap() {
  if (!zipCountyMapPromise) {
    zipCountyMapPromise = fetch('data/oregon-zip-counties.json').then((response) => response.json())
  }
  return zipCountyMapPromise
}

/**
 * `zipFlagged` gates only the initial, OCR-driven auto-pick - once a
 * reviewer has hand-typed a zip, it's trusted regardless of the original OCR
 * flag, so callers reacting to a manual edit should pass `false` here.
 */
export function pickCounty(zip, zipFlagged, zipCountyMap) {
  if (zipFlagged) return ''
  return zipCountyMap[zip] ?? ''
}
