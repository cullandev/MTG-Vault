import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { NavLink, Navigate, Route, Routes, useLocation } from 'react-router-dom'

import UpdateBanner from './components/UpdateBanner'

import { api } from './lib/api'
import Dashboard from './pages/Dashboard'
import Library from './pages/Library'
import SetsPage from './pages/Sets'
import SetDetailPage from './pages/SetDetail'
import Decks from './pages/Decks'
import DeckDetail from './pages/DeckDetail'
import BuildForMe from './pages/BuildForMe'
import SuggestedDecks from './pages/SuggestedDecks'
import BattlesPage from './pages/Battles'
import BuyListPage from './pages/BuyList'
import ArenaPage from './pages/Arena'
import Scan from './pages/Scan'
import CardDetail from './pages/CardDetail'
import AddCards from './pages/AddCards'
import ImportCsv from './pages/ImportCsv'
import AuditLog from './pages/AuditLog'
import SystemPage from './pages/System'
import { ToastProvider } from './components/toast'

interface SessionInfo {
  authenticated: boolean
  expires_at: string | null
}

const NAV = [
  { to: '/dashboard', label: 'Home', icon: '◈' },
  { to: '/library', label: 'Library', icon: '▦' },
  { to: '/sets', label: 'Sets', icon: '▤' },
  { to: '/decks', label: 'Decks', icon: '⛊' },
  { to: '/meta', label: 'Meta', icon: '⚔' },
  { to: '/suggested-decks', label: 'Suggested', icon: '◬' },
  { to: '/battles', label: 'Battles', icon: '✦' },
  { to: '/arena', label: 'Arena', icon: '◎' },
  { to: '/scan', label: 'Scan', icon: '◉' },
  { to: '/buylist', label: 'Buy list', icon: '☆' },
  { to: '/add', label: 'Add', icon: '＋' },
  { to: '/import', label: 'Import', icon: '⇪' },
  { to: '/audit', label: 'History', icon: '↺' },
  { to: '/system', label: 'System', icon: '⚙' },
]

// The phone gets the four rooms a phone is actually for; everything else
// lives one tap away in the More sheet. Thirteen scrolling tap targets was
// a ribbon of guesswork.
const MOBILE_PRIMARY = ['/scan', '/library', '/decks', '/buylist']

// Desktop: the daily pages stay inline; the rest fold into two menus.
// Fourteen links in one strip was the phone bar's disease at a larger size.
const DESKTOP_PRIMARY = ['/dashboard', '/library', '/sets', '/decks', '/scan']
const DESKTOP_GROUPS: Array<{ label: string; items: string[] }> = [
  { label: 'Play', items: ['/meta', '/suggested-decks', '/battles', '/arena'] },
  { label: 'Tools', items: ['/buylist', '/add', '/import', '/audit', '/system'] },
]

