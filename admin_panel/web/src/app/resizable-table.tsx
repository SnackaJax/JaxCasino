import * as React from "react"

/**
 * Column widths a plain <Table> lets you drag, remembered per table in this browser. Use the returned `tableRef`
 * and `tableStyle` on the <Table>, put <ColGroup widths={widths} /> first inside it, and a <ResizeHandle> in each
 * <TableHead> (which then needs `relative`). Double-clicking a handle puts every column back to its default.
 */
export function useColumnWidths(id: string, defaults: number[]) {
  const key = `jaxcasino-admin-columns-${id}`
  const [widths, setWidths] = React.useState<number[]>(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(key) ?? "null")
      if (Array.isArray(saved) && saved.length === defaults.length && saved.every((w) => typeof w === "number")) {
        return saved
      }
    } catch {
      // storage blocked or unreadable: the defaults
    }
    return defaults
  })
  const tableRef = React.useRef<HTMLTableElement>(null)

  const save = (w: number[] | null) => {
    try {
      if (w) localStorage.setItem(key, JSON.stringify(w))
      else localStorage.removeItem(key)
    } catch {
      // storage blocked: the widths just aren't remembered
    }
  }

  const startResize = (i: number) => (e: React.PointerEvent) => {
    e.preventDefault()
    e.stopPropagation()
    // start from what's on screen (a table wider than its columns spreads the extra space over them)
    const heads = tableRef.current?.querySelectorAll(":scope > thead > tr > th")
    const shown = heads ? Array.from(heads, (th) => (th as HTMLElement).getBoundingClientRect().width) : []
    const base = shown.length === widths.length ? shown : widths
    const x0 = e.clientX
    let latest = base
    const move = (ev: PointerEvent) => {
      latest = base.map((w, j) => (j === i ? Math.max(48, Math.round(w + ev.clientX - x0)) : Math.round(w)))
      setWidths(latest)
    }
    const up = () => {
      window.removeEventListener("pointermove", move)
      window.removeEventListener("pointerup", up)
      document.body.style.cursor = ""
      save(latest)
    }
    document.body.style.cursor = "col-resize"
    window.addEventListener("pointermove", move)
    window.addEventListener("pointerup", up)
  }

  const reset = () => {
    setWidths(defaults)
    save(null)
  }

  const total = widths.reduce((a, b) => a + b, 0)
  const tableStyle: React.CSSProperties = { tableLayout: "fixed", width: total, minWidth: "100%" }
  return { tableRef, tableStyle, widths, startResize, reset }
}

export function ColGroup({ widths }: { widths: number[] }) {
  return (
    <colgroup>
      {widths.map((w, i) => (
        <col key={i} style={{ width: w }} />
      ))}
    </colgroup>
  )
}

export function ResizeHandle({ onPointerDown, onReset }: { onPointerDown: (e: React.PointerEvent) => void; onReset: () => void }) {
  return (
    <span
      role="separator"
      aria-orientation="vertical"
      title="Drag to resize · double-click to reset all columns"
      onPointerDown={onPointerDown}
      onDoubleClick={onReset}
      className="before:bg-border hover:before:bg-primary absolute inset-y-0 right-0 z-10 flex w-2 cursor-col-resize touch-none justify-end before:h-full before:w-px"
    />
  )
}
