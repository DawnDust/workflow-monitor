import { escapeHtml as esc, evidenceLabel, filterRecords, graphNeighborhood, text } from "./state.js";

const pages = [
  ["workflow", "⌁", "工作流"], ["overview", "◫", "概览"], ["search", "⌕", "搜索"],
  ["resources", "◇", "资料"], ["workbench", "▦", "工作台"], ["diagnostics", "◌", "诊断"],
  ["explain", "?", "解释"], ["research-map", "◎", "科研地图"],
  ["exploration-compare", "⇄", "探索对比"], ["evidence-matrix", "▤", "证据矩阵"],
  ["advanced", "⋯", "高级查看"],
];

const state = { page: "overview", snapshot: null, token: null, graphFocus: null, query: "" };
const $ = selector => document.querySelector(selector);
const bridge = () => window.pywebview?.api;

function notice(message, tone = "info") {
  const node = $("#notice");
  node.textContent = message; node.className = `notice ${tone}`;
  clearTimeout(notice.timer); notice.timer = setTimeout(() => node.classList.add("hidden"), 6000);
}

async function invoke(method, ...args) {
  const api = bridge();
  if (!api || typeof api[method] !== "function") throw new Error("本地桥接尚未就绪");
  return api[method](...args);
}

function renderNavigation() {
  $("#navigation").innerHTML = pages.map(([id, icon, label]) =>
    `<button data-page="${id}" class="${state.page === id ? "active" : ""}"><span>${icon}</span>${label}</button>`).join("");
  $("#navigation").querySelectorAll("button").forEach(button => button.onclick = () => {
    state.page = button.dataset.page; state.graphFocus = null; renderNavigation(); render();
  });
}

function statusStrip(s) {
  const context = s.context || {}, attempt = s.attempt || {}, active = context.active_task || {};
  const label = attempt.state ? `探索 ${attempt.state}` : active.task_id ? "任务进行中" : "当前无活动任务";
  $("#status-strip").className = "status-strip";
  $("#status-strip").innerHTML = `<span class="pulse"></span><b>${esc(label)}</b><code>${esc(s.branch)}</code><span>STATE · ${esc((state.token || "").slice(0, 8).toUpperCase())}</span>`;
}

function panel(title, body, kicker = "LOCAL SNAPSHOT", extra = "") {
  return `<article class="panel"><header><div><small>${kicker}</small><h2>${esc(title)}</h2></div>${extra}</header>${body}</article>`;
}

function cards(values) {
  return `<div class="metrics">${values.map(([label, value, note, tone]) => `<article><span>${esc(label)}</span><b class="${tone || ""}">${esc(value)}</b><small>${esc(note)}</small></article>`).join("")}</div>`;
}

function overview(s) {
  const c = s.context || {}, health = s.health || {}, attempt = s.attempt || {};
  const profile = c.project_profile || {}, overviewState = c.overview_state || c.state || {};
  return cards([
    ["当前任务", c.active_task ? "1" : "0", c.active_task?.goal || "无活动任务"],
    ["资料索引", (s.catalog_items || []).length, `${(s.catalog_relations || []).length} 条显式关系`, "accent"],
    ["探索尝试", (s.explorations || []).length + (attempt.attempt_id ? 1 : 0), attempt.state || "无当前尝试"],
    ["系统健康", health.status || "未知", `${health.events || 0} events`, health.status === "passed" ? "good" : "warn"],
  ]) + `<div class="grid two">${panel("项目主线", `<dl class="details"><dt>描述</dt><dd>${esc(profile.description)}</dd><dt>大目标</dt><dd>${esc(profile.big_goal)}</dd><dt>当前判断</dt><dd>${esc(overviewState.judgment)}</dd><dt>断点</dt><dd>${esc(overviewState.breakpoint)}</dd></dl>`, "PROJECT STATE")}${panel("下一步", `<ol class="steps">${(c.visible_next_steps || overviewState.next_steps || []).map(step => `<li>${esc(step)}</li>`).join("") || "<li>暂无登记的下一步</li>"}</ol>`, "NEXT ACTION")}</div>`;
}