function DesktopMenu({
  label,
  items,
  active,
}: {
  label: string
  items: (typeof NAV)[number][]
  active: boolean
}) {
  const [open, setOpen] = useState(false)
  const location = useLocation()
  const rootRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    setOpen(false)
  }, [location.pathname])

  // Outside-click and Escape close the menu WITHOUT a backdrop element: an
  // invisible full-screen backdrop inside the sticky header sat above every
  // sibling control and ate the first click on all of them.
  useEffect(() => {
    if (!open) return
    function onPointerDown(event: PointerEvent) {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false)
      }
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  return (
    <div className="relative" ref={rootRef}>
      <button
        onClick={() => setOpen((current) => !current)}
        aria-expanded={open}
        aria-haspopup="menu"
        className={`rounded-lg px-3 py-1.5 text-sm ${
          active || open ? 'bg-sky-500/15 text-sky-200' : 'text-slate-400 hover:text-slate-100'
        }`}
      >
        {label} <span className="text-[10px]">▾</span>
      </button>
      {open && (
        <>
          <div className="absolute right-0 z-30 mt-1 w-44 rounded-xl border border-vault-line bg-vault-panel p-1 shadow-xl">
            {items.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  `flex items-center gap-2 rounded-lg px-3 py-1.5 text-sm ${
                    isActive ? 'bg-sky-500/15 text-sky-200' : 'text-slate-300 hover:bg-vault-bg/60'
                  }`
                }
              >
                <span className="w-4 text-center text-xs text-slate-500">{item.icon}</span>
                {item.label}
              </NavLink>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

function Shell({ children }: { children: React.ReactNode }) {
  const location = useLocation()
  const [moreOpen, setMoreOpen] = useState(false)

  // Navigating away always closes the sheet, whichever way it happened.
  useEffect(() => {
    setMoreOpen(false)
  }, [location.pathname])

  const primary = MOBILE_PRIMARY.map((to) => NAV.find((item) => item.to === to)).filter(
    (item): item is (typeof NAV)[number] => item !== undefined,
  )
  const overflow = NAV.filter((item) => !MOBILE_PRIMARY.includes(item.to))
  const overflowActive = overflow.some((item) => location.pathname.startsWith(item.to))

  return (
    <div className="flex min-h-full flex-col">
      <UpdateBanner />
      <header className="sticky top-0 z-20 border-b border-vault-line bg-vault-bg/95 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center gap-3 px-3 py-2 sm:px-5">
          <span className="text-base font-semibold tracking-tight text-sky-300">MTG Vault</span>
          <nav className="ml-auto hidden items-center gap-1 sm:flex">
            {DESKTOP_PRIMARY.map((to) => {
              const item = NAV.find((entry) => entry.to === to)
              if (!item) return null
              return (
                <NavLink
                  key={item.to}
                  to={item.to}
                  className={({ isActive }) =>
                    `rounded-lg px-3 py-1.5 text-sm ${
                      isActive
                        ? 'bg-sky-500/15 text-sky-200'
                        : 'text-slate-400 hover:text-slate-100'
                    }`
                  }
                >
                  {item.label}
                </NavLink>
              )
            })}
            {DESKTOP_GROUPS.map((group) => (
              <DesktopMenu
                key={group.label}
                label={group.label}
                items={group.items
                  .map((to) => NAV.find((entry) => entry.to === to))
                  .filter((item): item is (typeof NAV)[number] => item !== undefined)}
                active={group.items.some((to) => location.pathname.startsWith(to))}
              />
            ))}
          </nav>
        </div>
      </header>

      {/* max-w-7xl: the binder grids and value charts earn the extra room on
          real monitors; phones never notice. The practice table is the one page
          that wants every pixel -- two boards, a hand and a log side by side --
          so it alone drops the cap. */}
      <main
        className={
          'mx-auto w-full flex-1 px-3 pb-24 pt-3 sm:px-5 sm:pb-8 ' +
          (location.pathname.startsWith('/arena') ? 'max-w-none' : 'max-w-7xl')
        }
      >
        {children}
      </main>

      {/* The More sheet: the rest of the app, one tap up from the bar. */}
      {moreOpen && (
        <div className="fixed inset-0 z-20 sm:hidden" onClick={() => setMoreOpen(false)}>
          <div className="absolute inset-0 bg-black/50" />
          <div
            className="absolute inset-x-0 bottom-14 rounded-t-2xl border-t border-vault-line bg-vault-panel p-3 pb-4"
            style={{ marginBottom: 'var(--safe-bottom)' }}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="grid grid-cols-3 gap-2">
              {overflow.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  className={({ isActive }) =>
                    `tap flex flex-col items-center gap-1 rounded-xl px-2 py-3 text-xs ${
                      isActive
                        ? 'bg-sky-500/15 text-sky-200'
                        : 'bg-vault-bg/60 text-slate-300'
                    }`
                  }
                >
                  <span className="text-lg leading-none">{item.icon}</span>
                  {item.label}
                </NavLink>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Bottom bar on phones: five fixed, thumb-reachable targets. The four
          rooms a phone is for, plus More — no horizontal scrolling ribbon. */}
      <nav
        className="fixed inset-x-0 bottom-0 z-20 flex border-t border-vault-line bg-vault-bg/95 backdrop-blur sm:hidden"
        style={{ paddingBottom: 'var(--safe-bottom)' }}
      >
        {primary.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) =>
              `tap flex flex-1 flex-col items-center justify-center gap-0.5 px-1 py-1.5 text-[11px] ${
                isActive ? 'text-sky-300' : 'text-slate-500'
              }`
            }
          >
            <span className="text-base leading-none">{item.icon}</span>
            {item.label}
          </NavLink>
        ))}
        <button
          onClick={() => setMoreOpen((open) => !open)}
          aria-expanded={moreOpen}
          className={`tap flex flex-1 flex-col items-center justify-center gap-0.5 px-1 py-1.5 text-[11px] ${
            moreOpen || overflowActive ? 'text-sky-300' : 'text-slate-500'
          }`}
        >
          <span className="text-base leading-none">⋯</span>
          More
        </button>
      </nav>
    </div>
  )
}

export default function App() {
  const session = useQuery({
    queryKey: ['session'],
    queryFn: () => api.get<SessionInfo>('/api/auth/session'),
    staleTime: 60_000,
    // The interval below already re-knocks; per-request retries on top made
    // a dead backend cost ~one request per second from an open phone PWA.
    retry: 0,
    refetchInterval: (query) => (query.state.status === 'error' ? 3_000 : false),
  })

  if (session.isLoading) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-slate-500">
        Opening the vault…
      </div>
    )
  }

  // An unreachable backend is NOT a locked vault: say what is actually
  // happening and recover on our own. There is NO other gate -- the login
  // page is gone by the owner's decree (auth is permanently off on this
  // LAN instance), so no failure mode can ever show a password form again.
  if (session.isError) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 px-6 text-center">
        <p className="text-sm text-slate-200">The vault isn&apos;t answering.</p>
        <p className="max-w-sm text-xs text-slate-500">
          Usually a restart after an update — this page retries every few seconds and will
          let itself in when the backend is back. If it persists, the server or the network
          between you and it is down.
        </p>
        <span className="mt-1 h-4 w-4 animate-spin rounded-full border-2 border-slate-600 border-t-sky-400" />
      </div>
    )
  }

  // Cannot happen on this instance (auth is permanently off by the owner's
  // decree, and the login page is deleted) -- but a fresh deploy from
  // .env.example without AUTH_DISABLED would otherwise boot a shell where
  // every call 401s with nothing explaining why.
  if (session.data && !session.data.authenticated) {
    return (
      <div className="flex h-full items-center justify-center px-6 text-center text-sm text-slate-300">
        <p className="max-w-md">
          The backend has authentication enabled, but this build has no login screen —
          set <code className="text-sky-300">AUTH_DISABLED=true</code> in .env (this
          instance&apos;s standing configuration) and restart.
        </p>
      </div>
    )
  }

  return (
    <ToastProvider>
      <Shell>
        <Routes>
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="/login" element={<Navigate to="/dashboard" replace />} />
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/library" element={<Library />} />
        <Route path="/sets" element={<SetsPage />} />
        <Route path="/sets/:setCode" element={<SetDetailPage />} />
        <Route path="/decks" element={<Decks />} />
        <Route path="/decks/:deckId" element={<DeckDetail />} />
        <Route path="/meta" element={<BuildForMe />} />
        <Route path="/suggested-decks" element={<SuggestedDecks />} />
        <Route path="/hidden-decks" element={<Navigate to="/suggested-decks" replace />} />
        <Route path="/battles" element={<BattlesPage />} />
        <Route path="/buylist" element={<BuyListPage />} />
        <Route path="/arena" element={<ArenaPage />} />
        {/* The table was called Watch while it could only be watched. */}
        <Route path="/watch" element={<Navigate to="/arena" replace />} />
        <Route path="/scan" element={<Scan />} />
        <Route path="/cards/:oracleId" element={<CardDetail />} />
        <Route path="/add" element={<AddCards />} />
        <Route path="/import" element={<ImportCsv />} />
        <Route path="/audit" element={<AuditLog />} />
        <Route path="/system" element={<SystemPage />} />
        <Route path="*" element={<Navigate to="/library" replace />} />
        </Routes>
      </Shell>
    </ToastProvider>
  )
}
