/* The console's frame: navigation, page header, theme, palette, toast.
 *
 * Screens render into the outlet and put their own controls into the header
 * through `HeaderActions`, which is a portal rather than a context of nodes —
 * a React node cannot be a dependency, and an effect that "sets the actions"
 * re-runs on every render of the view that owns them.
 */

import {
  createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode,
} from 'react';
import { createPortal } from 'react-dom';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { useAppState } from '../api/client';
import { Banner, Loading } from '../components/ui';
import { Tooltip } from '../components/charts/Tooltip';
import { hideTip } from '../components/charts/Tooltip';
import { CommandPalette } from './CommandPalette';
import { ROUTES } from './routes';
import { Toast } from './Toast';

/* ---------------------------------------------------------- header slot */

const HeaderSlot = createContext<HTMLElement | null>(null);

export function HeaderActions({ children }: { children: ReactNode }) {
  const node = useContext(HeaderSlot);
  if (!node) return null;
  return createPortal(children, node);
}

/* ---------------------------------------------------------------- theme */

type Theme = 'auto' | 'light' | 'dark';
const THEME_ORDER: Theme[] = ['auto', 'light', 'dark'];
const THEME_LABEL: Record<Theme, string> = {
  auto: 'Theme: system', light: 'Theme: light', dark: 'Theme: dark',
};

function useTheme() {
  const [theme, setTheme] = useState<Theme>(() => {
    try {
      const stored = localStorage.getItem('pulsar-theme') as Theme | null;
      return stored && THEME_ORDER.includes(stored) ? stored : 'auto';
    } catch { return 'auto'; }
  });

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem('pulsar-theme', theme); } catch { /* private mode */ }
  }, [theme]);

  const cycle = useCallback(() => setTheme((current) =>
    THEME_ORDER[(THEME_ORDER.indexOf(current) + 1) % THEME_ORDER.length]), []);

  return { theme, cycle };
}

/* ---------------------------------------------------------------- shell */

export function Shell() {
  const location = useLocation();
  const { theme, cycle } = useTheme();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [actionsNode, setActionsNode] = useState<HTMLElement | null>(null);
  const { data: state, error, isPending } = useAppState();

  const id = location.pathname.replace(/^\//, '') || 'overview';
  const spec = useMemo(() => ROUTES.find((r) => r.id === id) ?? ROUTES[0], [id]);

  // A chart tooltip that outlives the chart it described is a ghost; drop it
  // whenever the screen changes.
  useEffect(() => { hideTip(); }, [location.key]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(target?.tagName || '');
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setPaletteOpen(true);
      } else if (event.key === '/' && !typing) {
        event.preventDefault();
        const search = document.querySelector<HTMLInputElement>('[data-role="search"]');
        if (search) search.focus(); else setPaletteOpen(true);
      } else if (event.key === 'Escape') {
        setPaletteOpen(false);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const counts = state?.counts || {};
  const badges: Record<string, number | undefined> = {
    opportunities: counts.opportunities,
    professors: counts.professors,
    campaigns: (state?.campaigns || []).length,
  };
  const mac = typeof navigator !== 'undefined'
    && /mac/i.test(navigator.platform || navigator.userAgent);

  return (
    <div className="shell" id="shell">
      <nav className="rail" aria-label="Sections">
        <NavLink className="brand" to="/overview">
          <svg viewBox="0 0 32 32" width="22" height="22" aria-hidden="true">
            <circle cx="16" cy="16" r="4.5" fill="currentColor" />
            <circle cx="16" cy="16" r="10" fill="none" stroke="currentColor"
                    strokeWidth="1.5" opacity=".4" />
            <circle cx="16" cy="16" r="15" fill="none" stroke="currentColor"
                    strokeWidth="1" opacity=".18" />
          </svg>
          <span>PULSAR</span>
        </NavLink>
        <div className="rail-nav">
          {ROUTES.map((route) => (
            <NavLink key={route.id} to={`/${route.id}`}
                     aria-current={route.id === id ? 'page' : undefined}>
              <span>{route.label}</span>
              {route.badge && badges[route.badge]
                ? <span className="count">{badges[route.badge]}</span>
                : null}
            </NavLink>
          ))}
        </div>
        <div className="rail-foot">
          <button className="ghost" type="button" onClick={() => setPaletteOpen(true)}>
            <span>Search</span><kbd>{mac ? '⌘ K' : 'Ctrl K'}</kbd>
          </button>
          <button className="ghost" type="button" onClick={cycle}
                  aria-label="Switch colour theme">
            <span>{THEME_LABEL[theme]}</span>
          </button>
          <div className="rail-meta">
            <div>{state?.space_id || 'no space'}</div>
            <div>{state?.run_id || 'no run'}</div>
            {state?.corpus_fingerprint && (
              <div>corpus {state.corpus_fingerprint.slice(0, 12)}</div>
            )}
          </div>
        </div>
      </nav>

      <main className="main">
        <header className="topbar">
          <div className="topbar-titles">
            <h1>{spec.title}</h1>
            <p>{spec.sub}</p>
          </div>
          <div className="topbar-actions" ref={setActionsNode} />
        </header>
        <div className="content" tabIndex={-1}>
          <HeaderSlot.Provider value={actionsNode}>
            {isPending && <Loading />}
            {error && (
              <Banner tone="bad">
                <div>
                  <b>Cannot reach the PULSAR server. </b>
                  {String((error as Error).message || error)}
                </div>
              </Banner>
            )}
            {state && !state.ready && <NotBuilt />}
            {state?.ready && <Outlet />}
          </HeaderSlot.Provider>
        </div>
      </main>

      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
      <Toast />
      <Tooltip />
    </div>
  );
}

function NotBuilt() {
  return (
    <Banner tone="warn">
      <div>
        <b>No semantic space yet. </b>
        Run <code className="mono">pulsar semantics build</code> and{' '}
        <code className="mono">pulsar semantics profile</code> in the project root,
        then reload this page.
      </div>
    </Banner>
  );
}