function workflow(s) {
  const c = s.context || {}, task = c.active_task || {}, attempt = s.attempt || {};
  const history = (s.history || []).slice(0, 10);
  const actions = s.action_matrix?.actions || [];
  return `<div class="grid two">${panel("当前工作周期", `<dl class="details"><dt>任务</dt><dd>${esc(task.task_id || attempt.attempt_id)}</dd><dt>目标</dt><dd>${esc(task.goal || attempt.goal)}</dd><dt>轨道</dt><dd>${esc(attempt.track || "stable")}</dd><dt>当前步骤</dt><dd>${esc(attempt.current_step)}</dd><dt>进展</dt><dd>${esc(attempt.progress)}</dd><dt>下一步</dt><dd>${esc(attempt.next_step)}</dd></dl>`, "ACTIVE CYCLE")}${panel("安全边界", `<p class="muted">Web 视图保持业务只读。任务、阶段、探索、决策和资料关系仍由结构化 CLI 生命周期写入。</p><div class="boundary"><b>允许</b><span>刷新 · 更新检查 · 诊断导出 · 打开受限路径 · 复制上下文</span><b>禁止</b><span>任意命令 · 任意文件读取 · SQLite 写入</span></div>`, "READ ONLY")}</div>${panel("动作可用性", table(actions, [["category","类别"],["label","动作"],["status","状态"],["missing_fields","等待输入"]]), "ACTION MATRIX")}${panel("最近完成", table(history, [["occurred_at","时间"],["summary","任务"],["result","结果"],["branch","分支"]]), "TASK HISTORY")}`;
}

