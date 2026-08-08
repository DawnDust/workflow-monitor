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

export function evidenceLabel(cell) {
  return cell?.status === "registered" ? text(cell.label) : "未登记";
}

export function paginate(records, requestedPage, pageSize) {
  const pages = Math.max(1, Math.ceil(records.length / pageSize));
  const page = Math.min(pages, Math.max(1, Number(requestedPage) || 1));
  return {page, pages, total: records.length, items: records.slice((page - 1) * pageSize, page * pageSize)};
}

export function sectionRevision(value) {
  return JSON.stringify(value ?? null);
}
