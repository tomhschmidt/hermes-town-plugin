import { Badge } from '@hermes/plugin-sdk'
import type { ReactNode } from 'react'

import { upNextRowMeta, type UpNextEvent, type UpNextResult } from './lib/upnext.ts'

// Up next (S1): the next 2-3 meaningful events under the header. Rows open
// the Schedule dialog. ``renderPeople`` is the attendee slot for the next
// meeting (S2's MeetingPeople is wired in by the orchestrator); it renders
// beside, never inside, the row button.

export interface UpNextProps<E extends UpNextEvent> {
  result: UpNextResult<E>
  onOpenSchedule: () => void
  renderPeople?: (event: E) => ReactNode
}

// Aligns the attendee slot under the title column: row padding + the w-20
// time column + gap-3 (the precompiled host CSS has no pl-* that wide).
const PEOPLE_INDENT = { paddingLeft: 'calc(0.5rem + 5rem + 0.75rem)' }

export function UpNext<E extends UpNextEvent>({ result, onOpenSchedule, renderPeople }: UpNextProps<E>) {
  const peopleKey = renderPeople ? result.rows.find(row => row.meeting)?.key ?? null : null

  return (
    <section aria-label="Up next" className="min-w-0">
      <h2 className="text-sm font-medium text-(--ui-text-secondary)">Up next</h2>
      <div className="mt-3">
        {result.rows.length > 0 ? (
          <div className="flex flex-col gap-0.5">
            {result.rows.map(row => {
              // U13: one line — clock time, then "Title · in 8h · Zoom"; the
              // second line is only the dual time while away.
              const meta = upNextRowMeta(row)
              return (
                <div className="min-w-0" key={row.key}>
                  <button
                    className="flex w-full min-w-0 cursor-pointer items-start gap-3 rounded-lg px-2 py-2 text-left transition-colors duration-150 hover:bg-(--ui-row-hover-background)"
                    title="Open schedule"
                    type="button"
                    onClick={onOpenSchedule}
                  >
                    <span className="w-20 shrink-0 pt-0.5 text-xs tabular-nums text-(--ui-text-secondary)">{row.time}</span>
                    <div className="min-w-0 flex-1">
                      <div className="flex min-w-0 items-center gap-2">
                        <p className="min-w-0 truncate text-sm" title={meta ? `${row.title} · ${meta}` : row.title}>
                          <span className="text-(--ui-text-primary)">{row.title}</span>
                          {meta ? <span className="text-xs text-(--ui-text-tertiary)">{` · ${meta}`}</span> : null}
                        </p>
                        {row.conflict ? (
                          <Badge className="shrink-0" size="xs" variant="warn">Overlaps</Badge>
                        ) : null}
                      </div>
                      {row.dualTime ? (
                        <p className="truncate text-xs text-(--ui-text-tertiary)" title={row.dualTime}>{row.dualTime}</p>
                      ) : null}
                    </div>
                  </button>
                  {renderPeople && row.key === peopleKey ? (
                    <div className="min-w-0 pb-2" style={PEOPLE_INDENT}>{renderPeople(row.event)}</div>
                  ) : null}
                </div>
              )
            })}
          </div>
        ) : (
          <div className="text-sm text-(--ui-text-tertiary)">
            <p>{result.emptyMessage}</p>
            {result.tomorrowFirst ? (
              <button
                className="mt-1 cursor-pointer text-left text-xs text-(--ui-text-secondary) transition-colors duration-150 hover:text-(--ui-text-primary)"
                type="button"
                onClick={onOpenSchedule}
              >
                Tomorrow: {result.tomorrowFirst.time} · {result.tomorrowFirst.title}
              </button>
            ) : null}
          </div>
        )}
      </div>
    </section>
  )
}