function table(records, columns, clickKind = "") {
  if (!records.length) return `<div class="empty">暂无记录</div>`;
  return `<div class="table-wrap"><table><thead><tr>${columns.map(([,label]) => `<th>${esc(label)}</th>`).join("")}</tr></thead><tbody>${records.map((record, index) => `<tr ${clickKind ? `data-record="${index}" data-kind="${clickKind}" tabindex="0"` : ""}>${columns.map(([key]) => `<td>${esc(record[key])}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
}

function search(s) {
  const results = filterRecords(s.search_index || [], state.query, ["title","summary","search_text","branch","kind_label"]);
  return panel("跨记录搜索", `<label class="searchbox"><span>⌕</span><input id="search-input" value="${esc(state.query)}" placeholder="搜索任务、事件、决策、资料、分支…"></label><p class="count">${results.length} 条结果</p>${table(results.slice(0, 200), [["kind_label","类型"],["title","标题"],["summary","摘要"],["branch","分支"]], "search")}`, "INDEX · LOCAL");
}

function resources(s) {
  return `${cards((s.resource_directories || []).map(x => [x.label, x.actual_files, `${x.indexed_files} 已登记 · ${x.status}`, x.status === "ok" ? "good" : "warn"]))}${panel("资料目录与索引", table(s.catalog_items || [], [["kind_label","类型"],["title","标题"],["status","状态"],["path","项目路径"],["branch","分支"]], "catalog"), "CATALOG")}`;
}

function workbench(s) {
  const tools = s.external_tools || [];
  return `<div class="grid two">${panel("科研上下文", `<p class="muted">选择登记资料并复制 Markdown 上下文，交给 AI 或本地工具继续工作。不会自动上传内容。</p><button id="copy-context" class="primary-button">复制全部登记资料上下文</button>`, "CONTEXT")}${panel("外置软件", table(tools, [["kind_label","类型"],["name","名称"],["status","状态"]]), "REGISTERED TOOLS")}</div>`;
}

function diagnostics(s) {
  const d = s.diagnostics || {}, issues = d.issues || d.active || [];
  return `<div class="grid two">${panel("诊断状态", `<dl class="details"><dt>状态</dt><dd>${esc(d.status || "正常")}</dd><dt>最新事件</dt><dd>${esc(d.latest_incident_id)}</dd><dt>活动问题</dt><dd>${esc(issues.length)}</dd></dl><div class="button-row"><button id="export-diagnostics" class="primary-button">导出脱敏 ZIP</button><button id="report-bug">报告 Bug</button></div>`, "PRIVACY SAFE")}${panel("问题", table(issues, [["occurred_at","时间"],["code","代码"],["summary","摘要"]]), "LOCAL LEDGER")}</div>`;
}

function explain(s) {
  return `<div class="grid two">${panel("数据如何流动", `<div class="flow"><b>事件日志</b><i>→</i><b>SQLite 投影</b><i>→</i><b>只读快照</b><i>→</i><b>WebView2</b></div><p class="muted">浏览器界面不直接访问 SQLite，也不开放网络端口。Python 桥接只暴露明确白名单。</p>`, "ARCHITECTURE")}${panel("关系解释", `<div class="legend vertical"><span><i class="solid"></i>显式 catalog 关系：可作为证据</span><span><i class="dashed"></i>task_id / branch / stage 派生：只表示过程关联</span></div><p class="muted">证据矩阵中的“未登记”不代表没有证据，只表示当前 catalog 中没有登记对应关系。</p>`, "PROVENANCE")}</div>`;
}

function graphView(s) {
  const source = s.research?.graph || {nodes:[],edges:[]};
  const graph = state.graphFocus ? graphNeighborhood(source, state.graphFocus, 1) : source;
  const max = 80, clipped = graph.nodes.length > max, nodes = graph.nodes.slice(0, max);
  const allowed = new Set(nodes.map(x => x.id));
  const edges = graph.edges.filter(x => allowed.has(x.source) && allowed.has(x.target));
  const width = 960, height = 540, cx = width/2, cy = height/2;
  const positioned = new Map(nodes.map((node, i) => { const ring = Math.floor(i / 18)+1, slot=i%18, a=(slot/18)*Math.PI*2; const radius=Math.min(70+ring*105,225); return [node.id,{...node,x:cx+Math.cos(a)*radius,y:cy+Math.sin(a)*radius}]; }));
  return panel("科研地图", `${clipped ? `<div class="notice warn">节点超过 ${max} 个，已显示安全上限。请搜索或点击节点聚焦。</div>` : ""}<div class="graph-tools"><input id="graph-search" placeholder="搜索节点并聚焦"><button id="graph-reset">显示全部</button><span>${nodes.length} 节点 · ${edges.length} 关系</span></div><div class="graph-canvas"><svg viewBox="0 0 ${width} ${height}">${edges.map(edge => { const a=positioned.get(edge.source),b=positioned.get(edge.target); return `<line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" class="${edge.provenance}" />`; }).join("")}${[...positioned.values()].map(node => `<g class="graph-node" data-node="${esc(node.id)}" transform="translate(${node.x} ${node.y})"><circle r="13" class="${esc(node.kind)}"></circle><text y="27" text-anchor="middle">${esc(node.label).slice(0,18)}</text></g>`).join("")}</svg></div><div class="legend"><span><i class="solid"></i>已登记关系</span><span><i class="dashed"></i>过程派生关系</span></div>`, "RESEARCH LENS");
}

function comparison(s) {
  const records = s.research?.explorations || [];
  return panel("探索对比", `<div class="compare">${records.map(x => `<article><header><span class="badge ${esc(x.state)}">${esc(x.state)}</span><small>${x.is_current ? "CURRENT" : "ARCHIVED"}</small></header><h3>${esc(x.goal)}</h3><dl class="details"><dt>分支</dt><dd>${esc(x.branch)}</dd><dt>假设</dt><dd>${esc(x.hypothesis)}</dd><dt>阶段</dt><dd>${esc(x.stage)}</dd><dt>当前步骤</dt><dd>${esc(x.current_step)}</dd><dt>证据</dt><dd>${esc((x.evidence || []).join("；"))}</dd><dt>结果</dt><dd>${esc(x.result)}</dd></dl></article>`).join("") || `<div class="empty">暂无探索记录</div>`}</div>`, "HYPOTHESIS · EVIDENCE · RESULT");
}

function matrix(s) {
  const data = s.research?.evidence_matrix || {rows:[]};
  return panel("证据矩阵", `<p class="muted">只展示已登记的 supports / validates / contradicts；“未登记”不等于“没有证据”。</p><div class="table-wrap"><table class="matrix"><thead><tr><th>理论</th><th>论文</th><th>实验 / 仿真</th><th>结果</th></tr></thead><tbody>${data.rows.map(row => `<tr><th>${esc(row.theory.title)}</th>${["paper","experiment","result"].map(key => `<td class="${row.cells[key].status}">${esc(evidenceLabel(row.cells[key]))}</td>`).join("")}</tr>`).join("") || `<tr><td colspan="4" class="empty">暂无已登记理论</td></tr>`}</tbody></table></div>`, "EXPLICIT EVIDENCE ONLY");
}

function advanced(s) {
  return `${panel("版本与运行状态", `<div class="button-row"><button id="check-updates" class="primary-button">检查更新</button><button id="software-refresh">刷新软件交付状态</button></div><pre>${esc(JSON.stringify({branch:s.branch,health:s.health,classification:s.classification,active_task_warning:s.active_task_warning}, null, 2))}</pre>`, "ADVANCED")}${panel("原始事件", table((s.events || []).slice(0,500), [["occurred_at","时间"],["event_type","类型"],["task_id","任务"],["branch","分支"]], "event"), "EVENT JOURNAL")}`;
}

function render() {
  const s = state.snapshot; const page = pages.find(x => x[0] === state.page);
  $("#page-title").textContent = page?.[2] || "概览";
  if (!s) { $("#content").innerHTML = `<div class="loading-card"><span></span>正在准备本地只读快照…</div>`; return; }
  const views = {workflow,overview,search,resources,workbench,diagnostics,explain,"research-map":graphView,"exploration-compare":comparison,"evidence-matrix":matrix,advanced};
  $("#content").innerHTML = views[state.page](s);
  bindPageEvents(s);
}

function bindPageEvents(s) {
  $("#search-input")?.addEventListener("input", event => { state.query = event.target.value; render(); $("#search-input")?.focus(); });
  $("#copy-context")?.addEventListener("click", async () => { const r=await invoke("copy_context", null); if(r.ok){await navigator.clipboard.writeText(r.data.text);notice("AI 上下文已复制");}else notice(r.error.message,"error"); });
  $("#export-diagnostics")?.addEventListener("click", async () => { const r=await invoke("export_diagnostics", null); notice(r.ok ? `诊断包已保存：${r.data.output}` : r.error.message, r.ok?"info":"error"); });
  $("#report-bug")?.addEventListener("click", async () => { const r=await invoke("report_bug"); notice(r.ok&&r.data.opened?"已打开 Bug 报告页面":"无法打开 Bug 报告页面",r.ok?"info":"error"); });
  $("#check-updates")?.addEventListener("click", async () => { const r=await invoke("check_updates"); openDrawer("更新检查", r); });
  $("#software-refresh")?.addEventListener("click", async () => { const r=await invoke("refresh_software_delivery", {}); openDrawer("软件交付状态", r); });
  $("#graph-reset")?.addEventListener("click", () => {state.graphFocus=null;render();});
  $("#graph-search")?.addEventListener("change", event => { const q=event.target.value.toLocaleLowerCase(); const hit=(s.research?.graph.nodes||[]).find(x=>x.label.toLocaleLowerCase().includes(q)); if(hit){state.graphFocus=hit.id;render();}else notice("没有匹配节点","warn"); });
  document.querySelectorAll(".graph-node").forEach(node => node.addEventListener("click",()=>{state.graphFocus=node.dataset.node;render();}));
  document.querySelectorAll("tr[data-record]").forEach(row => row.addEventListener("click",()=>openDrawer("记录详情", {kind:row.dataset.kind,index:row.dataset.record})));
}

function openDrawer(title, value) { $("#drawer-title")?.remove(); $("#drawer-body").innerHTML=`<small>DETAIL</small><h2 id="drawer-title">${esc(title)}</h2><pre>${esc(JSON.stringify(value,null,2))}</pre>`; $("#drawer").classList.remove("hidden"); }

async function refresh(force = false) {
  $("#refresh-button").disabled = true;
  try {
    const response = await invoke("refresh", state.token, force);
    if (response.data?.snapshot) state.snapshot = response.data.snapshot;
    if (response.data?.state_token) state.token = response.data.state_token;
    if (!response.ok) notice(`刷新失败，继续显示最后快照：${response.error.message}`, "error");
    statusStrip(state.snapshot); render();
  } catch (error) { notice(error.message, "error"); }
  finally { $("#refresh-button").disabled = false; }
}

function initialize() {
  const requestedTheme = new URLSearchParams(location.hash.slice(1)).get("theme");
  const preferredTheme = matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  $("#app").dataset.theme = ["light", "dark"].includes(requestedTheme) ? requestedTheme : preferredTheme;
  $("#theme-toggle").textContent = $("#app").dataset.theme === "dark" ? "☼" : "☾";
  renderNavigation(); render();
  $("#refresh-button").onclick = () => refresh(true);
  $("#theme-toggle").onclick = () => { const app=$("#app"); app.dataset.theme=app.dataset.theme==="dark"?"light":"dark"; $("#theme-toggle").textContent=app.dataset.theme==="dark"?"☼":"☾"; };
  $("#drawer-close").onclick = () => $("#drawer").classList.add("hidden");
  document.addEventListener("keydown", event => { if(event.key==="Escape") $("#drawer").classList.add("hidden"); });
  window.addEventListener("pywebviewready", async () => {
    const ready=await invoke("ready");
    $("#runtime-version").textContent=`v${ready.data.version} · bridge ready`;
    await refresh(true);
    if (ready.data.refresh_seconds > 0) {
      setInterval(() => refresh(false), ready.data.refresh_seconds * 1000);
    }
  });
  setTimeout(() => { if (!bridge()) notice("WebView2 桥接初始化较慢，请稍候…", "warn"); }, 2500);
}

initialize();
