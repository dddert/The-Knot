import type { ReactNode } from 'react';

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <section className={`card ${className}`}>{children}</section>;
}

export function Button({ children, onClick, variant = 'primary', disabled = false, type = 'button' }: {
  children: ReactNode;
  onClick?: () => void;
  variant?: 'primary' | 'secondary' | 'danger' | 'ghost';
  disabled?: boolean;
  type?: 'button' | 'submit';
}) {
  return <button type={type} className={`btn btn-${variant}`} disabled={disabled} onClick={onClick}>{children}</button>;
}

export function Badge({ children, tone = 'neutral' }: { children: ReactNode; tone?: 'neutral' | 'green' | 'yellow' | 'red' | 'blue' | 'purple' }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

export function Metric({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return <div className="metric"><span>{label}</span><strong>{value}</strong>{hint ? <small>{hint}</small> : null}</div>;
}

export function Notice({ children, tone = 'info' }: { children: ReactNode; tone?: 'info' | 'warning' | 'success' | 'danger' }) {
  return <div className={`notice notice-${tone}`}>{children}</div>;
}

export function JsonBlock({ value }: { value: unknown }) {
  return <pre className="json-block">{JSON.stringify(value, null, 2)}</pre>;
}

export function EmptyState({ title, text }: { title: string; text: string }) {
  return <div className="empty"><strong>{title}</strong><span>{text}</span></div>;
}

export function formatNumber(value?: number | null, digits = 2) {
  if (value === undefined || value === null || Number.isNaN(value)) return '—';
  return new Intl.NumberFormat('ru-RU', { maximumFractionDigits: digits }).format(value);
}

export function toneForStatus(status?: string): 'neutral' | 'green' | 'yellow' | 'red' | 'blue' | 'purple' {
  if (status === 'expert_verified') return 'green';
  if (status === 'contradicted') return 'red';
  if (status === 'deprecated') return 'neutral';
  if (status === 'source_supported') return 'blue';
  return 'yellow';
}
