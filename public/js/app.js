import { api } from './api.js'
import { createImagePane } from './imagePane.js'
import { createReviewGrid } from './reviewGrid.js'

const root = document.getElementById('app')
const token = new URLSearchParams(window.location.search).get('token') || 'demo-token'
const saveTimers = {}

function statusScreen(message, isError = false) {
  root.innerHTML = ''
  const div = document.createElement('div')
  div.className = isError ? 'review-page__status review-page__status--error' : 'review-page__status'
  div.textContent = message
  root.appendChild(div)
}

function scheduleSave(data, rowId, patch, showError) {
  clearTimeout(saveTimers[rowId])
  saveTimers[rowId] = setTimeout(() => {
    api.saveRowEdits(data.token, rowId, patch).catch(() => {
      showError('A recent edit failed to save. Check your connection and try again.')
    })
  }, 600)
}

function render(data) {
  if (data.status === 'finished') {
    statusScreen("Thanks! This review has been submitted. You can close this page.")
    return
  }

  root.innerHTML = ''

  const page = document.createElement('div')
  page.className = 'review-page'

  const header = document.createElement('header')
  header.className = 'review-page__header'
  header.innerHTML = `
    <h1>Review sign-up sheet</h1>
    <p>Compare the photo to the extracted data. Fields marked with ⚠ were hard for
    OCR to read and need a closer look. Check off each row once it's correct, then
    click Finish.</p>
  `

  if (data.warnings.length > 0) {
    const list = document.createElement('ul')
    list.className = 'review-page__warnings'
    data.warnings.forEach((warning) => {
      const item = document.createElement('li')
      item.textContent = warning
      list.appendChild(item)
    })
    header.appendChild(list)
  }

  const errorBanner = document.createElement('p')
  errorBanner.className = 'review-page__error'
  errorBanner.hidden = true
  header.appendChild(errorBanner)

  function showError(message) {
    errorBanner.textContent = message
    errorBanner.hidden = false
  }

  const progress = document.createElement('div')
  progress.className = 'review-page__progress'
  header.appendChild(progress)

  function updateProgress() {
    const reviewedCount = data.rows.filter((row) => row.reviewed).length
    progress.textContent = `${reviewedCount} of ${data.rows.length} rows checked`
  }
  updateProgress()

  function updateRow(rowId, patch) {
    const row = data.rows.find((r) => r.id === rowId)
    Object.assign(row, patch)
    scheduleSave(data, rowId, patch, showError)
  }

  const panes = document.createElement('div')
  panes.className = 'review-page__panes'

  const imagePane = createImagePane(data.sourceImageUrl, 'Photo of the sign-up sheet')

  const grid = createReviewGrid(data.rows, {
    onFieldChange(rowId, field, value) {
      updateRow(rowId, { [field]: value })
    },
    onToggleReviewed(rowId, reviewed) {
      updateRow(rowId, { reviewed })
      updateProgress()
      updateFinishButtonState()
    },
  })

  const gridWrap = document.createElement('div')
  gridWrap.className = 'review-page__grid-wrap'
  gridWrap.appendChild(grid.element)

  panes.append(imagePane, gridWrap)

  const footer = document.createElement('footer')
  footer.className = 'review-page__footer'

  const finishButton = document.createElement('button')
  finishButton.type = 'button'
  finishButton.className = 'review-page__finish-button'
  finishButton.textContent = 'Finish'

  function updateFinishButtonState() {
    finishButton.disabled = data.rows.some((row) => !row.reviewed)
  }
  updateFinishButtonState()

  finishButton.addEventListener('click', async () => {
    finishButton.disabled = true
    finishButton.textContent = 'Finishing…'
    try {
      await api.finishReview(data.token)
      data.status = 'finished'
      render(data)
    } catch {
      showError('Could not finish this review. Please try again.')
      finishButton.disabled = false
      finishButton.textContent = 'Finish'
    }
  })
  footer.appendChild(finishButton)

  page.append(header, panes, footer)
  root.appendChild(page)
}

async function init() {
  statusScreen('Loading review…')
  try {
    const data = await api.getReviewData(token)
    render(data)
  } catch {
    statusScreen('Could not load this review. The link may be invalid or expired.', true)
  }
}

init()
