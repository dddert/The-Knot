export type Role = 'external_partner' | 'researcher' | 'analyst' | 'manager' | 'admin';
export type GraphMode = 'compact' | 'full' | 'none';
export type AccessLevel = 'public' | 'internal' | 'confidential';

export type Health = {
  status: string;
  app: string;
  use_mock_ml: boolean;
  dependencies?: Record<string, string>;
};

export type Source = {
  id: string;
  document_id?: string;
  title?: string;
  filename?: string;
  source_type?: string;
  access_level?: AccessLevel;
  year?: number;
  country?: string;
  organization?: string;
  authors?: string[];
  page?: number;
  quote?: string;
};

export type Entity = {
  id: string;
  type: string;
  name: string;
  canonical_name?: string;
  page?: number;
  confidence?: number;
};

export type NumericValue = {
  id: string;
  parameter: string;
  display_name?: string;
  value?: number;
  value_min?: number;
  value_max?: number;
  comparator?: string;
  unit_original?: string;
  unit_normalized?: string;
  context?: string;
  source_text?: string;
  page?: number;
  confidence?: number;
};

export type Fact = {
  id: string;
  claim_text: string;
  fact_type?: string;
  geo_scope?: string;
  country?: string;
  year?: number;
  confidence?: number;
  verification_level?: string;
  status?: string;
  updated_at?: string;
  entities?: Entity[];
  numeric_values?: NumericValue[];
  source?: Source;
  source_id?: string;
  source_title?: string;
  source_page?: number;
  source_quote?: string;
};

export type GraphNode = {
  id: string;
  label: string;
  labels?: string[];
  title?: string;
  status?: string;
  confidence?: number;
  properties?: Record<string, unknown>;
};

export type GraphEdge = {
  id: string;
  source: string;
  target: string;
  label: string;
  properties?: Record<string, unknown>;
};

export type GraphData = {
  nodes: GraphNode[];
  edges: GraphEdge[];
};

export type SearchResponse = {
  query: string;
  query_plan?: Record<string, unknown>;
  facts: Fact[];
  sources: Source[];
  answer?: Record<string, unknown>;
  retrieved_evidence?: Array<Record<string, unknown>>;
  graph?: GraphData;
  debug?: Record<string, unknown>;
};

export type DocumentItem = {
  document_id: string;
  filename: string;
  source_type?: string;
  access_level?: AccessLevel;
  status?: string;
  created_at?: string;
  storage_path?: string;
};

export type DashboardData = {
  counts?: Array<{ label: string; count: number }>;
  facts_by_process?: Array<{ process: string; facts: number }>;
  facts_by_geo?: Array<{ geo_scope: string; facts: number }>;
  facts_by_status?: Array<{ status: string; facts: number }>;
  weak_topics?: Array<Record<string, unknown>>;
  contradictions_count?: number;
};
