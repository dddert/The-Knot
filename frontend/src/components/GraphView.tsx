import { useMemo, useState } from 'react';
import type { GraphData, GraphEdge, GraphNode } from '../lib/types';
import { Badge } from './Ui';

function colorFor(node: Pick<GraphNode, 'label'>) {
  const label = node.label || 'Node';
  if (label === 'Fact') return '#4f46e5';
  if (label === 'Source') return '#0284c7';
  if (label === 'ParameterValue') return '#ea580c';
  if (label === 'Process') return '#16a34a';
  if (label === 'Material') return '#9333ea';
  if (label === 'TechnologySolution') return '#0d9488';
  if (label === 'Chunk') return '#64748b';
  if (label === 'Expert' || label === 'Laboratory') return '#be123c';
  return '#475569';
}

function short(text?: string, max = 58) {
  if (!text) return '—';
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

function meta(node: GraphNode) {
  const props = node.properties || {};
  if (node.label === 'ParameterValue') {
    const display = String(props.display_name || props.parameter || 'parameter');
    const min = props.value_min ?? props.value;
    const max = props.value_max;
    const value = min !== undefined && max !== undefined ? `${min}–${max}` : max !== undefined ? `≤${max}` : min !== undefined ? String(min) : '';
    return `${display}${value ? ` · ${value}` : ''}${props.unit_normalized ? ` ${props.unit_normalized}` : ''}`;
  }
  if (node.label === 'Source') return `${props.source_type || 'source'} · ${props.access_level || '—'} · ${props.year || '—'}`;
  if (node.label === 'Chunk') return `page ${props.page || '—'} · ${short(String(props.text_preview || props.text || ''), 38)}`;
  if (node.confidence !== undefined) return `confidence ${Number(node.confidence).toFixed(2)}`;
  return String(props.canonical_name || props.name || node.label || 'node');
}

export function GraphView({ graph, onSelect }: { graph?: GraphData; onSelect?: (item: GraphNode | GraphEdge) => void }) {
  const nodes = graph?.nodes || [];
  const edges = graph?.edges || [];
  const factNodes = nodes.filter((n) => n.label === 'Fact');
  const [focusFact, setFocusFact] = useState(factNodes[0]?.id || '');
  const [showMaterials, setShowMaterials] = useState(true);
  const [showChunks, setShowChunks] = useState(true);
  const [showEdgeLabels, setShowEdgeLabels] = useState(false);

  const actualFocus = focusFact || factNodes[0]?.id || '';

  const visible = useMemo(() => {
    if (!nodes.length) return { nodes: [] as GraphNode[], edges: [] as GraphEdge[] };
    if (!actualFocus) return { nodes, edges };
    const incident = new Set<string>([actualFocus]);
    for (const edge of edges) {
      if (edge.source === actualFocus) incident.add(edge.target);
      if (edge.target === actualFocus) incident.add(edge.source);
    }
    let visibleNodes = nodes.filter((n) => incident.has(n.id));
    if (!showMaterials) visibleNodes = visibleNodes.filter((n) => n.label !== 'Material');
    if (!showChunks) visibleNodes = visibleNodes.filter((n) => n.label !== 'Chunk');
    const ids = new Set(visibleNodes.map((n) => n.id));
    return {
      nodes: visibleNodes,
      edges: edges.filter((e) => ids.has(e.source) && ids.has(e.target)),
    };
  }, [nodes, edges, actualFocus, showMaterials, showChunks]);

  if (!nodes.length) return <div className="empty graph-empty"><strong>Граф пуст</strong><span>Выполните поиск или выберите fact_ids.</span></div>;

  const width = 1080;
  const cardW = 220;
  const cardH = 72;
  const gapY = 92;
  const positions = new Map<string, { x: number; y: number }>();
  const groups: Record<string, GraphNode[]> = { left: [], center: [], params: [], right: [] };
  visible.nodes.forEach((node) => {
    if (node.label === 'Fact') groups.center.push(node);
    else if (node.label === 'ParameterValue') groups.params.push(node);
    else if (node.label === 'Source' || node.label === 'Chunk') groups.right.push(node);
    else groups.left.push(node);
  });

  function place(list: GraphNode[], x: number, yStart: number, gap = gapY) {
    list.forEach((node, i) => positions.set(node.id, { x, y: yStart + i * gap }));
  }
  place(groups.left, 50, 88);
  place(groups.center, 430, 140, 110);
  place(groups.params, 430, 330, gapY);
  place(groups.right, 810, 88);

  const bottom = Math.max(
    560,
    ...Array.from(positions.values()).map((p) => p.y + cardH + 64)
  );
  const height = bottom;

  return <div className="graph-panel">
    <div className="graph-toolbar">
      <div className="graph-controls">
        <label>Focus fact<select value={actualFocus} onChange={(e) => setFocusFact(e.target.value)}>{factNodes.map((f) => <option key={f.id} value={f.id}>{short(f.title || f.id, 72)}</option>)}</select></label>
        <label className="check"><input type="checkbox" checked={showMaterials} onChange={(e) => setShowMaterials(e.target.checked)} /> Materials</label>
        <label className="check"><input type="checkbox" checked={showChunks} onChange={(e) => setShowChunks(e.target.checked)} /> Chunks</label>
        <label className="check"><input type="checkbox" checked={showEdgeLabels} onChange={(e) => setShowEdgeLabels(e.target.checked)} /> Edge labels</label>
      </div>
      <div className="graph-legend">
        {['Fact', 'Source', 'ParameterValue', 'Process', 'Material', 'TechnologySolution', 'Chunk'].map((label) => <span key={label}><i style={{ background: colorFor({ label }) }} />{label}</span>)}
      </div>
    </div>
    <div className="graph-scroll">
    <svg viewBox={`0 0 ${width} ${height}`} className="graph-svg graph-svg-cards" role="img" aria-label="Knowledge graph">
      <defs>
        <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="#94a3b8" />
        </marker>
      </defs>
      {visible.edges.map((edge) => {
        const a = positions.get(edge.source);
        const b = positions.get(edge.target);
        if (!a || !b) return null;
        const ax = a.x + cardW / 2;
        const ay = a.y + cardH / 2;
        const bx = b.x + cardW / 2;
        const by = b.y + cardH / 2;
        const mx = (ax + bx) / 2;
        const my = (ay + by) / 2;
        return <g key={edge.id} className="graph-edge" onClick={() => onSelect?.(edge)}>
          <line x1={ax} y1={ay} x2={bx} y2={by} stroke="#94a3b8" strokeWidth="1.5" markerEnd="url(#arrow)" />
          {showEdgeLabels ? <text x={mx} y={my - 6} textAnchor="middle" className="edge-label">{edge.label}</text> : null}
        </g>;
      })}
      {visible.nodes.map((node) => {
        const p = positions.get(node.id);
        if (!p) return null;
        const color = colorFor(node);
        return <g key={node.id} className="graph-card-node" onClick={() => onSelect?.(node)}>
          <rect x={p.x} y={p.y} width={cardW} height={cardH} rx="16" fill="white" stroke={color} strokeWidth="2" />
          <rect x={p.x} y={p.y} width="8" height={cardH} rx="4" fill={color} />
          <text x={p.x + 20} y={p.y + 22} className="graph-card-type">{node.label}</text>
          <text x={p.x + 20} y={p.y + 42} className="graph-card-title">{short(node.title || node.id, 31)}</text>
          <text x={p.x + 20} y={p.y + 58} className="graph-card-meta">{short(meta(node), 34)}</text>
        </g>;
      })}
    </svg>
    </div>
    <div className="graph-stats">
      <Badge tone="blue">{visible.nodes.length}/{nodes.length} nodes</Badge>
      <Badge tone="purple">{visible.edges.length}/{edges.length} edges</Badge>
      <Badge tone="green">focus graph</Badge>
    </div>
  </div>;
}
