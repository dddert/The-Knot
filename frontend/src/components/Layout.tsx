import type { AppContext } from '../lib/context';
import { GRAPH_MODES, ROLES, saveContext } from '../lib/context';
import type { Role } from '../lib/types';

export type PageKey = 'home' | 'import' | 'search' | 'graph' | 'facts' | 'dashboard' | 'export' | 'audit';

const NAV: Array<{ key: PageKey; label: string; caption: string; roles?: Role[] }> = [
  { key: 'home', label: 'Overview', caption: 'старт и статус' },
  { key: 'import', label: 'Import', caption: 'документы', roles: ['researcher', 'analyst', 'manager', 'admin'] },
  { key: 'search', label: 'Search', caption: 'QueryPlan retrieval' },
  { key: 'graph', label: 'Graph', caption: 'fact-centric view', roles: ['researcher', 'analyst', 'manager', 'admin'] },
  { key: 'facts', label: 'Fact Editor', caption: 'версии и верификация' },
  { key: 'dashboard', label: 'Dashboard', caption: 'покрытие знаний', roles: ['researcher', 'analyst', 'manager', 'admin'] },
  { key: 'export', label: 'Export', caption: 'PDF / MD / JSON-LD', roles: ['analyst', 'manager', 'admin'] },
  { key: 'audit', label: 'Audit', caption: 'журнал действий', roles: ['manager', 'admin'] }
];

export function Layout({ ctx, setCtx, page, setPage, children }: {
  ctx: AppContext;
  setCtx: (ctx: AppContext) => void;
  page: PageKey;
  setPage: (page: PageKey) => void;
  children: React.ReactNode;
}) {
  function update(next: Partial<AppContext>) {
    const updated = { ...ctx, ...next };
    saveContext(updated);
    setCtx(updated);
  }

  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-logo">К</div>
        <div>
          <strong>Научный клубок</strong>
          <span>demo-hardened MVP</span>
        </div>
      </div>

      <div className="context-panel">
        <label>User ID</label>
        <input value={ctx.userId} onChange={(e) => update({ userId: e.target.value })} />
        <label>Role</label>
        <select value={ctx.role} onChange={(e) => update({ role: e.target.value as AppContext['role'] })}>
          {ROLES.map((role) => <option key={role} value={role}>{role}</option>)}
        </select>
        <label>Graph mode</label>
        <select value={ctx.graphMode} onChange={(e) => update({ graphMode: e.target.value as AppContext['graphMode'] })}>
          {GRAPH_MODES.map((mode) => <option key={mode} value={mode}>{mode}</option>)}
        </select>
        <p>Внутренние роли подтверждаются demo token. В production это заменяется JWT/OIDC.</p>
      </div>

      <nav className="nav">
        {NAV.map((item) => {
          const locked = item.roles && !item.roles.includes(ctx.role);
          return <button key={item.key} className={`nav-item ${page === item.key ? 'active' : ''}`} onClick={() => setPage(item.key)}>
            <span>{item.label}</span>
            <small>{locked ? 'роль ограничена' : item.caption}</small>
          </button>;
        })}
      </nav>
    </aside>
    <main className="content">
      {children}
    </main>
  </div>;
}
