import { useEffect, useMemo, useState } from 'react';
import { Layout, type PageKey } from './components/Layout';
import { Badge, Button, Card, EmptyState, JsonBlock, Metric, Notice, formatNumber, toneForStatus } from './components/Ui';
import { DataTable } from './components/DataTable';
import { GraphView } from './components/GraphView';
import { can, loadContext, saveContext, type AppContext } from './lib/context';
import { ApiError, apiDelete, apiGet, apiPatch, apiPost, apiPostForm } from './lib/api';
import type { DashboardData, DocumentItem, Fact, GraphData, Health, SearchResponse } from './lib/types';

const INTERNAL_ROLES = ['researcher', 'analyst', 'manager', 'admin'] as AppContext['role'][];
const EDIT_ROLES = ['admin', 'analyst'] as AppContext['role'][];
const EXPORT_ROLES = ['admin', 'analyst', 'manager'] as AppContext['role'][];
const AUDIT_ROLES = ['admin', 'manager'] as AppContext['role'][];

const demoQueries = [
  'Какие методы обессоливания воды подходят?',
  'Какие схемы циркуляции католита применяются в электроэкстракции никеля?',
  'Сравни отечественную и зарубежную практику по закачке шахтных вод',
  'Какие эксперименты изучали распределение Au, Ag и МПГ между штейном и шлаком?'
];

function errorText(error: unknown) {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return String(error);
}

type AnswerSection = {
  title?: unknown;
  type?: unknown;
  content?: unknown;
};

function AnswerSectionView({ section }: { section: AnswerSection }) {
  const title = String(section.title || 'Раздел');
  const type = String(section.type || 'text');
  const content = section.content;

  if (type === 'table' && Array.isArray(content)) {
    const rows = content.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object' && !Array.isArray(item));
    const keys = Array.from(new Set(rows.slice(0, 20).flatMap((row) => Object.keys(row)))).slice(0, 8);
    return <Card>
      <h3>{title}</h3>
      <DataTable
        rows={rows}
        columns={keys.map((key) => ({ key, header: key }))}
        compact
      />
    </Card>;
  }

  if (Array.isArray(content)) {
    return <Card>
      <h3>{title}</h3>
      <ul>{content.map((item, index) => <li key={index}>{typeof item === 'string' ? item : JSON.stringify(item)}</li>)}</ul>
    </Card>;
  }

  if (content && typeof content === 'object') {
    return <Card><h3>{title}</h3><JsonBlock value={content} /></Card>;
  }

  return <Card><h3>{title}</h3><p>{String(content ?? '—')}</p></Card>;
}

function useAsyncAction() {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function run<T>(fn: () => Promise<T>): Promise<T | null> {
    setLoading(true);
    setError(null);
    try {
      return await fn();
    } catch (e) {
      setError(errorText(e));
      return null;
    } finally {
      setLoading(false);
    }
  }
  return { loading, error, run, setError };
}

function PageTitle({ title, subtitle }: { title: string; subtitle?: string }) {
  return <div className="page-title"><div><h1>{title}</h1>{subtitle ? <p>{subtitle}</p> : null}</div></div>;
}

function RoleGate({ ctx, allowed, action, children, soft = false }: { ctx: AppContext; allowed: AppContext['role'][]; action: string; children: React.ReactNode; soft?: boolean }) {
  if (can(ctx.role, allowed)) return <>{children}</>;
  const notice = <Notice tone="warning">{action} доступно только ролям: <strong>{allowed.join(', ')}</strong>. Сейчас выбрана роль <strong>{ctx.role}</strong>.</Notice>;
  return soft ? notice : <>{notice}<EmptyState title="Недостаточно прав" text="Переключите роль в левом меню для продолжения демо." /></>;
}

