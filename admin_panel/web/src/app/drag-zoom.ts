import * as React from "react"

type Drag = { a: number; b: number }
const DOUBLE_CLICK_MS = 400
const MIN_DRAG_PX = 5        // less than this is a click, not a drag

/** Whether there's a mouse or trackpad to drag with. On a phone a drag scrolls the page, and a tap shows the values. */
export const canDragZoom = typeof window !== "undefined" && !!window.matchMedia?.("(any-pointer: fine)").matches
export const ZOOM_HINT = canDragZoom ? "Drag across the chart to zoom in." : "Tap the chart to see the values."

/**
 * Drag across a time chart to zoom into a stretch of it. Put `ref` and `handlers` on a div wrapped tightly around
 * the chart. `start` and `end` are the times (ms) at the plot area's left and right edge, `left` and `right` the px
 * between the div's edges and the plot area (the value axis, the chart's right margin). `drag` is the stretch being
 * dragged right now (for a ReferenceArea); a drag of a few px or more ends in onZoom(from, to), in ms. Two
 * clicks in a row call onReset. (The chart redraws when the mouse goes down, which takes away the element the
 * browser would send its own click and dblclick to, so a dblclick listener never hears anything.)
 */
export function useDragZoom({
  start,
  end,
  left,
  right,
  onZoom,
  onReset,
}: {
  start: number
  end: number
  left: number
  right: number
  onZoom: (from: number, to: number) => void
  onReset?: () => void
}) {
  const ref = React.useRef<HTMLDivElement>(null)
  const [drag, setDrag] = React.useState<Drag | null>(null)
  // the chart's mouse events can come faster than renders: the ref always has the latest drag
  const dragRef = React.useRef<Drag | null>(null)
  const update = (d: Drag | null) => {
    dragRef.current = d
    setDrag(d)
  }
  const lastClick = React.useRef(0)
  const downX = React.useRef(0)

  const timeAt = (clientX: number) => {
    const box = ref.current?.getBoundingClientRect()
    if (!box || end <= start) return null
    const x0 = box.left + left, x1 = box.right - right
    const f = Math.min(1, Math.max(0, (clientX - x0) / (x1 - x0)))
    return start + f * (end - start)
  }

  const handlers = {
    onPointerDown: (e: React.PointerEvent<HTMLDivElement>) => {
      if (e.button !== 0 || e.pointerType === "touch") return   // a finger scrolls; a tap shows the tooltip
      const x = timeAt(e.clientX)
      if (x === null) return
      e.currentTarget.setPointerCapture(e.pointerId)
      downX.current = e.clientX
      update({ a: x, b: x })
    },
    onPointerMove: (e: React.PointerEvent<HTMLDivElement>) => {
      const x = timeAt(e.clientX)
      if (dragRef.current && x !== null) update({ ...dragRef.current, b: x })
    },
    onPointerUp: (e: React.PointerEvent<HTMLDivElement>) => {
      const d = dragRef.current
      if (!d) return
      if (Math.abs(e.clientX - downX.current) >= MIN_DRAG_PX && d.a !== d.b) {
        onZoom(Math.min(d.a, d.b), Math.max(d.a, d.b))
        lastClick.current = 0
      } else if (performance.now() - lastClick.current < DOUBLE_CLICK_MS) {
        onReset?.()
        lastClick.current = 0
      } else {
        lastClick.current = performance.now()
      }
      update(null)
    },
    onPointerCancel: () => update(null),
  }

  return { ref, drag: drag && drag.a !== drag.b ? drag : null, dragging: drag !== null, handlers }
}
