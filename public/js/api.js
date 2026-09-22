import { mockApi } from './mockApi.js'
import { realApi } from './realApi.js'

/**
 * Contract this app needs from a backend: getReviewData(token),
 * saveRowEdits(token, rowId, patch), finishReview(token). Flip this to true
 * once functions/ is deployed (or running in the Firebase emulator) and
 * GOOGLE_SHEET_ID etc. are configured - see README.md.
 */
const USE_REAL_BACKEND = false

export const api = USE_REAL_BACKEND ? realApi : mockApi