function HomePage({ ctx, onSearchReady }: { ctx: AppContext; onSearchReady: (search: SearchResponse) => void }) {
  const [health, setHealth] = useState<Health | null>(null);
  const [result, setResult] = useState<unknown>(null);
  const { loading, error, run } = useAsyncAction();

  useEffect(() => {
    apiGet<Health>(ctx, '/health').then(setHealth).catch(() => null);
  }, [ctx]);

  async function demoSearch() {
    const search = await apiPost<SearchResponse>(ctx, '/api/search', {
      query: 'Какие методы обессоливания воды подходят?',
      filters: { geo_scope: 'all', confidence_min: 0, numeric_parameter: 'dry_residue', numeric_operator: '<=', numeric_max: 1000, numeric_unit: 'mg/L' },
      graph_mode: ctx.graphMode
    });
    onSearchReady(search);
    setResult(search);
  }

  return <>
    <PageTitle title="Научный клубок" subtitle="React + FastAPI + Neo4j + PostgreSQL demo-hardened MVP для R&D knowledge graph" />
    <div className="hero-grid">
      <Metric label="Backend" value={health?.status || '—'} hint={health?.app} />
      <Metric label="Mock ML" value={String(health?.use_mock_ml ?? '—')} hint="extraction/synthesis contract mode" />
      <Metric label="Neo4j" value={health?.dependencies?.neo4j || '—'} />
      <Metric label="Postgres" value={health?.dependencies?.postgres || '—'} />
    </div>

    <Notice tone="info">Для полного демо выберите роль <strong>admin</strong>, нажмите Init schema и Import mock. Роль <strong>external_partner</strong> видит только public-данные, а mock-источник импортируется как internal.</Notice>
    {error ? <Notice tone="danger">{error}</Notice> : null}

    <div className="action-grid">
      <Card>
        <h3>1 · Init schema</h3>
        <p>Создаёт constraints, indexes и fulltext index.</p>
        <Button disabled={loading || ctx.role !== 'admin'} onClick={() => run(async () => setResult(await apiPost(ctx, '/api/graph/init-schema')))}>Init Neo4j schema</Button>
        {ctx.role !== 'admin' ? <small>Нужна роль admin.</small> : null}
      </Card>
      <Card>
        <h3>2 · Import mock</h3>
        <p>Явно импортирует synthetic R&D corpus. Это единственный mock ingestion path.</p>
        <Button disabled={loading || !can(ctx.role, INTERNAL_ROLES)} onClick={() => run(async () => setResult(await apiPost(ctx, '/api/documents/process-mock')))}>Import mock</Button>
      </Card>
      <Card>
        <h3>3 · Demo search</h3>
        <p>Запрос с числовым фильтром dry_residue ≤ 1000 mg/L.</p>
        <Button disabled={loading} onClick={() => run(demoSearch)}>Run search</Button>
      </Card>
      <Card>
        <h3>4 · Reset demo</h3>
        <p>Удаляет только demo=true узлы.</p>
        <Button variant="danger" disabled={loading || ctx.role !== 'admin'} onClick={() => run(async () => setResult(await apiDelete(ctx, '/api/graph/clear-demo')))}>Clear demo graph</Button>
      </Card>
    </div>

    {result ? <Card><h3>Последний результат</h3><JsonBlock value={result} /></Card> : null}

    <Card>
      <h2>Сценарий защиты</h2>
      <ol className="demo-steps">
        <li><strong>Overview</strong>: показать здоровье сервисов и честный статус mock ML.</li>
        <li><strong>Import</strong>: загрузка файла и отдельно mock import; объяснить, что mock facts synthetic.</li>
        <li><strong>Search</strong>: QueryPlan, числовые фильтры, facts, source/page/quote.</li>
        <li><strong>Graph</strong>: цепочка Fact → Source / ParameterValue / Entity / Chunk.</li>
        <li><strong>Fact Editor</strong>: expert_verified + version + audit.</li>
        <li><strong>Compare / Dashboard / Export</strong>: таблицы, метрики, PDF/Markdown/JSON-LD.</li>
      </ol>
    </Card>
  </>;
}

function ImportPage({ ctx }: { ctx: AppContext }) {
  const [docs, setDocs] = useState<DocumentItem[]>([]);
  const [selectedDoc, setSelectedDoc] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [accessLevel, setAccessLevel] = useState('internal');
  const [result, setResult] = useState<unknown>(null);
  const { loading, error, run } = useAsyncAction();

  async function refresh() {
    const data = await apiGet<{ items: DocumentItem[] }>(ctx, '/api/documents');
    setDocs(data.items || []);
    if (!selectedDoc && data.items?.[0]) setSelectedDoc(data.items[0].document_id);
  }

  useEffect(() => { if (can(ctx.role, INTERNAL_ROLES)) refresh().catch(() => null); }, [ctx.role]);

  async function upload() {
    if (!file) return;
    const form = new FormData();
    form.append('file', file);
    const data = await apiPostForm(ctx, '/api/documents/upload', form, { access_level: accessLevel });
    setResult(data);
    await refresh();
  }

  return <>
    <PageTitle title="Import" subtitle="Upload + best-effort text extraction. В mock ML знания синтетические, но идут через тот же контракт." />
    <RoleGate ctx={ctx} allowed={INTERNAL_ROLES} action="Импорт документов">
      {error ? <Notice tone="danger">{error}</Notice> : null}
      <div className="two-col">
        <Card>
          <h2>Upload file</h2>
          <Notice tone="info">Backend принимает PDF/DOCX/XLSX/TXT/MD/CSV до 50 MB. При USE_MOCK_ML=true извлечённый текст не превращается в реальные факты — это зона ML-сервиса.</Notice>
          <label>Файл</label>
          <input type="file" accept=".pdf,.docx,.xlsx,.xlsm,.txt,.md,.csv" onChange={(e) => setFile(e.target.files?.[0] || null)} />
          <label>Access level</label>
          <select value={accessLevel} onChange={(e) => setAccessLevel(e.target.value)}>
            <option value="public">public</option>
            <option value="internal">internal</option>
            <option value="confidential">confidential</option>
          </select>
          {accessLevel === 'confidential' && !can(ctx.role, ['admin', 'manager']) ? <Notice tone="warning">Confidential upload доступен только admin / manager.</Notice> : null}
          <Button disabled={!file || loading || (accessLevel === 'confidential' && !can(ctx.role, ['admin', 'manager']))} onClick={() => run(upload)}>Upload</Button>
        </Card>
        <Card>
          <h2>Mock import</h2>
          <p>Безопасный synthetic path: не притворяется обработкой произвольного документа.</p>
          <Button disabled={loading} onClick={() => run(async () => setResult(await apiPost(ctx, '/api/documents/process-mock')))}>Import mock JSON</Button>
        </Card>
      </div>

      <Card>
        <div className="section-head"><h2>Документы</h2><Button variant="secondary" onClick={() => run(refresh)}>Refresh</Button></div>
        <DataTable rows={docs as unknown as Record<string, unknown>[]} columns={[
          { key: 'document_id', header: 'ID' },
          { key: 'filename', header: 'Файл' },
          { key: 'access_level', header: 'Доступ', render: (r) => <Badge tone={r.access_level === 'confidential' ? 'red' : r.access_level === 'public' ? 'green' : 'blue'}>{String(r.access_level || '—')}</Badge> },
          { key: 'status', header: 'Статус' },
          { key: 'created_at', header: 'Создан' }
        ]} />
        {docs.length ? <div className="inline-form">
          <select value={selectedDoc} onChange={(e) => setSelectedDoc(e.target.value)}>{docs.map((d) => <option key={d.document_id} value={d.document_id}>{d.document_id}</option>)}</select>
          <Button disabled={!selectedDoc || loading} onClick={() => run(async () => setResult(await apiPost(ctx, `/api/documents/${selectedDoc}/process`)))}>Process selected</Button>
        </div> : null}
      </Card>
      {result ? <Card><h3>Результат</h3><JsonBlock value={result} /></Card> : null}
    </RoleGate>
  </>;
}

