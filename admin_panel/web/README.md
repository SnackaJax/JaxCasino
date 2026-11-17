# Admin panel page

The admin panel's website: React + TypeScript, built with Vite into `admin_panel/static`, which the bot serves
(`admin_panel/server.py`). Dark mode only, deep navy, monospace numbers, in the style of the OpenStatus
data-table-filters demo.

```bash
npm ci            # once, and after package.json changes
npm run build     # builds into ../static; the bot picks it up on the next page load
npm run dev       # hot reload on http://localhost:5180, API calls go to the dev server below
```

The dev server for the API: `python -m admin_panel.devserver --seed --live` from the bot's folder (port 8781,
password `dev`, its own test database).

## Where things are

- `src/app/` — the panel itself: `shell.tsx` (navigation, server picker), `pages/` (Overview, Players, Activity,
  Balance edits, Debug), `edit-balance.tsx` (the balance editor), `money-pie.tsx`, `player-sheet.tsx`, `api.ts`
  (every API call and its shape) and `format.ts` (numbers, names, game names).
- `src/components/ui/` — shadcn/ui components (`npx shadcn@latest add <name>`).
- `src/components/data-table/`, `src/lib/` (store, table-schema, filters, …) — the
  [data-table-filters](https://github.com/openstatusHQ/data-table-filters) blocks (MIT). The shadcn CLI flattens
  these into the wrong folders in a Vite project, so they were copied from the registry at their original paths
  (`https://data-table.openstatus.dev/r/<block>.json`). Two small changes: the footer row count falls back to the
  table's own rows, and `debugAll` no longer reads `process.env`.
- `src/index.css` — the theme: the colours live in one block of CSS variables (`:root, .dark`).
