import { ROLE_TOKENS, type AppContext } from './context';

export class ApiError extends Error {
  status: number;
  body: string;
  constructor(status: number, body: string) {
    super(`HTTP ${status}: ${body.slice(0, 500)}`);
    this.status = status;
    this.body = body;
  }
}

function withContext(path: string, ctx: AppContext, params?: Record<string, string | number | boolean | undefined | null>) {
  const url = new URL(path, window.location.origin);
  url.searchParams.set('user_id', ctx.userId || 'demo_user');
  url.searchParams.set('role', ctx.role || 'external_partner');
  for (const [key, value] of Object.entries(params || {})) {
    if (value !== undefined && value !== null && value !== '') url.searchParams.set(key, String(value));
  }
  return `${url.pathname}${url.search}`;
}

async function parseResponse<T>(response: Response): Promise<T> {
  const contentType = response.headers.get('content-type') || '';
  const text = await response.text();
  if (!response.ok) throw new ApiError(response.status, text || response.statusText);
  if (contentType.includes('application/json')) return JSON.parse(text) as T;
  return text as T;
}

export async function apiGet<T>(ctx: AppContext, path: string, params?: Record<string, string | number | boolean | undefined | null>): Promise<T> {
  const response = await fetch(withContext(path, ctx, params), {
    headers: { 'X-Demo-Role-Token': ROLE_TOKENS[ctx.role] }
  });
  return parseResponse<T>(response);
}

export async function apiPost<T>(ctx: AppContext, path: string, body?: unknown, params?: Record<string, string | number | boolean | undefined | null>): Promise<T> {
  const response = await fetch(withContext(path, ctx, params), {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Demo-Role-Token': ROLE_TOKENS[ctx.role]
    },
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  return parseResponse<T>(response);
}

export async function apiPostForm<T>(ctx: AppContext, path: string, formData: FormData, params?: Record<string, string | number | boolean | undefined | null>): Promise<T> {
  const response = await fetch(withContext(path, ctx, params), {
    method: 'POST',
    headers: { 'X-Demo-Role-Token': ROLE_TOKENS[ctx.role] },
    body: formData
  });
  return parseResponse<T>(response);
}

export async function apiPatch<T>(ctx: AppContext, path: string, body?: unknown): Promise<T> {
  const response = await fetch(withContext(path, ctx), {
    method: 'PATCH',
    headers: {
      'Content-Type': 'application/json',
      'X-Demo-Role-Token': ROLE_TOKENS[ctx.role]
    },
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  return parseResponse<T>(response);
}

export async function apiDelete<T>(ctx: AppContext, path: string): Promise<T> {
  const response = await fetch(withContext(path, ctx), {
    method: 'DELETE',
    headers: { 'X-Demo-Role-Token': ROLE_TOKENS[ctx.role] }
  });
  return parseResponse<T>(response);
}