function SearchPage({ ctx, lastSearch, setLastSearch, setPage }: { ctx: AppContext; lastSearch: SearchResponse | null; setLastSearch: (s: SearchResponse) => void; setPage: (p: PageKey) => void }) {
  const [query, setQuery] = useState(demoQueries[0]);
  const [confidence, setConfidence] = useState(0);
  const [geoScope, setGeoScope] = useState('all');
  const [processFilter, setProcessFilter] = useState('');
  const [materialFilter, setMaterialFilter] = useState('');
  const [countryFilter, setCountryFilter] = useState('');
  const [yearFrom, setYearFrom] = useState<number | ''>('');
  const [yearTo, setYearTo] = useState<number | ''>('');
  const [statusFilter, setStatusFilter] = useState('');
  const [factTypeFilter, setFactTypeFilter] = useState('');
  const [numericEnabled, setNumericEnabled] = useState(false);
  const [numericParameter, setNumericParameter] = useState('dry_residue');
  const [numericOperator, setNumericOperator] = useState('<=');
  const [numericMin, setNumericMin] = useState<number | ''>('');
  const [numericMax, setNumericMax] = useState<number | ''>(1000);
  const [numericUnit, setNumericUnit] = useState('mg/L');
  const [showDebug, setShowDebug] = useState(false);
  const { loading, error, run } = useAsyncAction();

  async function search() {
    const filters: Record<string, unknown> = { geo_scope: geoScope, confidence_min: confidence };
    if (processFilter.trim()) filters.process = processFilter.trim();
    if (materialFilter.trim()) filters.material = materialFilter.trim();
    if (countryFilter.trim()) filters.country = countryFilter.trim();
    if (yearFrom !== '') filters.year_from = Number(yearFrom);
    if (yearTo !== '') filters.year_to = Number(yearTo);
    if (statusFilter) filters.status = statusFilter;
    if (factTypeFilter.trim()) filters.fact_type = factTypeFilter.trim();
    if (numericEnabled && numericParameter) {
      filters.numeric_parameter = numericParameter;
      filters.numeric_operator = numericOperator;
      if (numericMin !== '') filters.numeric_min = Number(numericMin);
      if (numericMax !== '') filters.numeric_max = Number(numericMax);
      if (numericUnit.trim()) filters.numeric_unit = numericUnit.trim();
    }
    const data = await apiPost<SearchResponse>(ctx, '/api/search', { query, filters, graph_mode: ctx.graphMode });
    setLastSearch(data);
  }

  const facts = lastSearch?.facts || [];
  const answerSections = Array.isArray(lastSearch?.answer?.sections)
    ? lastSearch.answer.sections as AnswerSection[]
    : [];
  const relatedExperts = Array.isArray(lastSearch?.answer?.related_experts)
    ? lastSearch.answer.related_experts.filter(
        (item): item is Record<string, unknown> =>
          Boolean(item) && typeof item === 'object' && !Array.isArray(item)
      )
    : [];

  return <>
    <PageTitle title="Search" subtitle="Hybrid full-corpus retrieval + QueryPlan + graph facts + numeric filters." />
    {ctx.role === 'external_partner' ? <Notice tone="warning">Вы в роли external_partner: видны только public-данные. Demo mock импортируется как internal, поэтому выдача может быть пустой.</Notice> : null}
    {error ? <Notice tone="danger">{error}</Notice> : null}
    <div className="two-col wide-left">
      <Card>
        <h2>Запрос</h2>
        <div className="chips">{demoQueries.map((q) => <button key={q} className="chip" onClick={() => setQuery(q)}>{q}</button>)}</div>
        <textarea value={query} onChange={(e) => setQuery(e.target.value)} rows={4} />
        <div className="form-grid search-filter-grid">
          <label>Process<input placeholder="water desalination" value={processFilter} onChange={(e) => setProcessFilter(e.target.value)} /></label>
          <label>Material<input placeholder="sulfates" value={materialFilter} onChange={(e) => setMaterialFilter(e.target.value)} /></label>
          <label>Country<input placeholder="RU / WORLD" value={countryFilter} onChange={(e) => setCountryFilter(e.target.value)} /></label>
          <label>Year from<input type="number" min="1900" max="2100" value={yearFrom} onChange={(e) => setYearFrom(e.target.value === '' ? '' : Number(e.target.value))} /></label>
          <label>Year to<input type="number" min="1900" max="2100" value={yearTo} onChange={(e) => setYearTo(e.target.value === '' ? '' : Number(e.target.value))} /></label>
          <label>Geo scope<select value={geoScope} onChange={(e) => setGeoScope(e.target.value)}><option value="all">all</option><option value="domestic">domestic</option><option value="foreign">foreign</option><option value="unknown">unknown</option></select></label>
          <label>Status<select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}><option value="">any</option><option value="auto_extracted">auto_extracted</option><option value="source_supported">source_supported</option><option value="expert_verified">expert_verified</option><option value="contradicted">contradicted</option><option value="deprecated">deprecated</option></select></label>
          <label>Fact type<input placeholder="technology_applicability" value={factTypeFilter} onChange={(e) => setFactTypeFilter(e.target.value)} /></label>
          <label>Confidence min<input type="number" min="0" max="1" step="0.01" value={confidence} onChange={(e) => setConfidence(Number(e.target.value))} /></label>
        </div>
        <small className="field-hint">Фильтры process/material/year/country/status/fact_type уходят в backend SearchFilters и применяются вместе с QueryPlan.</small>
        <label className="check"><input type="checkbox" checked={numericEnabled} onChange={(e) => setNumericEnabled(e.target.checked)} /> Числовой фильтр</label>
        {numericEnabled ? <div className="form-grid numeric-grid">
          <label>Parameter<input value={numericParameter} onChange={(e) => setNumericParameter(e.target.value)} /></label>
          <label>Operator<select value={numericOperator} onChange={(e) => setNumericOperator(e.target.value)}>{['between', '<', '<=', '>', '>=', '='].map((op) => <option key={op}>{op}</option>)}</select></label>
          <label>Min<input type="number" value={numericMin} onChange={(e) => setNumericMin(e.target.value === '' ? '' : Number(e.target.value))} /></label>
          <label>Max<input type="number" value={numericMax} onChange={(e) => setNumericMax(e.target.value === '' ? '' : Number(e.target.value))} /></label>
          <label>Unit<input placeholder="%, degC, MPa, m/s, mg/L" value={numericUnit} onChange={(e) => setNumericUnit(e.target.value)} /></label>
        </div> : null}
        <Button disabled={loading} onClick={() => run(search)}>Найти</Button>
      </Card>

      <Card>
        <h2>Ответ</h2>
        {lastSearch?.answer ? <>
          <p className="answer-summary">{String(lastSearch.answer.summary || '—')}</p>
          <div className="metric-row">
            <Metric label="Facts" value={facts.length} />
            <Metric label="Sources" value={lastSearch.sources?.length || 0} />
            <Metric label="Confidence" value={formatNumber(Number(lastSearch.answer.confidence ?? 0))} />
          </div>
          {Array.isArray(lastSearch.answer.recommendations) ? <ul>{lastSearch.answer.recommendations.map((r, i) => <li key={i}>{String(r)}</li>)}</ul> : null}
        </> : <EmptyState title="Пока нет ответа" text="Запустите поиск, чтобы увидеть summary и факты." />}
      </Card>
    </div>

    {lastSearch?.answer && answerSections.length ? <div className="answer-sections">
      {answerSections.map((section, index) => <AnswerSectionView key={`${String(section.title || 'section')}-${index}`} section={section} />)}
    </div> : null}

    {relatedExperts.length ? <Card>
      <h2>Связанные эксперты и организации</h2>
      <DataTable
        rows={relatedExperts}
        columns={[
          { key: 'name', header: 'Name' },
          { key: 'kind', header: 'Kind' },
          { key: 'affiliation', header: 'Affiliation' },
          { key: 'location', header: 'Location' },
          { key: 'confidence', header: 'Confidence', render: (row) => formatNumber(Number(row.confidence ?? 0)) },
        ]}
      />
    </Card> : null}

    {lastSearch ? <>
      <Card>
        <div className="section-head"><h2>Факты</h2><Button variant="secondary" onClick={() => setPage('graph')}>Открыть граф</Button></div>
        <DataTable rows={facts as unknown as Record<string, unknown>[]} columns={[
          { key: 'id', header: 'ID' },
          { key: 'claim_text', header: 'Claim' },
          { key: 'confidence', header: 'Conf.', render: (r) => formatNumber(Number(r.confidence ?? 0)) },
          { key: 'status', header: 'Status', render: (r) => <Badge tone={toneForStatus(String(r.status || ''))}>{String(r.status || '—')}</Badge> },
          { key: 'source_title', header: 'Source' },
          { key: 'source_page', header: 'Page' },
          { key: 'source_quote', header: 'Quote', render: (r) => <span className="quote-cell">{String(r.source_quote || '—')}</span> }
        ]} />
      </Card>
      {lastSearch.retrieved_evidence?.length ? <Card>
        <h2>Evidence из полного корпуса</h2>
        <DataTable rows={lastSearch.retrieved_evidence as Record<string, unknown>[]} columns={[
          { key: 'filename', header: 'Source' },
          { key: 'page_start', header: 'Page' },
          { key: 'score', header: 'Score', render: (r) => formatNumber(Number(r.score ?? 0)) },
          { key: 'text', header: 'Excerpt', render: (r) => <span className="quote-cell">{String(r.text || '—').slice(0, 600)}</span> }
        ]} />
      </Card> : null}
      <Card>
        <h2>Числовые параметры</h2>
        <DataTable rows={facts.flatMap((f) => (f.numeric_values || []).map((n) => ({ fact_id: f.id, ...n }))) as unknown as Record<string, unknown>[]} columns={[
          { key: 'fact_id', header: 'Fact' },
          { key: 'display_name', header: 'Параметр' },
          { key: 'parameter', header: 'Key' },
          { key: 'value_min', header: 'Min' },
          { key: 'value_max', header: 'Max' },
          { key: 'unit_normalized', header: 'Unit' },
          { key: 'source_text', header: 'Source text' }
        ]} />
      </Card>
      <div className="two-col">
        <Card><h2>QueryPlan</h2><JsonBlock value={lastSearch.query_plan} /></Card>
        <Card><h2>Debug</h2>{lastSearch.debug ? <><label className="check"><input type="checkbox" checked={showDebug} onChange={(e) => setShowDebug(e.target.checked)} /> Показать debug</label>{showDebug ? <JsonBlock value={lastSearch.debug} /> : <Notice tone="info">Debug доступен только если backend EXPOSE_DEBUG=true и роль admin/analyst.</Notice>}</> : <Notice tone="info">Backend не вернул debug. Это нормально для demo-hardened режима.</Notice>}</Card>
      </div>
    </> : null}
  </>;
}

