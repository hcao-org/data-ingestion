const { onRequest } = require('firebase-functions/v2/https')
const { getReviewData, saveRowEdits, finishReview } = require('./sheets')

/**
 * Backs the /api/** rewrite in firebase.json. Firebase Hosting forwards the
 * full original path (e.g. "/api/review"), so strip the "/api" prefix before
 * routing - this way the routes below read the same regardless of whether a
 * request came through Hosting or hit this function directly.
 *
 * `token` is the Drive file ID of the original sheet in "OCR originals" - see
 * sheets.js for how that maps to a working copy in "In Review"/"Reviewed".
 */
exports.api = onRequest(async (req, res) => {
  const path = req.path.replace(/^\/api/, '') || '/'

  try {
    if (req.method === 'GET' && path === '/review') {
      const token = req.query.token
      if (!token) {
        res.status(400).json({ error: "Missing token (the original sheet's Drive file ID)" })
        return
      }
      const { workingSheetId, status, rows } = await getReviewData(token)
      res.json({
        token,
        sheetId: workingSheetId,
        sourceImageUrl: 'volunteer-sheet.jpg',
        status,
        rows,
        warnings: [],
      })
      return
    }

    if (req.method === 'POST' && path === '/review/save') {
      await saveRowEdits(req.body.token, req.body.rowId, req.body.patch)
      res.status(204).send('')
      return
    }

    if (req.method === 'POST' && path === '/review/finish') {
      await finishReview(req.body.token)
      res.status(204).send('')
      return
    }

    res.status(404).send('Not found')
  } catch (error) {
    console.error(error)
    res.status(500).json({ error: error.message })
  }
})
