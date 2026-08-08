export function text(value, fallback = "—") {
  if (value === null || value === undefined || value === "") return fallback;
  return String(value);
}

export function escapeHtml(value) {
  return text(value, "").replace(/[&<>'"]/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[char]);
}

export function filterRecords(records, query, fields = []) {
  const needle = String(query || "").trim().toLocaleLowerCase();
  if (!needle) return records;
  return records.filter(record => fields.some(field =>
    text(record?.[field], "").toLocaleLowerCase().includes(needle)));
}

export function graphNeighborhood(graph, startId, depth = 1) {
  if (!startId) return graph;
  const adjacency = new Map();
  for (const edge of graph.edges || []) {
    if (!adjacency.has(edge.source)) adjacency.set(edge.source, new Set());
    if (!adjacency.has(edge.target)) adjacency.set(edge.target, new Set());
    adjacency.get(edge.source).add(edge.target);
    adjacency.get(edge.target).add(edge.source);
  }
  const seen = new Set([startId]);
  let frontier = [startId];
  for (let level = 0; level < depth; level += 1) {
    const next = [];
    for (const id of frontier) for (const neighbor of adjacency.get(id) || []) {
      if (!seen.has(neighbor)) { seen.add(neighbor); next.push(neighbor); }
    }
    frontier = next;
  }
  return {
    nodes: (graph.nodes || []).filter(node => seen.has(node.id)),
    edges: (graph.edges || []).filter(edge => seen.has(edge.source) && seen.has(edge.target)),
  };
}

export function evidenceLabel(cell) {
  return cell?.status === "registered" ? text(cell.label) : "未登记";
}

export function sectionRevision(value) {
  return JSON.stringify(value ?? null);
}