function GraphPage({ ctx, lastSearch }: { ctx: AppContext; lastSearch: SearchResponse | null }) {
  const [factIds, setFactIds] = useState(lastSearch?.facts?.map((f) => f.id).join(',') || '');
  const [graph, setGraph] = useState<GraphData | undefined>(lastSearch?.graph);
  const [selected, setSelected] = useState<unknown>(null);
  const { loading, error, run } = useAsyncAction();

  useEffect(() => {
    if (lastSearch?.graph) setGraph(lastSearch.graph);
    if (lastSearch?.facts?.length) setFactIds(lastSearch.facts.map((f) => f.id).join(','));
  }, [lastSearch]);

  async function loadSubgraph() {
    const ids = factIds.split(',').map((x) => x.trim()).filter(Boolean);
    if (!ids.length) throw new Error('fact_ids required');
    const data = await apiGet<GraphData>(ctx, '/api/graph/subgraph', { fact_ids: ids.join(','), mode: ctx.graphMode, limit: 100 });
    setGraph(data);
  }

  return <>
    <PageTitle title="Graph" subtitle="Fact-centric graph view. Arbitrary graph dump отключён: subgraph требует fact_ids." />
    <RoleGate ctx={ctx} allowed={INTERNAL_ROLES} action="Graph view">
      {error ? <Notice tone="danger">{error}</Notice> : null}
      <Card>
        <div className="inline-form"><input value={factIds} onChange={(e) => setFactIds(e.target.value)} placeholder="fact_id_1,fact_id_2" /><Button disabled={loading || !factIds.trim()} onClick={() => run(loadSubgraph)}>Load subgraph</Button></div>
        <Notice tone="info">Последний search response уже содержит compact graph. Здесь можно перезагрузить graph по fact_ids.</Notice>
      </Card>
      <GraphView graph={graph} onSelect={(node) => setSelected(node)} />
      {selected ? <Card><h3>Selected node</h3><JsonBlock value={selected} /></Card> : null}
    </RoleGate>
  </>;
}

