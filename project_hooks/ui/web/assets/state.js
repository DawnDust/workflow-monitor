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

export function paginate(records, requestedPage, pageSize) {
  const pages = Math.max(1, Math.ceil(records.length / pageSize));
  const page = Math.min(pages, Math.max(1, Number(requestedPage) || 1));
  return {page, pages, total: records.length, items: records.slice((page - 1) * pageSize, page * pageSize)};
}

export function sectionRevision(value) {
  return JSON.stringify(value ?? null);
}

export const DISPOSITIONS = {
  catalog_active:["有效","Available"], catalog_missing:["文件缺失","Missing file"],
  archived:["已归档","Archived"], active:["进行中","In progress"],
  abandoned:["已遗弃","Abandoned"], merged:["已合并","Merged"],
  merge_unknown:["合并状态未知","Merge status unknown"], pending_merge:["待合并","Pending merge"],
  pending_archive:["待归档","Pending archive"], undisposed:["未处置","Not disposed"],
  unspecified:["未标注","Unspecified"],
};

export const ARCHIVE_STATUSES = {
  archived:["已归档","Archived"], unarchived:["未归档","Not archived"],
  unknown:["归档状态未知","Archive status unknown"], not_applicable:["不适用","Not applicable"],
};
export const MERGE_STATUSES = {
  merged:["已合并","Merged"], pending_merge:["待合并","Pending merge"],
  merge_unknown:["合并状态未知","Merge status unknown"], not_applicable:["不适用","Not applicable"],
};

export function dispositionCode(record) {
  if (record.status_code) return record.status_code;
  if (record.status === "archived" || record.status === "已归档") return "archived";
  return Object.keys(DISPOSITIONS).find(code => DISPOSITIONS[code].includes(record.status)) || "unspecified";
}

export function dispositionOptions(records, kind) {
  const typed = kind === "all" ? records : records.filter(x => x.kind === kind);
  const codes = new Set(typed.map(dispositionCode));
  return ["unarchived", "all", "archived", ...Object.keys(DISPOSITIONS).filter(x => x !== "archived" && codes.has(x))];
}

export function archiveStatusCode(record) {
  if (record.archive_status_code) return record.archive_status_code;
  return dispositionCode(record) === "archived" ? "archived" : "unarchived";
}

export function mergeStatusCode(record) {
  if (record.merge_status_code) return record.merge_status_code;
  const legacy = dispositionCode(record);
  return ["merged","pending_merge","merge_unknown"].includes(legacy) ? legacy : "not_applicable";
}

function statusOptions(records, kind, field, base) {
  const typed = kind === "all" ? records : records.filter(x => x.kind === kind);
  const codes = new Set(typed.map(field));
  return [...base, ...Object.keys(field === archiveStatusCode ? ARCHIVE_STATUSES : MERGE_STATUSES)
    .filter(code => !base.includes(code) && codes.has(code))];
}

export function archiveStatusOptions(records, kind) {
  return statusOptions(records, kind, archiveStatusCode, ["unarchived","all","archived"]);
}

export function mergeStatusOptions(records, kind) {
  return statusOptions(records, kind, mergeStatusCode, ["all"]);
}

export function searchRecords(records, {kind="all", archive="unarchived", merge="all", query="", scopeSelected=false} = {}) {
  const archiveOptions = archiveStatusOptions(records, kind);
  const mergeOptions = mergeStatusOptions(records, kind);
  if (!archiveOptions.includes(archive)) archive = "unarchived";
  if (!mergeOptions.includes(merge)) merge = "all";
  const statusRows = records.filter(record => {
    const archiveCode = archiveStatusCode(record), mergeCode = mergeStatusCode(record);
    return (archive === "all" || (archive === "unarchived" ? archiveCode !== "archived" : archiveCode === archive))
      && (merge === "all" || mergeCode === merge);
  });
  const searchable = statusRows.map(record => ({...record,search_status_text:[
    ...(ARCHIVE_STATUSES[archiveStatusCode(record)] || []),
    ...(MERGE_STATUSES[mergeStatusCode(record)] || []),
    ...(DISPOSITIONS[dispositionCode(record)] || []),
  ].join(" ")}));
  const scoped = filterRecords(searchable, query, ["title","summary","search_text","branch","kind_label","status","status_code","archive_status","merge_status","search_status_text"]);
  const typed = kind === "all" ? scoped : scoped.filter(x => x.kind === kind);
  const hasCriteria = scopeSelected || Boolean(query) || kind !== "all" || archive !== "unarchived" || merge !== "all";
  return {archive, merge, archiveOptions, mergeOptions, scoped, hasCriteria, items:hasCriteria ? typed : []};
}
