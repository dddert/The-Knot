import type { GraphMode, Role } from './types';

export const ROLES: Role[] = ['external_partner', 'researcher', 'analyst', 'manager', 'admin'];
export const GRAPH_MODES: GraphMode[] = ['compact', 'full', 'none'];

export const ROLE_TOKENS: Record<Role, string> = {
  external_partner: 'partner-token',
  researcher: 'researcher-token',
  analyst: 'analyst-token',
  manager: 'manager-token',
  admin: 'admin-token'
};

export type AppContext = {
  userId: string;
  role: Role;
  graphMode: GraphMode;
};

export const defaultContext: AppContext = {
  userId: 'demo_user',
  role: 'external_partner',
  graphMode: 'compact'
};

export function loadContext(): AppContext {
  const role = (localStorage.getItem('sk_role') || defaultContext.role) as Role;
  const graphMode = (localStorage.getItem('sk_graph_mode') || defaultContext.graphMode) as GraphMode;
  return {
    userId: localStorage.getItem('sk_user_id') || defaultContext.userId,
    role: ROLES.includes(role) ? role : defaultContext.role,
    graphMode: GRAPH_MODES.includes(graphMode) ? graphMode : defaultContext.graphMode
  };
}

export function saveContext(ctx: AppContext) {
  localStorage.setItem('sk_user_id', ctx.userId);
  localStorage.setItem('sk_role', ctx.role);
  localStorage.setItem('sk_graph_mode', ctx.graphMode);
}

export function can(role: Role, allowed: Role[]): boolean {
  return allowed.includes(role);
}