function ComparePage({ ctx }: { ctx: AppContext }) {
  const [process, setProcess] = useState('mine water deep injection');
  const [groupBy, setGroupBy] = useState('geo_scope');
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [tableMode, setTableMode] = useState<'table' | 'cards'>('table');
  const { loading, error, run } = useAsyncAction();

  async function compare() {
    const data = await apiPost<Record<string, unknown>>(ctx, '/api/compare', { process, group_by: groupBy, geo_scope: 'all', confidence_min: 0, limit: 100 });
    setResult(data);
  }

  const table = (result?.table as Record<string, unknown>[] | undefined) || [];
  return <>
    <PageTitle title="Compare" subtitle="Группировка практик и экономических параметров. Это demo analytics поверх extracted facts." />
    {ctx.role === 'external_partner' ? <Notice tone="warning">External partner видит только public-данные; internal mock может дать пустую таблицу.</Notice> : null}
    {error ? <Notice tone="danger">{error}</Notice> : null}
    <Card>
      <div className="form-grid">
        <label>Process<input value={process} onChange={(e) => setProcess(e.target.value)} /></label>
        <label>Group by<select value={groupBy} onChange={(e) => setGroupBy(e.target.value)}><option value="geo_scope">geo_scope</option><option value="country">country</option><option value="status">status</option><option value="fact_type">fact_type</option></select></label>
      </div>
      <Button disabled={loading} onClick={() => run(compare)}>Сравнить</Button>
    </Card>
    {result ? <>
      <div className="hero-grid"><Metric label="Всего фактов" value={String(result.total_facts ?? 0)} /><Metric label="Group by" value={String(result.group_by ?? '—')} /></div>
      <Card><h2>Таблица сравнения</h2><DataTable rows={table} columns={[
        { key: 'Технология / процесс', header: 'Технология / процесс' },
        { key: 'Практика', header: 'Практика' },
        { key: 'География', header: 'География' },
        { key: 'CAPEX', header: 'CAPEX' },
        { key: 'OPEX', header: 'OPEX' },
        { key: 'Условия применимости', header: 'Условия' },
        { key: 'Экологические ограничения', header: 'Экология' },
        { key: 'Confidence', header: 'Conf.' },
        { key: 'Источник', header: 'Источник' }
      ]} /></Card>
      <Card><h2>Raw groups</h2><JsonBlock value={result.groups} /></Card>
    </> : null}
  </>;
}

