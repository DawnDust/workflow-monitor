export const LANGUAGE_KEY = "workflow-monitor.language";
export const DEFAULT_LANGUAGE = "zh-CN";

export function normalizeLanguage(value) {
  return value === "en" ? "en" : DEFAULT_LANGUAGE;
}

export function loadLanguage(storage = globalThis.localStorage) {
  try {
    return normalizeLanguage(storage?.getItem(LANGUAGE_KEY));
  } catch {
    return DEFAULT_LANGUAGE;
  }
}

export function saveLanguage(language, storage = globalThis.localStorage) {
  const normalized = normalizeLanguage(language);
  try {
    storage?.setItem(LANGUAGE_KEY, normalized);
  } catch {
    // WebView storage can be unavailable in hardened or transient profiles.
  }
  return normalized;
}

const STATUS_EN = {
  idle:"Not started", starting:"Starting cycle", started:"Cycle started", working:"In progress",
  "progress-recorded":"Progress recorded", ready:"Ready to finish", finishing:"Finishing", completed:"Completed",
  "recovery-required":"Recovery required", active:"Active", blocked:"Blocked", failed:"Failed",
  indeterminate:"Pending judgment", abandoned:"Abandoned", available:"Available", needs_input:"Waiting for AI text",
  running:"Running", paused:"Paused", cancelled:"Cancelled", validated:"Validated", negative:"Negative result",
  inconclusive:"Inconclusive", decision:"Decision", alternatives:"Alternatives", basis:"Basis",
  reopen_condition:"Reopen condition", immutable:"Immutable", missing:"Missing file", archived:"Archived", ok:"OK",
  attention:"Needs attention", "missing directory":"Missing directory", current:"Current issue",
  old_version:"Old-version issue", old_version_protected:"Protected old-version issue", resolved:"Resolved",
  exported:"Exported", retired:"Retired", clean:"Clean worktree", dirty:"Dirty worktree", synced:"Synced",
  ahead:"Local ahead", behind:"Local behind", diverged:"Diverged", unavailable:"Unavailable",
  "build match":"EXE matches repository", "latest release":"Latest release",
  "unreleased software":"Software changes after release",
};

const SECTION_EN = {
  "工作流 Workflow":"Workflow", "任务 Task":"Task", "动作 Action":"Action", "阶段 Stage":"Stage",
  "探索 Exploration":"Exploration", "决策 Decision":"Decision", "资料 Resource":"Resource",
  "诊断 Diagnostics":"Diagnostics", "外置工具 External tool":"External tool",
  "Git 与发布 Git / Release":"Git / Release",
};

const GUIDANCE_EN = {
  "工作流 Workflow":"Continue through the structured lifecycle. If a write was interrupted, use task recover instead of deleting state.",
  "任务 Task":"Review the task evidence and continue, resolve the blocker, or close it through the structured lifecycle.",
  "动作 Action":"Follow the availability reason and let AI invoke the corresponding structured action when it is safe.",
  "阶段 Stage":"Keep the stage summary, current step, next step, and evidence aligned with the actual work.",
  "探索 Exploration":"Preserve the branch and evidence, record the conclusion, and only prepare a Squash PR for validated work.",
  "决策 Decision":"Preserve the append-only decision record and add a new decision when the route changes.",
  "资料 Resource":"Keep the catalog entry, expected type, and project-relative file location consistent.",
  "诊断 Diagnostics":"Review the incident and export a redacted diagnostics bundle before reporting a bug when needed.",
  "外置工具 External tool":"Use the external tool only when explicitly selected; the workbench never runs it automatically.",
  "Git 与发布 Git / Release":"Verify worktree, commit, remote, and release state separately before treating work as published.",
};

export function glossaryText(section, item, language) {
  if (language !== "en") return {
    section, meaning:item.meaning, implication:item.implication, nextStep:item.next_step,
  };
  const sectionName = SECTION_EN[section] || section;
  const meaning = STATUS_EN[item.code] || item.code;
  return {
    section:sectionName,
    meaning,
    implication:`${meaning} is the current ${sectionName.toLocaleLowerCase()} state.`,
    nextStep:GUIDANCE_EN[section] || "Review the recorded evidence and follow the safe structured workflow.",
  };
}
