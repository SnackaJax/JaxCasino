import { useQuery } from "@tanstack/react-query"

import { api, type Edit, type Guild } from "@/app/api"
import { credits, dateTime, displayName, signed } from "@/app/format"
import { PlayerAvatar } from "@/app/player-avatar"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { ColGroup, ResizeHandle, useColumnWidths } from "@/app/resizable-table"
import { cn } from "@/lib/utils"

const ACTION_LABEL = { set: "Set", add: "Added", remove: "Removed" } as const

/** Every balance change made in this panel, newest first, with its note. */
export function EditsPage({ guild }: { guild: Guild }) {
  const cols = useColumnWidths("edits", [160, 200, 150, 120, 120, 120, 280])
  const edits = useQuery({
    queryKey: ["edits", guild.id],
    queryFn: () => api.get<Edit[]>(`/api/edits?guild=${guild.id}&limit=500`),
  })

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex w-full max-w-[2400px] flex-col gap-4 p-4 md:p-6 2xl:px-8">
        <Card>
          <CardHeader>
            <CardTitle>Balance edits</CardTitle>
            <CardDescription>
              Changes made in this admin panel in {guild.name}. They're never announced in Discord. (Changes made with
              the /admin commands show up under Activity as "Admin change".)
            </CardDescription>
          </CardHeader>
          <CardContent className="px-0">
            {edits.isLoading ? (
              <Skeleton className="mx-6 h-40" />
            ) : !edits.data?.length ? (
              <p className="text-muted-foreground px-6 text-sm">No edits yet.</p>
            ) : (
              <Table ref={cols.tableRef} style={cols.tableStyle} className="[&_td]:overflow-hidden [&_td]:text-ellipsis [&_th]:overflow-hidden">
      <ColGroup widths={cols.widths} />
                <TableHeader>
                  <TableRow>
                    <TableHead className="relative pl-6">When<ResizeHandle onPointerDown={cols.startResize(0)} onReset={cols.reset} /></TableHead>
                    <TableHead className="relative">Player<ResizeHandle onPointerDown={cols.startResize(1)} onReset={cols.reset} /></TableHead>
                    <TableHead className="relative">Action<ResizeHandle onPointerDown={cols.startResize(2)} onReset={cols.reset} /></TableHead>
                    <TableHead className="relative text-right">Before<ResizeHandle onPointerDown={cols.startResize(3)} onReset={cols.reset} /></TableHead>
                    <TableHead className="relative text-right">After<ResizeHandle onPointerDown={cols.startResize(4)} onReset={cols.reset} /></TableHead>
                    <TableHead className="relative text-right">Change<ResizeHandle onPointerDown={cols.startResize(5)} onReset={cols.reset} /></TableHead>
                    <TableHead className="relative pr-6">Note<ResizeHandle onPointerDown={cols.startResize(6)} onReset={cols.reset} /></TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {edits.data.map((e) => (
                    <TableRow key={e.id}>
                      <TableCell className="text-muted-foreground tabular pl-6">{dateTime(e.ts)}</TableCell>
                      <TableCell>
                        <span className="flex items-center gap-2">
                          <PlayerAvatar name={e.name} avatar={e.avatar} size={20} />
                          {displayName(e, e.user_id)}
                        </span>
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline">
                          {ACTION_LABEL[e.action]} {credits(e.amount)}
                        </Badge>
                      </TableCell>
                      <TableCell className="tabular text-right">{credits(e.before)}</TableCell>
                      <TableCell className="tabular text-right">{credits(e.after)}</TableCell>
                      <TableCell className={cn("tabular text-right", e.after > e.before ? "text-success" : "text-error")}>
                        {signed(e.after - e.before)}
                      </TableCell>
                      <TableCell className="text-muted-foreground max-w-72 truncate pr-6" title={e.note ?? undefined}>
                        {e.note ?? "—"}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
