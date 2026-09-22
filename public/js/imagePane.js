const MIN_SCALE = 1
const MAX_SCALE = 4

function clampScale(value) {
  return Math.min(MAX_SCALE, Math.max(MIN_SCALE, value))
}

function makeButton(label, ariaLabel, onClick) {
  const button = document.createElement('button')
  button.type = 'button'
  button.textContent = label
  button.setAttribute('aria-label', ariaLabel)
  button.addEventListener('click', onClick)
  return button
}

/** Returns a DOM element: a pannable/zoomable image viewer for the sign-up sheet photo. */
export function createImagePane(src, alt) {
  let scale = MIN_SCALE
  let offsetX = 0
  let offsetY = 0
  let dragState = null

  const container = document.createElement('div')
  container.className = 'image-pane'

  const viewport = document.createElement('div')
  viewport.className = 'image-pane__viewport'

  const img = document.createElement('img')
  img.src = src
  img.alt = alt
  img.draggable = false
  viewport.appendChild(img)

  function applyTransform() {
    img.style.transform = `translate(${offsetX}px, ${offsetY}px) scale(${scale})`
    viewport.style.cursor = scale > MIN_SCALE ? 'grab' : 'default'
  }

  function zoomBy(factor) {
    scale = clampScale(scale * factor)
    if (scale === MIN_SCALE) {
      offsetX = 0
      offsetY = 0
    }
    applyTransform()
  }

  function reset() {
    scale = MIN_SCALE
    offsetX = 0
    offsetY = 0
    applyTransform()
  }

  const controls = document.createElement('div')
  controls.className = 'image-pane__controls'
  controls.append(
    makeButton('+', 'Zoom in', () => zoomBy(1.25)),
    makeButton('−', 'Zoom out', () => zoomBy(0.8)),
    makeButton('Reset', 'Reset zoom', reset),
  )

  viewport.addEventListener(
    'wheel',
    (event) => {
      event.preventDefault()
      zoomBy(event.deltaY < 0 ? 1.1 : 0.9)
    },
    { passive: false },
  )

  viewport.addEventListener('mousedown', (event) => {
    if (scale === MIN_SCALE) return
    dragState = { startX: event.clientX, startY: event.clientY, startOffsetX: offsetX, startOffsetY: offsetY }
  })

  viewport.addEventListener('mousemove', (event) => {
    if (!dragState) return
    offsetX = dragState.startOffsetX + (event.clientX - dragState.startX)
    offsetY = dragState.startOffsetY + (event.clientY - dragState.startY)
    applyTransform()
  })

  function stopDragging() {
    dragState = null
  }
  viewport.addEventListener('mouseup', stopDragging)
  viewport.addEventListener('mouseleave', stopDragging)

  applyTransform()
  container.append(controls, viewport)
  return container
}
