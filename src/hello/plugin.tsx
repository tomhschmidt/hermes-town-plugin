import {
  cn,
  haptic,
  host,
  Tip,
  useValue,
  type PluginContext
} from '@hermes/plugin-sdk'

function HelloPane() {
  const gateway = useValue(host.state.gateway)

  return (
    <div className="flex h-full flex-col gap-2 p-3 text-sm">
      <div className="font-medium">Hello Town</div>
      <div className="text-(--ui-text-tertiary)">gateway: {gateway}</div>
    </div>
  )
}

function HelloChip() {
  return (
    <Tip label="Hello Town - click me">
      <button
        className={cn(
          'inline-flex h-full items-center gap-1 px-1.5 text-xs transition-colors',
          'text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-foreground'
        )}
        type="button"
        onClick={() => {
          haptic('tap')
          host.notify({ kind: 'info', message: 'Hello from Hello Town!' })
        }}
      >
        hello
      </button>
    </Tip>
  )
}

export default {
  id: 'hello',
  name: 'Hello Town',
  register(ctx: PluginContext) {
    ctx.register({
      id: 'pane',
      area: 'panes',
      title: 'hello',
      data: { placement: 'right', width: '237px' },
      render: () => <HelloPane />
    })

    ctx.register({
      id: 'chip',
      area: 'statusBar.right',
      order: 130,
      render: () => <HelloChip />
    })
  }
}
