import { escapeHtml as esc, filterRecords, paginate } from "./state.js";

const runningStates = new Set(["starting", "active", "finishing"]);
const labels = {
  starting: ["启动中", "Starting"], active: ["进行中", "In progress"], finishing: ["收尾中", "Finishing"],
  paused: ["已暂停", "Paused"], blocked: ["受阻", "Blocked"], idle: ["空闲", "Idle"],
  closing: ["待归档", "Closing"], archived: ["已归档", "Archived"], completed: ["已完成", "Completed"],
  abandoned: ["已放弃", "Abandoned"], historical: ["历史记录", "Historical"],
  failed: ["失败", "Failed"], indeterminate: ["待判定", "Indeterminate"],
  pending: ["待处理", "Pending"], resolved: ["已处理", "Resolved"],
};

const statusText = (status, l) => l(...(labels[status] || [status, status]));

export function workspaceSummary(monitor, l) {
  const groups = (monitor?.workflows || []).filter(item => item.status !== "archived");
  const running = groups.filter(item => runningStates.has(item.current_status)).length;
  const counts = [
    [groups.filter(item => item.current_status === "paused").length, l("暂停", "Paused")],
    [groups.filter(item => item.current_status === "blocked").length, l("受阻", "Blocked")],
    [groups.filter(item => item.issues?.length).length, l("待核实", "Needs verification")],
    [(monitor?.conflicts || []).filter(item => item.status === "pending").length, l("待处理冲突", "Open conflicts")],
    [monitor?.pending_events, l("待同步事件", "Pending audit events")],
    [monitor?.migration_conflicts?.length, l("迁移冲突", "Migration conflicts")],
    [monitor?.sync_failures?.length, l("同步失败", "Sync failures")],
  ];
  return '<b class="workspace-running"><span class="pulse" aria-hidden="true"></span>'+
    esc(l("进行中", "In progress"))+' '+running+'</b>'+
    counts.filter(([count]) => count).map(([count, label]) =>
      '<span class="workspace-alert">'+esc(label)+' <b>'+esc(count)+'</b></span>').join("");
}

function timestamp(value, language) {
  if (!value) return "";
  const date = new Date(value);
  const display = Number.isNaN(date.getTime()) ? value : date.toLocaleString(language);
  return '<time datetime="'+esc(value)+'">'+esc(display)+'</time>';
}

function statusBadge(status, l) {
  const style = ["paused","blocked","closing","failed","indeterminate"].includes(status)
    ? "warn-label" : runningStates.has(status) ? "" : "muted-label";
  return '<span class="badge '+style+'">'+esc(statusText(status,l))+'</span>';
}

function workspaceRow(item, open, body, l, language) {
  const status = item.status === "open" ? item.current_status || "idle" : item.status;
  const title = item.track === "stable" ? l("主线维护", "Main maintenance") : item.topic || item.branch;
  const issues = item.issues || [];
  const dirty = item.dirty;
  const summaryFocus = esc("row:" + item.workflow_id);
  return '<details class="stage-card workspace-row'+(open ? ' current" open' : '"')+'>'+
    '<summary tabindex="0" data-workspace-row="'+esc(item.workflow_id)+'" data-workspace-focus="'+summaryFocus+'">'+
      '<span class="stage-heading"><b>'+esc(title)+'</b><code>'+esc(item.branch)+'</code>'+
      '</span>'+
      '<span class="workspace-row-meta"><span class="workspace-tags">'+statusBadge(status,l)+
      (dirty === true ? '<span class="badge warn-label">'+l("有未提交改动","Uncommitted changes")+'</span>' : '')+
      (issues.length ? '<span class="badge warn-label">'+l("状态待核实","Needs verification")+'</span>' : '')+'</span>'+
      (dirty === false && !issues.length ? '<small>'+l("工作区干净","Clean workspace")+'</small>' : '')+
      timestamp(item.updated_at,language)+'</span></summary>'+
    (open ? '<div class="stage-body workspace-body">'+body+'</div>' : '')+'</details>';
}