function FactEditorPage({ ctx, lastSearch }: { ctx: AppContext; lastSearch: SearchResponse | null }) {
  const initialId = lastSearch?.facts?.[0]?.id || 'fact_catholyte_velocity';
  const [factId, setFactId] = useState(initialId);
  const [fact, setFact] = useState<Fact | null>(null);
  const [versions, setVersions] = useState<unknown[]>([]);
  const [form, setForm] = useState({ claim_text: '', confidence: 0.5, status: 'auto_extracted', verification_level: 'source_supported', comment: 'Проверено во время демо' });
  const { loading, error, run } = useAsyncAction();
  const editable = can(ctx.role, EDIT_ROLES);
  const canVersions = can(ctx.role, ['admin', 'analyst', 'manager']);

  useEffect(() => { setFactId(initialId); }, [initialId]);

  async function loadFact() {
    const data = await apiGet<Fact>(ctx, `/api/facts/${factId}`);
    setFact(data);
    setForm({ claim_text: data.claim_text || '', confidence: data.confidence || 0.5, status: data.status || 'auto_extracted', verification_level: data.verification_level || 'source_supported', comment: 'Проверено во время демо' });
    if (canVersions) {
      const v = await apiGet<{ items: unknown[] }>(ctx, `/api/facts/${factId}/versions`);
      setVersions(v.items || []);
    } else {
      setVersions([]);
    }
  }

  async function save() {
    const data = await apiPatch<{ fact: Fact; version: unknown }>(ctx, `/api/facts/${fact?.id}`, form);
    setFact(data.fact);
    await loadFact();
  }

  return <>
    <PageTitle title="Fact Editor" subtitle="Ручная верификация, versions и audit. Read-only для ролей без edit-доступа." />
    {error ? <Notice tone="danger">{error}</Notice> : null}
    <Card>
      <div className="inline-form"><input value={factId} onChange={(e) => setFactId(e.target.value)} /><Button disabled={loading || !factId} onClick={() => run(loadFact)}>Загрузить факт</Button></div>
    </Card>
    {fact ? <div className="two-col wide-left">
      <Card>
        <h2>Редактирование</h2>
        {!editable ? <Notice tone="info">Текущая роль может просматривать факт, но не редактировать. Нужна роль admin или analyst.</Notice> : null}
        <label>Claim</label><textarea rows={5} disabled={!editable} value={form.claim_text} onChange={(e) => setForm({ ...form, claim_text: e.target.value })} />
        <div className="form-grid">
          <label>Confidence<input type="number" min="0" max="1" step="0.01" disabled={!editable} value={form.confidence} onChange={(e) => setForm({ ...form, confidence: Number(e.target.value) })} /></label>
          <label>Status<select disabled={!editable} value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })}>{['auto_extracted', 'source_supported', 'expert_verified', 'contradicted', 'deprecated'].map((s) => <option key={s}>{s}</option>)}</select></label>
        </div>
        <label>Verification level<input disabled={!editable} value={form.verification_level} onChange={(e) => setForm({ ...form, verification_level: e.target.value })} /></label>
        <label>Comment<textarea rows={3} disabled={!editable} value={form.comment} onChange={(e) => setForm({ ...form, comment: e.target.value })} /></label>
        <Button disabled={!editable || loading} onClick={() => run(save)}>Сохранить новую версию</Button>
      </Card>
      <Card><h2>Текущий факт</h2><JsonBlock value={fact} /></Card>
      <Card className="span-2"><h2>История версий</h2>{canVersions ? <JsonBlock value={versions} /> : <Notice tone="info">История версий скрыта для текущей роли. Доступ: admin / analyst / manager.</Notice>}</Card>
    </div> : null}
  </>;
}

