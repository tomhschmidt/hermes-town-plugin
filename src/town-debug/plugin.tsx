import { Button, host, ScrollArea, Skeleton, type PluginContext } from '@hermes/plugin-sdk'
import { useCallback, useEffect, useState } from 'react'

type DebugValue = {
  data: unknown
  error: string | null
  loading: boolean
}

const initialValue: DebugValue = { data: null, error: null, loading: true }

function DebugSection({
  title,
  value,
  onRefresh
}: {
  title: string
  value: DebugValue
  onRefresh: () => void
}) {
  return (
    <section className="flex flex-col gap-2">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-xs font-medium uppercase tracking-wide text-(--ui-text-tertiary)">{title}</h2>
        <Button disabled={value.loading} onClick={onRefresh} size="xs" type="button" variant="text">
          Refresh
        </Button>
      </div>
      {value.loading ? <Skeleton className="h-20 w-full" /> : null}
      {value.error ? <div className="text-xs text-destructive">{value.error}</div> : null}
      {!value.loading && !value.error ? (
        <pre className="whitespace-pre-wrap break-words text-xs text-(--ui-text-secondary)">
          {JSON.stringify(value.data, null, 2)}
        </pre>
      ) : null}
    </section>
  )
}

function TownDebugPane() {
  const [catalog, setCatalog] = useState<DebugValue>(initialValue)
  const [status, setStatus] = useState<DebugValue>(initialValue)

  const refreshCatalog = useCallback(() => {
    setCatalog(current => ({ ...current, error: null, loading: true }))
    void host.request('commands.catalog', {}).then(
      data => setCatalog({ data, error: null, loading: false }),
      error => setCatalog({ data: null, error: error instanceof Error ? error.message : String(error), loading: false })
    )
  }, [])

  const refreshStatus = useCallback(() => {
    setStatus(current => ({ ...current, error: null, loading: true }))
    void host.status().then(
      data => setStatus({ data, error: null, loading: false }),
      error => setStatus({ data: null, error: error instanceof Error ? error.message : String(error), loading: false })
    )
  }, [])

  useEffect(() => {
    refreshCatalog()
    refreshStatus()
  }, [refreshCatalog, refreshStatus])

  return (
    <ScrollArea className="h-full">
      <div className="flex min-w-0 flex-col gap-5 p-3">
        <DebugSection onRefresh={refreshCatalog} title="commands.catalog" value={catalog} />
        <DebugSection onRefresh={refreshStatus} title="host.status" value={status} />
      </div>
    </ScrollArea>
  )
}

export default {
  id: 'town-debug',
  name: 'Town Debug',
  register(ctx: PluginContext) {
    ctx.register({
      id: 'pane',
      area: 'panes',
      title: 'town debug',
      data: { placement: 'right', width: '300px' },
      render: () => <TownDebugPane />
    })
  }
}