function section(label, content) {
  return '<section class="workspace-section"><h3>'+esc(label)+'</h3>'+content+'</section>';
}

function conflictList(conflicts, l) {
  return '<div class="stage-records">'+conflicts.map(conflict=>'<article><header><b>'+esc(conflict.object)+'</b>'+
    '<span class="badge '+(conflict.status==="pending" ? 'warn-label' : 'muted-label')+'">'+
    esc(statusText(conflict.status,l))+'</span></header>'+
    '<ul class="workspace-list">'+(conflict.sources||[]).map(source=>'<li><code>'+esc(source.branch)+'</code>'+
      '</li>').join("")+'</ul></article>').join("")+'</div>';
}

function projectAlerts(monitor, open, l) {
  const conflicts = (monitor.conflicts || []).filter(item => item.status === "pending");
  const migration = monitor.migration_conflicts || [];
  const failures = monitor.sync_failures || [];
  const total = conflicts.length + migration.length + failures.length;
  if (!total) return "";
  const notes = [
    ...migration.map(item => [l("迁移冲突", "Migration conflict"), item]),
    ...failures.map(item => [l("同步失败", "Sync failure"), item]),
  ];
  return '<details class="attention-status needs-attention workspace-alerts"'+(open ? ' open' : '')+
    '><summary data-workspace-focus="alerts"><span>'+l("需要关注","Needs attention")+'</span><b>'+total+'</b></summary>'+
    '<div class="workspace-alert-body">'+(conflicts.length ? conflictList(conflicts,l) : '')+
    notes.map(([label,item])=>'<p><b>'+esc(label)+'</b> '+esc(item.reason||item.error||item.kind)+' '+
      esc(item.path||(item.paths||[]).join(" · "))+'</p>').join("")+'</div></details>';
}

function workspaceDetail(item, monitor, l) {
  const path = item.path ? '<code>'+esc(item.path)+'</code>' :
    '<span class="muted">'+l("工作区已清理或暂不可用","Workspace removed or unavailable")+'</span>';
  const notes = item.issues?.length ? section(l("待核实原因","Needs verification"),
    '<ul class="workspace-list workspace-issues">'+item.issues.map(issue=>'<li>'+esc(issue)+'</li>').join("")+'</ul>') : '';
  const conflicts = (monitor.conflicts || []).filter(conflict => conflict.sources.some(
    source => source.workflow_id === item.workflow_id || source.branch === item.branch));
  return '<dl class="details workspace-path"><dt>'+l("工作区","Workspace")+'</dt><dd>'+path+'</dd></dl>'+
    notes+(conflicts.length ? section(l("关联冲突","Related conflicts"),conflictList(conflicts,l)) : '')+
    '<div class="button-row"><button type="button" data-workspace-focus="search" data-workspace-id="'+
    esc(item.workflow_id)+'" data-archived="'+(item.status==="archived")+'">'+l("搜索此工作区","Search this workspace")+'</button></div>';
}

