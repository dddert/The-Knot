import type { ReactNode } from 'react';

type Column<T> = { key: keyof T | string; header: string; render?: (row: T) => ReactNode };

function valueFor<T extends Record<string, unknown>>(row: T, col: Column<T>) {
  return col.render ? col.render(row) : String(row[col.key as keyof T] ?? '—');
}

export function DataTable<T extends Record<string, unknown>>({ rows, columns, compact = false, mode = 'auto' }: {
  rows: T[];
  columns: Array<Column<T>>;
  compact?: boolean;
  mode?: 'auto' | 'table' | 'cards';
}) {
  if (!rows.length) return <div className="empty-table">Нет данных</div>;
  const useCards = mode === 'cards' || (mode === 'auto' && columns.length > 5);
  if (useCards) {
    return <div className="data-card-grid">
      {rows.map((row, idx) => <article className="data-card" key={String(row.id ?? row.document_id ?? row.fact_id ?? idx)}>
        {columns.map((col) => <div className="data-card-row" key={String(col.key)}>
          <span>{col.header}</span>
          <strong>{valueFor(row, col)}</strong>
        </div>)}
      </article>)}
    </div>;
  }
  return <div className="table-wrap"><table className={compact ? 'compact' : ''}>
    <thead><tr>{columns.map((col) => <th key={String(col.key)}>{col.header}</th>)}</tr></thead>
    <tbody>
      {rows.map((row, idx) => <tr key={String(row.id ?? row.document_id ?? row.fact_id ?? idx)}>
        {columns.map((col) => <td key={String(col.key)}>{valueFor(row, col)}</td>)}
      </tr>)}
    </tbody>
  </table></div>;
}
