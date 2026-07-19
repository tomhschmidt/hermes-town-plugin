import { type PluginContext } from '@hermes/plugin-sdk'

const townLayout = {
  type: 'split',
  id: 'town-root',
  orientation: 'column',
  children: [
    {
      type: 'split',
      id: 'town-main-row',
      orientation: 'row',
      children: [
        {
          type: 'group',
          id: 'town-sessions',
          panes: ['sessions'],
          active: 'sessions'
        },
        {
          type: 'group',
          id: 'town-workspace',
          panes: ['workspace'],
          active: 'workspace'
        },
        {
          type: 'split',
          id: 'town-right-column',
          orientation: 'column',
          children: [
            {
              type: 'group',
              id: 'town-approvals',
              panes: ['town-approvals:pane'],
              active: 'town-approvals:pane'
            },
            {
              type: 'group',
              id: 'town-files',
              panes: ['files'],
              active: 'files'
            }
          ],
          weights: [1.6, 1]
        }
      ],
      weights: [1, 3.4, 1.25]
    },
    {
      type: 'group',
      id: 'town-terminal',
      panes: ['terminal'],
      active: 'terminal',
      minimized: true
    }
  ],
  weights: [3, 1]
}

export default {
  id: 'town-layout',
  name: 'Town Layout',
  register(ctx: PluginContext) {
    ctx.register({
      id: 'town',
      area: 'layouts',
      title: 'Town',
      order: 40,
      data: townLayout
    })
  }
}