function DashboardPage({ ctx }: { ctx: AppContext }) {
  const [data, setData] = useState<DashboardData | null>(null);
  const { loading, error, run } = useAsyncAction();
  async function load() { setData(await apiGet<DashboardData>(ctx, '/api/dashboard/coverage')); }
  useEffect(() => { if (can(ctx.role, INTERNAL_ROLES)) load().catch(() => null); }, [ctx.role]);
  const counts = data?.counts || [];
  const factCount = counts.find((x) => x.label === 'Fact')?.count || 0;
  const sourceCount = counts.find((x) => x.label === 'Source')?.count || 0;
  return <>
    <PageTitle title="Dashboard" subtitle="Access-aware агрегаты покрытия знаний без раскрытия секретов внешним ролям." />
    <RoleGate ctx={ctx} allowed={INTERNAL_ROLES} action="Dashboard">
      {error ? <Notice tone="danger">{error}</Notice> : null}
      <Card><Button disabled={loading} onClick={() => run(load)}>Обновить dashboard</Button></Card>
      <div className="hero-grid"><Metric label="Факты" value={factCount} /><Metric label="Источники" value={sourceCount} /><Metric label="Противоречия" value={data?.contradictions_count || 0} /></div>
      <div className="two-col">
        <Card><h2>Counts</h2><DataTable rows={counts as unknown as Record<string, unknown>[]} columns={[{ key: 'label', header: 'Node type' }, { key: 'count', header: 'Count' }]} /></Card>
        <Card><h2>By process</h2><DataTable rows={(data?.facts_by_process || []) as unknown as Record<string, unknown>[]} columns={[{ key: 'process', header: 'Process' }, { key: 'facts', header: 'Facts' }]} /></Card>
        <Card><h2>By geo</h2><DataTable rows={(data?.facts_by_geo || []) as unknown as Record<string, unknown>[]} columns={[{ key: 'geo_scope', header: 'Geo' }, { key: 'facts', header: 'Facts' }]} /></Card>
        <Card><h2>By status</h2><DataTable rows={(data?.facts_by_status || []) as unknown as Record<string, unknown>[]} columns={[{ key: 'status', header: 'Status' }, { key: 'facts', header: 'Facts' }]} /></Card>
      </div>
    </RoleGate>
  </>;
}

