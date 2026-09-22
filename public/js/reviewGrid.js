import { fieldFlags, flagLabel } from './flags.js'

const COLUMNS = [
  { key: 'name', label: 'Name' },
  { key: 'email', label: 'Email' },
  { key: 'address', label: 'Address' },
  { key: 'city', label: 'City' },
  { key: 'zip', label: 'Zip' },
  { key: 'phone', label: 'Phone' },
  { key: 'volunteerInterest', label: 'Volunteer Interest', type: 'checkbox' },
]

const MIN_COLUMN_WIDTH = 60

function th(label) {
  const cell = document.createElement('th')
  cell.scope = 'col'
  cell.textContent = label
  cell.className = label.toLowerCase().replace(' ', '-')
  return cell
}

function autoGrow(textarea) {
  textarea.style.height = 'auto'
  textarea.style.height = `${textarea.scrollHeight}px`
}

/**
 * Lets a reviewer drag a column's right edge to resize it. Starts out on a
 * normal auto-layout table (so initial column sizing looks exactly like it did
 * before - content-based, CSS width hints like `th.zip` still apply), then
 * "locks in" those natural widths as explicit <col> widths on first paint and
 * switches to table-layout: fixed, so dragging one column never reflows others.
 */
function makeColumnsResizable(table, headerCells) {
  const colgroup = document.createElement('colgroup')
  const cols = headerCells.map(() => document.createElement('col'))
  cols.forEach((col) => colgroup.appendChild(col))
  table.insertBefore(colgroup, table.firstChild)

  const widths = new Array(headerCells.length).fill(0)

  function applyWidths() {
    let total = 0
    cols.forEach((col, index) => {
      col.style.width = `${widths[index]}px`
      total += widths[index]
    })
    table.style.width = `${total}px`
  }

  function lockInNaturalWidths() {
    headerCells.forEach((cell, index) => {
      widths[index] = Math.round(cell.getBoundingClientRect().width)
    })
    table.style.tableLayout = 'fixed'
    applyWidths()
  }

  function startResize(index, startEvent) {
    const startX = startEvent.clientX
    const startWidth = widths[index]
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'

    function onMouseMove(event) {
      widths[index] = Math.max(MIN_COLUMN_WIDTH, startWidth + (event.clientX - startX))
      applyWidths()
    }

    function stopResize() {
      document.removeEventListener('mousemove', onMouseMove)
      document.removeEventListener('mouseup', stopResize)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }

    document.addEventListener('mousemove', onMouseMove)
    document.addEventListener('mouseup', stopResize)
  }

  headerCells.forEach((cell, index) => {
    const handle = document.createElement('div')
    handle.className = 'review-grid__col-resizer'
    handle.addEventListener('mousedown', (event) => {
      event.preventDefault()
      startResize(index, event)
    })
    cell.appendChild(handle)
  })

  // getBoundingClientRect only reflects real layout once the table is actually
  // in the document, so wait a frame rather than measuring right now.
  requestAnimationFrame(lockInNaturalWidths)
}

/**
 * Returns { element }: `element` is the <table>. Fields are uncontrolled
 * <textarea>s (built once from `rows`, then left alone) - callbacks fire on
 * every keystroke/toggle, but the DOM itself is never rebuilt, so typing
 * never loses cursor position or focus.
 */
export function createReviewGrid(rows, { onFieldChange, onToggleReviewed }) {
  const table = document.createElement('table')
  table.className = 'review-grid'

  const thead = document.createElement('thead')
  const headerRow = document.createElement('tr')
  headerRow.appendChild(th('OK?'))
  COLUMNS.forEach((column) => headerRow.appendChild(th(column.label)))
  thead.appendChild(headerRow)
  table.appendChild(thead)

  const tbody = document.createElement('tbody')

  rows.forEach((row) => {
    const tr = document.createElement('tr')
    if (row.reviewed) tr.classList.add('review-grid__row--reviewed')

    const checkboxCell = document.createElement('td')
    const checkbox = document.createElement('input')
    checkbox.type = 'checkbox'
    checkbox.checked = row.reviewed
    checkbox.setAttribute('aria-label', `Mark row for ${row.name || 'this signup'} as checked`)
    checkbox.addEventListener('change', () => {
      tr.classList.toggle('review-grid__row--reviewed', checkbox.checked)
      onToggleReviewed(row.id, checkbox.checked)
    })
    checkboxCell.appendChild(checkbox)
    tr.appendChild(checkboxCell)

    COLUMNS.forEach((column) => {
      const td = document.createElement('td')
      const flags = fieldFlags(row.flags, column.key)
      const flagged = flags.length > 0
      if (flagged) td.classList.add('review-grid__cell--flagged')

      if (column.type === 'checkbox') {
        const fieldCheckbox = document.createElement('input')
        fieldCheckbox.type = 'checkbox'
        fieldCheckbox.checked = Boolean(row[column.key])
        if (flagged) fieldCheckbox.title = `Flagged: ${flags.map(flagLabel).join(', ')}`
        fieldCheckbox.addEventListener('change', () => {
          onFieldChange(row.id, column.key, fieldCheckbox.checked)
        })
        td.appendChild(fieldCheckbox)
      } else {
        const textarea = document.createElement('textarea')
        textarea.rows = 1
        textarea.value = row[column.key]
        if (flagged) textarea.title = `Flagged: ${flags.map(flagLabel).join(', ')}`
        textarea.addEventListener('input', () => {
          onFieldChange(row.id, column.key, textarea.value)
          autoGrow(textarea)
        })
        td.appendChild(textarea)
        // scrollHeight only reflects real content once the element is laid out in
        // the document, so size it on the next frame rather than right now.
        requestAnimationFrame(() => autoGrow(textarea))
      }

      if (flagged) {
        const badge = document.createElement('span')
        badge.className = 'review-grid__flag-badge'
        badge.setAttribute('aria-hidden', 'true')
        badge.textContent = '⚠'
        td.appendChild(badge)
      }

      tr.appendChild(td)
    })

    tbody.appendChild(tr)
  })

  table.appendChild(tbody)
  makeColumnsResizable(table, [...headerRow.children])

  return { element: table }
}