export function createWorkspacePage({container, onSearch, l, language}) {
  let monitor = {};
  let scope = "current";
  let query = "";
  let draft = "";
  let composing = false;
  let page = 1;
  let openId = null;
  let alertsOpen = false;
  let lastMarkup;

  function render() {
    if (composing) return;
    const root = container();
    const workflows = monitor.workflows || [];
    const archived = workflows.filter(item=>item.status==="archived");
    const current = workflows.filter(item=>item.status!=="archived");
    const rows = filterRecords(scope==="archived" ? archived : current,query,["workflow_id","branch","topic","path"]);
    const results = paginate(rows,page,12);
    page = results.page;
    if (!results.items.some(item=>item.workflow_id===openId)) openId = null;
    const html = '<section class="panel workspace-monitor" aria-label="'+l("工作区列表","Workspace list")+'">'+
      '<form class="workspace-filters"><label class="workspace-field"><span>'+l("范围","Scope")+'</span>'+
      '<select data-workspace-focus="scope"><option value="current">'+l("当前工作流","Current workflows")+' · '+current.length+
      '</option><option value="archived"'+(scope==="archived" ? ' selected' : '')+'>'+l("已归档","Archived")+' · '+archived.length+
      '</option></select></label><label class="workspace-field workspace-query"><span>'+l("工作流或分支","Workflow or branch")+'</span>'+
      '<input type="search" data-workspace-focus="query" value="'+esc(draft)+'"></label>'+
      '<button type="submit" data-workspace-focus="filter">'+l("筛选","Filter")+'</button></form>'+
      '<div class="stage-list">'+(results.items.length ? results.items.map(item=>{
        const open = item.workflow_id===openId;
        return workspaceRow(item,open,open ? workspaceDetail(item,monitor,l) : "",l,language());
      }).join("") : '<p class="empty compact">'+l("没有符合条件的工作流","No matching workflows")+'</p>')+'</div>'+
      (results.pages>1 ? '<div class="pagination">'+
        '<button type="button" data-workspace-focus="previous"'+(page===1 ? ' disabled' : '')+'>'+l("上一页","Previous")+'</button>'+
        '<span>'+page+' / '+results.pages+'</span><button type="button" data-workspace-focus="next"'+
        (page===results.pages ? ' disabled' : '')+'>'+l("下一页","Next")+'</button></div>' : '')+
      projectAlerts(monitor,alertsOpen,l)+'</section>';

    if (html===lastMarkup) return;
    lastMarkup = html;
    const focused = document.activeElement;
    const focusKey = focused?.dataset.workspaceFocus;
    const selection = focused?.type==="search" ? [focused.selectionStart,focused.selectionEnd,focused.selectionDirection] : null;
    const {scrollX,scrollY} = window;
    root.innerHTML = html;
    bind(root);
    if (focusKey) {
      const focus = Array.from(root.querySelectorAll("[data-workspace-focus]")).find(node=>node.dataset.workspaceFocus===focusKey);
      const fallback = openId && Array.from(root.querySelectorAll("[data-workspace-row]")).find(node=>node.dataset.workspaceRow===openId);
      (focus || fallback)?.focus({preventScroll:true});
      if (selection && focus) focus.setSelectionRange(...selection);
    }
    window.scrollTo(scrollX,scrollY);
  }

  function bind(root) {
    const control = name => root.querySelector('[data-workspace-focus="'+name+'"]');
    root.querySelectorAll("[data-workspace-row]").forEach(summary=>{
      const toggle = event => {
        if (event.type==="keydown" && event.key!=="Enter" && event.key!==" ") return;
        event.preventDefault();
        if (event.repeat) return;
        const id = summary.dataset.workspaceRow;
        openId = openId===id ? null : id;
        render();
      };
      summary.addEventListener("click",toggle);
      summary.addEventListener("keydown",toggle);
    });
    root.querySelector(".workspace-filters").addEventListener("submit",event=>{
      event.preventDefault();
      query = control("query").value;
      draft = query;
      page = 1;
      render();
    });
    const search = control("query");
    search.addEventListener("input",event=>{draft = event.target.value;});
    search.addEventListener("compositionstart",()=>{composing = true;});
    search.addEventListener("compositionend",()=>{composing = false;draft = search.value;render();});
    control("scope").addEventListener("change",event=>{
      scope = event.target.value;
      page = 1;
      render();
    });
    for (const [name, delta] of [["previous",-1],["next",1]]) {
      control(name)?.addEventListener("click",()=>{page += delta; render();});
    }
    control("search")?.addEventListener("click",event=>{
      const {workspaceId,archived} = event.currentTarget.dataset;
      onSearch(workspaceId,archived==="true");
    });
    const alerts = root.querySelector(".workspace-alerts");
    alerts?.addEventListener("toggle",()=>{if (alerts.isConnected) alertsOpen = alerts.open;});
  }

  return {
    show(snapshot) {
      monitor = snapshot || {};
      render();
    },
    leave() {
      composing = false;
      lastMarkup = null;
    },
  };
}