function ExportPage({ ctx, lastSearch }: { ctx: AppContext; lastSearch: SearchResponse | null }) {
  const [result, setResult] = useState<unknown>(null);
  const { loading, error, run } = useAsyncAction();
  const payload = useMemo(() => ({
    answer: lastSearch?.answer || { summary: 'No search yet', sections: [], confidence: 0 },
    fact_ids: (lastSearch?.facts || []).map((f) => f.id)
  }), [lastSearch]);
  function downloadText(filename: string, content: string, mime: string) {
    const blob = new Blob([content], { type: mime });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = filename; a.click(); URL.revokeObjectURL(url);
  }
  function downloadBase64(filename: string, base64: string, mime: string) {
    const binary = atob(base64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    const url = URL.createObjectURL(new Blob([bytes], { type: mime }));
    const a = document.createElement('a'); a.href = url; a.download = filename; a.click(); URL.revokeObjectURL(url);
  }
  return <>
    <PageTitle title="Export" subtitle="Markdown, JSON-LD и PDF. Frontend отправляет fact_ids, backend заново проверяет доступ и re-fetch'ит факты." />
    <RoleGate ctx={ctx} allowed={EXPORT_ROLES} action="Export">
      {!lastSearch ? <Notice tone="warning">Сначала выполните поиск. Пока будет экспортирован пустой demo payload.</Notice> : null}
      {error ? <Notice tone="danger">{error}</Notice> : null}
      <div className="action-grid">
        <Card><h3>Markdown</h3><Button disabled={loading} onClick={() => run(async () => { const data = await apiPost<{ content: string }>(ctx, '/api/export/markdown', payload); setResult(data); downloadText('scientific_knot_result.md', data.content, 'text/markdown'); })}>Скачать .md</Button></Card>
        <Card><h3>JSON-LD</h3><Button disabled={loading} onClick={() => run(async () => { const data = await apiPost<{ content: unknown }>(ctx, '/api/export/jsonld', payload); setResult(data); downloadText('scientific_knot_result.jsonld', JSON.stringify(data.content, null, 2), 'application/ld+json'); })}>Скачать .jsonld</Button></Card>
        <Card><h3>PDF</h3><Button disabled={loading} onClick={() => run(async () => { const data = await apiPost<{ content_base64: string; filename: string }>(ctx, '/api/export/pdf', payload); setResult({ ...data, content_base64: `${data.content_base64.slice(0, 100)}...` }); downloadBase64(data.filename || 'scientific_knot_result.pdf', data.content_base64, 'application/pdf'); })}>Скачать .pdf</Button></Card>
      </div>
      {result ? <Card><h3>Export result</h3><JsonBlock value={result} /></Card> : null}
    </RoleGate>
  </>;
}

function AuditPage({ ctx }: { ctx: AppContext }) {
  const [logs, setLogs] = useState<Record<string, unknown>[]>([]);
  const [limit, setLimit] = useState(100);
  const [onlyRole, setOnlyRole] = useState(false);
  const { loading, error, run } = useAsyncAction();
  async function load() { const data = await apiGet<{ items: Record<string, unknown>[] }>(ctx, '/api/audit', { limit }); setLogs(data.items || []); }
  useEffect(() => { if (can(ctx.role, AUDIT_ROLES)) load().catch(() => null); }, [ctx.role]);
  const rows = onlyRole ? logs.filter((x) => x.role === ctx.role) : logs;
  return <>
    <PageTitle title="Audit" subtitle="Журнал действий, role-aware context и проверка, что backend не использует fallback role." />
    <RoleGate ctx={ctx} allowed={AUDIT_ROLES} action="Audit">
      {error ? <Notice tone="danger">{error}</Notice> : null}
      <Card><div className="inline-form"><label>Limit<input type="number" min="10" max="500" value={limit} onChange={(e) => setLimit(Number(e.target.value))} /></label><label className="check"><input type="checkbox" checked={onlyRole} onChange={(e) => setOnlyRole(e.target.checked)} /> Только текущая роль</label><Button disabled={loading} onClick={() => run(load)}>Refresh</Button></div></Card>
      <Card><DataTable rows={rows} columns={[{ key: 'created_at', header: 'Time' }, { key: 'user_id', header: 'User' }, { key: 'role', header: 'Role' }, { key: 'action', header: 'Action' }, { key: 'target_type', header: 'Target' }, { key: 'target_id', header: 'Target ID' }]} /></Card>
    </RoleGate>
  </>;
}

export default function App() {
  const [ctx, setCtx] = useState<AppContext>(() => loadContext());
  const [page, setPage] = useState<PageKey>(() => {
    const stored = sessionStorage.getItem('sk_page') as PageKey | null;
    return stored || 'home';
  });
  const [lastSearch, setLastSearch] = useState<SearchResponse | null>(() => {
    const raw = sessionStorage.getItem('sk_last_search');
    if (!raw) return null;
    try { return JSON.parse(raw) as SearchResponse; } catch { return null; }
  });

  useEffect(() => saveContext(ctx), [ctx]);
  useEffect(() => { sessionStorage.setItem('sk_page', page); }, [page]);
  useEffect(() => {
    if (lastSearch) sessionStorage.setItem('sk_last_search', JSON.stringify(lastSearch));
  }, [lastSearch]);

  const content = {
    home: <HomePage ctx={ctx} onSearchReady={setLastSearch} />,
    import: <ImportPage ctx={ctx} />,
    search: <SearchPage ctx={ctx} lastSearch={lastSearch} setLastSearch={setLastSearch} setPage={setPage} />,
    graph: <GraphPage ctx={ctx} lastSearch={lastSearch} />,
    compare: <ComparePage ctx={ctx} />,
    facts: <FactEditorPage ctx={ctx} lastSearch={lastSearch} />,
    dashboard: <DashboardPage ctx={ctx} />,
    export: <ExportPage ctx={ctx} lastSearch={lastSearch} />,
    audit: <AuditPage ctx={ctx} />
  }[page];

  return <Layout ctx={ctx} setCtx={setCtx} page={page} setPage={setPage}>{content}</Layout>;
}
