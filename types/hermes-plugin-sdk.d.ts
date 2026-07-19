declare module '@hermes/plugin-sdk' {
  export interface ViewportRect {
    width: number
    height: number
    narrow: boolean
  }

  export const host: {
    state: {
      activeSessionId: { get(): null | string }
      cwd: { get(): string }
      gateway: { get(): string }
      model: { get(): string }
      profile: { get(): string }
      viewport: { get(): ViewportRect }
    }
    notify: (...args: any[]) => any
    notifyError: (...args: any[]) => any
    logs: (...args: any[]) => Promise<any>
    navigate: (path: string) => void
    onEvent: (...args: any[]) => any
    restartGateway: () => Promise<any>
    status: () => Promise<any>
    request: <T = unknown>(method: string, params?: Record<string, unknown>) => Promise<T>
  }

  export const COMPOSER_AREAS: any
  export type ComposerAttachmentProvider = unknown
  export type ComposerMiddleware = unknown
  export const PALETTE_AREA: any
  export type PaletteContribution = unknown
  export type RouteContribution = unknown
  export const ROUTES_AREA: any
  export const SIDEBAR_NAV_AREA: any
  export type SidebarNavContribution = unknown
  export type StatusbarItem = unknown
  export type TitlebarTool = unknown
  export const StatusDot: any
  export type StatusTone = unknown
  export const Badge: any
  export const Button: any
  export const Checkbox: any
  export const Codicon: any
  export const ConfirmDialog: any
  export const ContextMenu: any
  export const ContextMenuContent: any
  export const ContextMenuItem: any
  export const ContextMenuSeparator: any
  export const ContextMenuTrigger: any
  export const CopyButton: any
  export const DecodeText: any
  export const Dialog: any
  export const DialogContent: any
  export const DialogDescription: any
  export const DialogFooter: any
  export const DialogHeader: any
  export const DialogTitle: any
  export const DialogTrigger: any
  export const DropdownMenu: any
  export const DropdownMenuContent: any
  export const DropdownMenuItem: any
  export const DropdownMenuSeparator: any
  export const DropdownMenuTrigger: any
  export const EmptyState: any
  export const ErrorState: any
  export const GlyphSpinner: any
  export const Input: any
  export const Kbd: any
  export const KbdGroup: any
  export const Loader: any
  export type LoaderType = unknown
  export const LogView: any
  export const Popover: any
  export const PopoverContent: any
  export const PopoverTrigger: any
  export const ScrollArea: any
  export const SearchField: any
  export const SegmentedControl: any
  export const Select: any
  export const SelectContent: any
  export const SelectItem: any
  export const SelectTrigger: any
  export const SelectValue: any
  export const Separator: any
  export const Skeleton: any
  export const Switch: any
  export const Tabs: any
  export const TabsList: any
  export const TabsTrigger: any
  export const Textarea: any
  export const Tip: any
  export const Tooltip: any
  export const TooltipContent: any
  export const TooltipProvider: any
  export const TooltipTrigger: any
  export type GatewayEventListener = unknown
  export interface HermesPlugin {
    id: string
    name: string
    register(ctx: PluginContext): unknown
  }
  export interface PluginContext {
    register(contribution: PluginContribution): unknown
    registerMany(contributions: PluginContribution[]): unknown
    rest: (...args: any[]) => any
    socket: (...args: any[]) => any
    storage: PluginStorage
  }
  export type PluginContribution = Record<string, any>
  export type PluginRestOptions = unknown
  export type PluginStorage = Record<string, any>
  export const Contribute: any
  export type ContributeProps = unknown
  export type Contribution = unknown
  export const useI18n: any
  export const haptic: (...args: any[]) => any
  export const icons: any
  export type KeybindContribution = unknown
  export const KEYBINDS_AREA: any
  export const profileColor: (...args: any[]) => any
  export const profileColorSoft: (...args: any[]) => any
  export const queryClient: any
  export const PANES_AREA: 'panes'
  export const STATUSBAR_AREAS: {
    readonly left: 'statusBar.left'
    readonly right: 'statusBar.right'
  }
  export const TITLEBAR_AREAS: {
    readonly center: 'titleBar.center'
    readonly left: 'titleBar.left'
    readonly right: 'titleBar.right'
  }
  export const evaluateRuntimeReadiness: (...args: any[]) => any
  export type RuntimeReadinessResult = unknown
  export const coarseElapsed: (...args: any[]) => any
  export const fmtDateTime: (...args: any[]) => any
  export const fmtDayTime: (...args: any[]) => any
  export const relativeTime: (...args: any[]) => any
  export const cn: (...args: any[]) => string
  export const THEMES_AREA: any
  export type RpcEvent = unknown
  export type StatusResponse = unknown
  export function useValue<T>(atom: { get(): T }): T
  export const useMutation: any
  export const useQuery: any
  export const useQueryClient: any
  export const atom: any
  export const computed: any
}
