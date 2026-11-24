import { escapeHtml as esc, filterRecords, paginate, searchRecords, dispositionCode, archiveStatusCode, mergeStatusCode, ARCHIVE_STATUSES, MERGE_STATUSES, DISPOSITIONS } from "./state.js";
import { renderResourceView, resourceDetails } from "./resource-view.js";
import { glossaryText, loadLanguage, saveLanguage } from "./i18n.js";

import { createWorkspacePage, workspaceSummary } from "./workspace-view.js";

const pages = [
  ["workflow","⌁","工作流","Workflow"],["workspaces","⑂","工作区","Workspaces"],["overview","◫","概览","Overview"],["search","⌕","搜索","Search"],
  ["resources","◇","资料","Resources"],
  ["research-stages","◎","研究篇章","Research Chapters"],
];
const state = {page:"overview",settingsSection:"language",language:loadLanguage(),snapshot:null,snapshotToken:null,runtime:{version:"",build_identity:{}},draftQuery:"",committedQuery:"",selectedKind:"all",selectedWorkspace:"",selectedBranch:"",selectedArchiveStatus:"unarchived",selectedMergeStatus:"all",reviewOpen:false,reviewHelpOpen:false,searchPage:1,selectedResourcePath:null,folderOpen:new Set(["resources"]),resourceNavOpen:!matchMedia("(max-width:950px)").matches,resourcePage:1,eventPage:1,searchComposing:false,pendingSearchRender:false,glossaryOpen:new Set(),advancedOpen:new Set(),stageOpen:new Set(),stageExpansionInitialized:false,records:new Map()};
const productLinks={repository:"https://github.com/DawnDust/workflow-monitor",manual:"https://dawndust.github.io/workflow-monitor/",license:"https://github.com/DawnDust/workflow-monitor/blob/main/LICENSE",security:"https://github.com/DawnDust/workflow-monitor/security/advisories/new"};
const $ = q => document.querySelector(q), bridge = () => window.pywebview?.api;
const l = (zh,en) => state.language === "en" ? en : zh;
const workspaces = createWorkspacePage({
  container: () => $("#content"), onSearch: searchWorkspace, l, language: () => state.language,
});
const snapshots = new Map();
const viewErrors = new Map();
const requests = new Map();
let connected = false;
const viewName = () => state.page === "settings" && state.settingsSection !== "advanced" ? "overview" : state.page;
const pageLabel = page => state.language === "en" ? page[3] : page[2];
function notice(message,tone="info"){const n=$("#notice");n.textContent=message;n.className=`notice ${tone}`;clearTimeout(notice.timer);notice.timer=setTimeout(()=>n.classList.add("hidden"),6000);}
async function invoke(method,...args){const api=bridge();if(!api||typeof api[method]!=="function")throw new Error(l("本地桥接尚未就绪","The local bridge is not ready"));return api[method](...args);}
function panel(title,body,kicker="LOCAL SNAPSHOT",extra=""){return `<article class="panel"><header><div><small>${kicker}</small><h2>${esc(title)}</h2></div>${extra}</header>${body}</article>`;}
function cards(values,clickable=false){return `<div class="metrics ${clickable?"resource-cards":""}">${values.map(x=>`<article ${clickable?`class="resource-card" role="button" tabindex="0" data-resource-path="${esc(x[4])}"`:""}><span>${esc(x[0])}</span><b class="${x[3]||""}">${esc(x[1])}</b><small>${esc(x[2])}</small></article>`).join("")}</div>`;}
function searchStatusCell(record){const badge=(zhPrefix,enPrefix,code,labels)=>`<span class="badge disposition ${esc(code)}">${esc(l(zhPrefix,enPrefix))}${esc(l(...(labels[code]||[code,code])))}</span>`;const values=[badge("合并：","Merge: ",mergeStatusCode(record),MERGE_STATUSES),badge("归档：","Archive: ",archiveStatusCode(record),ARCHIVE_STATUSES)];if(record.kind==="catalog")values.unshift(badge("资料：","Resource: ",dispositionCode(record),DISPOSITIONS));return values.join(" ");}
function table(records,columns,kind=""){if(!records.length)return `<div class="empty">${l("暂无记录","No records")}</div>`;return `<div class="table-wrap"><table><thead><tr>${columns.map(x=>`<th>${esc(l(x[1],x[2]||x[1]))}</th>`).join("")}</tr></thead><tbody>${records.map((record,i)=>{const key=`${kind}:${record.search_key||record.event_id||record.item_id||record.task_id||i}`;state.records.set(key,record);return `<tr ${kind?`data-record-key="${esc(key)}" data-kind="${kind}" tabindex="0"`:""}>${columns.map(x=>`<td>${kind==="search"&&x[0]==="search_statuses"?searchStatusCell(record):esc(record[x[0]])}</td>`).join("")}</tr>`;}).join("")}</tbody></table></div>`;}
function renderNavigation() {
  const navigation=$("#navigation");
  if(navigation.dataset.language!==state.language){
    navigation.dataset.language=state.language;
    navigation.innerHTML=pages.map(page=>{
      const label=esc(pageLabel(page));
      return '<button data-page="'+page[0]+'" title="'+label+'"><span aria-hidden="true">'+page[1]+'</span>'+label+'</button>';
    }).join("");
    navigation.querySelectorAll("button").forEach(button=>button.onclick=()=>{state.page=button.dataset.page;render();});
  }
  navigation.querySelectorAll("button").forEach(button=>{
    const active=button.dataset.page===state.page;
    button.classList.toggle("active",active);
    if(active)button.setAttribute("aria-current","page");else button.removeAttribute("aria-current");
  });
  const settings=$("#settings-button");
  settings.classList.toggle("active",state.page==="settings");
  settings.setAttribute("aria-current",state.page==="settings"?"page":"false");
  $("#settings-label").textContent=l("设置","Settings");
  $("#refresh-button").disabled=requests.has(viewName());
}
function applyChromeLanguage(){document.documentElement.lang=state.language;$("#navigation").setAttribute("aria-label",l("主导航","Main navigation"));$("#crumb").textContent=l("本地科研工作区","LOCAL RESEARCH WORKSPACE");$("#theme-toggle").title=l("切换主题","Toggle theme");$("#theme-toggle").setAttribute("aria-label",l("切换主题","Toggle theme"));$("#refresh-button").textContent=l("↻ 刷新快照","↻ Refresh snapshot");$("#drawer-close").setAttribute("aria-label",l("关闭","Close"));}
function statusStrip(s){
  if(state.page==="workspaces"){
    $("#status-strip").className="status-strip workspaces-status";
    $("#status-strip").innerHTML=workspaceSummary(s?.project_monitor,l);
    return;
  }
const lifecycle=s.action_matrix?.state||{},step=lifecycle.lifecycle_step||{},active=lifecycle.active_task||s.context?.active_task||snapshots.get("overview")?.snapshot.context?.active_task||{};$("#status-strip").className="status-strip";const label=step.label||(active.task_id?l("任务进行中","Task in progress"):l("当前无活动任务","No active task"));$("#status-strip").innerHTML=`<span class="pulse"></span><b>${esc(label)}</b><code>${esc(s.branch)}</code><span>STATE · ${esc((state.snapshotToken||"").slice(0,8).toUpperCase())}</span>`;}

function attentionStatus(c){const count=(c.research_attention||[]).length,label=count?l("需关注","Needs attention"):l("无需关注","Clear"),explanation=count?l("下方提醒均来自已登记的结构化事实，可点击查看来源。","The signals below come from registered structured facts and link to their sources."):l("当前结构化事实未触发提醒；这不表示研究已完成，也不排除未知风险。","No structured fact currently triggers a signal; this does not mean the research is complete or free of unknown risks.");return `<details class="attention-status ${count?"needs-attention":"clear"}"><summary><span>Research Attention</span><b>${label}${count?` · ${count}`:""}</b></summary><p>${explanation}</p></details>`}
function attentionCard(c){const items=c.research_attention||[];if(!items.length)return "";const rows=items.map(x=>{const summary=x.summary?.[state.language]||x.summary?.en||x.code,sources=(x.source_ids||[]).join(" · ")||"-";return `<button class="attention-item" data-attention-target="${esc(x.target)}"><span><b>${esc(summary)}</b><small>${esc(x.code)} · ${esc(x.source_type)} · ${esc(sources)}</small></span><em>→</em></button>`}).join("");return panel(l("研究注意事项","Research attention"),`<p class="muted">${l("仅显示由现有结构化事实推导的确定性提醒，不代表科学结论。","Deterministic signals derived only from existing structured facts; they are not scientific conclusions.")}</p><div class="attention-list">${rows}</div>`,"MAX 3 · READ ONLY")}
function reviewCard(c){
  const summary=c.review_summary||{},pending=(c.reviews||[]).filter(x=>x.pending),counts=summary.pending_by_kind||{};
  const stamp=(value,empty)=>esc(value||empty),lastChanged=stamp(summary.last_research_changed_at,l("尚无科研变动","No research changes")),lastReviewed=stamp(summary.last_overall_reviewed_at,l("尚无完整审阅记录","No complete review recorded"));
  const times=`<dl class="review-times"><dt>${l("最后科研变动","Last research change")}</dt><dd>${lastChanged}</dd><dt>${l("最后整体审阅","Last complete review")}</dt><dd>${lastReviewed}</dd></dl>`;
  const labels={stage:l("篇章","Chapters"),attempt:l("探索","Explorations"),resource:l("资料","Resources")};
  const states=[["normal",l("当前正常","Clear")],["review_suggested",l("建议审阅","Review suggested")],["action_required",l("需要处理","Action needed")]];
  const active=summary.status||"normal",strip=`<div class="review-states" role="group" aria-label="${l("审阅状态","Review status")}">${states.map(([key,label])=>`<span class="${active===key?"current "+key:""}" ${active===key?'aria-current="true"':""}>${label}</span>`).join("")}</div>`;
  const deferred=summary.deferred_count?`<p class="review-deferred">${l("已延后","Deferred")} ${summary.deferred_count} ${l("项","items")}${summary.deferred_until?` · ${l("最近恢复","Next resume")} ${esc(summary.deferred_until)}`:""}</p>`:"";
  const help=`<details class="review-help" ${state.reviewHelpOpen?"open":""}><summary>${l("审阅范围与说明","Review scope and meaning")}</summary><p>${l("篇章、探索和资料按明确关联检查。当前正常表示没有待办；建议审阅表示内容变化、首次审阅或到期；需要处理表示资料缺失、未登记或关联冲突等完整性问题。延后不会清除这些问题。最后整体审阅只在所有适用对象均完成审阅时自动记录；新变化不会删除旧记录。","Chapters, explorations and resources are checked by explicit links. Clear means no pending work; review suggested means new content, a first review or an overdue review; action needed means an integrity problem such as missing or unindexed material or a conflicting link. Deferral does not clear integrity problems. A complete review is recorded automatically only when every applicable item is covered, and later changes do not erase that record.")}</p></details>`;
  if(!summary.pending_count)return panel(l("科研审阅","Research reviews"),`${strip}${times}${deferred}${help}`,"READ ONLY");
  const kinds=["stage","attempt","resource"].filter(kind=>counts[kind]);
  const totals=kinds.map(kind=>`${labels[kind]} ${counts[kind]}`).join(" · ");
  const groups=kinds.map(kind=>`<section class="review-group"><h3>${labels[kind]}</h3>${pending.filter(x=>x.review_kind===kind).map(x=>`<article class="review-item"><b>${esc(x.title)}</b><p>${esc((x.reasons||[]).join(" · "))}</p></article>`).join("")}</section>`).join("");
  return panel(l("科研审阅","Research reviews"),`${strip}${times}<p class="review-alert">${summary.pending_count} ${l("项待处理","pending items")}</p><details class="review-pending" ${state.reviewOpen?"open":""}><summary>${esc(totals)} · ${l("查看待审阅内容","View reminders")}</summary>${groups}</details>${deferred}${help}`,"READ ONLY");
}
function overview(s){
  const c=s.context||{},h=s.health||{},a=s.attempt||{},p=c.project_profile||{},o=c.overview_state||c.state||{},current=s.research?.stages?.current;
  const currentStage=current?`<div class="current-stage"><b>${l("篇章","Chapter")} ${esc(current.sequence)} · ${esc(current.title)}</b><span class="badge">${esc(current.status)}</span></div>`:`<div class="empty compact">${l("当前没有进行中的研究篇章","No active Research Chapter")}</div>`;
  const roadmap=state.runtime.physics_roadmap_available?panel(l("理论物理研究路线","Theoretical Physics Research Roadmap"),`<p class="muted">${l("这是一份研究思路参考。研究过程可以按问题需要往返调整，不构成必经阶段或完成门禁。","A reference for research thinking. The process can loop as needed and does not impose stages or completion gates.")}</p><button id="open-physics-research-roadmap" class="primary-button">${l("打开路线图","Open roadmap")}</button>`,"REFERENCE GUIDE"):"";
  const understanding=state.runtime.research_understanding_guide_available?panel(l("研究结果理解与证明阅读","Understanding research results and proofs"),`<p class="muted">${l("按需使用的理解方法，不设置评分或完成门禁。","Optional reading methods, without scores or completion gates.")}</p><button id="open-understanding-guide" class="primary-button">${l("打开理解指南","Open understanding guide")}</button>`,"REFERENCE GUIDE"):"";
  return cards([[l("当前任务","Current task"),c.active_task?"1":"0",c.active_task?.scope||l("无活动任务","No active task")],[l("资料索引","Resource index"),(s.catalog_items||[]).length,`${(s.catalog_relations||[]).length} ${l("条显式关系","explicit relations")}`,"accent"],[l("探索尝试","Explorations"),(s.attempts||[]).length,(a.state||l("无当前尝试","No current attempt"))],[l("系统健康","System health"),(h.status||l("未知","Unknown")),`${h.events||0} events`,h.status==="passed"?"good":"warn"]])+panel(l("当前篇章","Current Chapter"),`${currentStage}${attentionStatus(c)}`,"RESEARCH CHAPTER")+attentionCard(c)+reviewCard(c)+`<div class="grid two">${panel(l("项目主线","Project direction"),`<dl class="details"><dt>${l("项目描述","Project description")}</dt><dd>${esc(p.description)}</dd><dt>${l("长期方向","Long-term direction")}</dt><dd>${esc(p.big_goal)}</dd><dt>${l("当前判断","Current judgment")}</dt><dd>${esc(o.judgment)}</dd><dt>${l("断点","Breakpoint")}</dt><dd>${esc(o.breakpoint)}</dd></dl>`,"PROJECT STATE")}${panel(l("下一步","Next steps"),`<ol class="steps">${(c.visible_next_steps||o.next_steps||[]).map(x=>`<li>${esc(x)}</li>`).join("")||`<li>${l("暂无登记的下一步","No next step recorded")}</li>`}</ol>`,"NEXT ACTION")}</div>`+roadmap+understanding;
}
function workflow(s){const d=s.action_matrix?.state||{},active=d.active_task||{},writer=d.writer_lock||{},step=d.lifecycle_step||{},git=d.git||{},attempt=d.attempt||s.attempt||{},labels=state.language==="en"?["Not started","Cycle started","Working","Progress recorded","Ready","Finishing","Completed"]:["未开始","周期已建立","工作中","进展已记录","可完成","正在收尾","已完成"];const steps=labels.map((x,i)=>`<span class="${i===step.index?"current":""}">${x}</span>`).join("<i>→</i>");const detail=active.task_id?`<dl class="details"><dt>${l("任务","Task")}</dt><dd>${esc(active.task_id)}</dd><dt>${l("轨道","Track")}</dt><dd>${esc(active.track||attempt.track||"stable")}</dd><dt>${l("开始","Started")}</dt><dd>${esc(active.started_at)}</dd><dt>${l("变化","Changes")}</dt><dd>${(d.changed_paths||[]).length} ${l("个文件","files")}</dd><dt>${l("进度","Progress")}</dt><dd>${active.state_updated?l("已记录","Recorded"):l("未记录","Not recorded")}</dd><dt>${l("写锁","Write lock")}</dt><dd>${writer.status==="active"?`${esc(writer.command)} / PID ${esc(writer.pid)}`:l("空闲","Idle")}</dd><dt>${l("工作树稳定","Worktree quiet")}</dt><dd>${esc(d.worktree_quiet_seconds||0)} ${l("秒","seconds")}</dd></dl>`:`<p class="muted">${l("当前没有活动工作周期。","There is no active work cycle.")}</p>`;const delivery=`<dl class="details"><dt>${l("工作区","Worktree")}</dt><dd>${(d.dirty_paths||[]).length?`${d.dirty_paths.length} ${l("个未提交变化","uncommitted changes")}`:l("干净","Clean")}</dd><dt>${l("提交/推送","Commit / push")}</dt><dd>${esc((git.relation||l("未知","Unknown")))}</dd><dt>${l("恢复","Recovery")}</dt><dd>${esc(d.sidecar?.error||l("无需恢复","Not required"))}</dd></dl>`;return panel(l("实时生命周期","Live lifecycle"),`<div class="lifecycle-steps">${steps}</div>${detail}`,"LIVE · 3 SECOND REFRESH")+`<div class="grid two">${panel(l("交付状态","Delivery status"),delivery,"WORKTREE · COMMIT · PUSH")}${panel(l("安全边界","Safety boundary"),`<p class="muted">${l("Dashboard 保持业务只读。任务、研究篇章、探索和资料关系仍由结构化 CLI 生命周期写入。","The Dashboard remains read-only. Tasks, Research Chapters, explorations, and resource relations are written only through the structured CLI lifecycle.")}</p><div class="boundary"><b>${l("允许","Allowed")}</b><span>${l("刷新 · 更新检查 · 诊断导出 · 打开受限资料 · 复制上下文","Refresh · update checks · diagnostics export · restricted resource access · copy context")}</span><b>${l("禁止","Blocked")}</b><span>${l("任意命令 · 任意文件读取 · SQLite 写入","Arbitrary commands · arbitrary file reads · SQLite writes")}</span></div>`,"READ ONLY")}</div>`;}
function dispositionLabel(code){const pair={unarchived:["未归档","Not archived"],all:["全部","All"],...DISPOSITIONS}[code]||[code,code];return l(...pair);}
function archiveStatusLabel(code){return l(...({all:["全部","All"],...ARCHIVE_STATUSES}[code]||[code,code]));}
function mergeStatusLabel(code){return l(...({all:["全部","All"],...MERGE_STATUSES}[code]||[code,code]));}
function search(s){
  const scopes=s.search_scopes||[];
  const source=(s.search_index||[]).filter(record=>
    (!state.selectedWorkspace||record.workspaces?.includes(state.selectedWorkspace))&&
    (!state.selectedBranch||record.branches?.includes(state.selectedBranch)));
  const types=[["all",l("全部","All")],["task",l("任务","Tasks")],["exploration",l("探索","Explorations")],["stage",l("篇章","Chapters")],["catalog",l("资料","Resources")]];
  const result=searchRecords(source,{kind:state.selectedKind,archive:state.selectedArchiveStatus,merge:state.selectedMergeStatus,query:state.committedQuery,scopeSelected:Boolean(state.selectedWorkspace||state.selectedBranch)});
  state.selectedArchiveStatus=result.archive;state.selectedMergeStatus=result.merge;
  const counts=Object.fromEntries(types.map(([k])=>[k,k==="all"?result.scoped.length:result.scoped.filter(x=>x.kind===k).length])),page=paginate(result.items,state.searchPage,10);state.searchPage=page.page;
  const options=(items,active)=>items.map(([id,label])=>
    '<option value="'+esc(id)+'"'+(id===active?' selected':'')+'>'+esc(label)+'</option>').join("");
  const branches=[...new Set(scopes.map(item=>item.branch).concat((s.search_index||[]).flatMap(record=>record.branches||[])))].sort();
  const warnings=(s.search_warnings||[]).map(item=>'<li>'+esc(item.branch)+' · '+esc(item.message)+'</li>').join("");
  const hasCriteria=result.hasCriteria;
  return panel(l("跨记录搜索","Cross-record search"),`<div class="type-chips" role="group" aria-label="${l("记录类型","Record types")}">${types.map(([k,label])=>`<button data-search-kind="${k}" class="${state.selectedKind===k?"active":""}" aria-pressed="${state.selectedKind===k}">${label}<span>${counts[k]}</span></button>`).join("")}</div>
  <form id="search-form" class="search-controls"><label class="searchbox"><span>⌕</span><input id="search-input" value="${esc(state.draftQuery)}" placeholder="${l("搜索任务、篇章、证据或资料…","Search tasks, chapters, evidence, or resources…")}"></label><button class="primary-button" type="submit">${l("搜索","Search")}</button><button id="search-clear" type="button">${l("清除","Clear")}</button>
  <label class="disposition-control">${l("工作区","Workspace")}<select id="search-workspace">${options([["",l("全部工作区","All workspaces")],...scopes.map(item=>[item.workflow_id,item.label])],state.selectedWorkspace)}</select></label>
  <label class="disposition-control">${l("分支","Branch")}<select id="search-branch">${options([["",l("全部分支","All branches")],...branches.map(branch=>[branch,branch])],state.selectedBranch)}</select></label>
  <label class="disposition-control">${l("归档状态","Archive status")}<select id="search-archive-status">${result.archiveOptions.map(code=>`<option value="${esc(code)}" ${code===state.selectedArchiveStatus?"selected":""}>${esc(archiveStatusLabel(code))}</option>`).join("")}</select></label>
  <label class="disposition-control">${l("合并状态","Merge status")}<select id="search-merge-status">${result.mergeOptions.map(code=>`<option value="${esc(code)}" ${code===state.selectedMergeStatus?"selected":""}>${esc(mergeStatusLabel(code))}</option>`).join("")}</select></label></form>
  <p class="muted">${l("默认隐藏已归档记录；归档筛选控制历史记录，合并筛选独立显示 Git 合并结果。","Archived records are hidden by default. Archive and merge filters work independently.")}</p>
  ${warnings?`<ul class="notice warn">${warnings}</ul>`:""}
  ${hasCriteria?`<p class="count">${state.committedQuery?`“${esc(state.committedQuery)}” · `:""}${result.items.length} ${l("条结果","results")} · ${l(`第 ${page.page}/${page.pages} 页`,`Page ${page.page}/${page.pages}`)}</p>${table(page.items,[["kind_label","类型","Type"],["title","标题","Title"],["search_statuses","合并与归档状态","Merge and archive status"],["summary","摘要","Summary"],["occurred_at","记录时间","Recorded at"],["branch","分支","Branch"]],"search")}<div class="pagination"><button id="search-prev" ${page.page<=1?"disabled":""}>${l("上一页","Previous")}</button><button id="search-next" ${page.page>=page.pages?"disabled":""}>${l("下一页","Next")}</button></div>`:`<div class="empty">${l("输入关键词，或选择类型或状态浏览","Enter a keyword or select a type or status to browse")}</div>`}`,"INDEX · LOCAL");
}
function searchWorkspace(workspaceId, archived) {
  state.page="search";
  state.selectedWorkspace=workspaceId;
  state.selectedBranch="";
  state.selectedKind="all";
  state.selectedArchiveStatus=archived?"archived":"all";
  state.selectedMergeStatus="all";
  state.draftQuery=state.committedQuery="";
  state.searchPage=1;
  render();
}
function openSearchRecord(record) {
  const detail=record.detail||{}, title=record.title||record.record_id;
  const labels=[
    [l("记录编号","Record ID"),record.record_id],
    [l("来源分支","Source branch"),record.branch],
    [l("可查工作区","Available workspaces"),(record.sources||[]).map(source=>source.label).join(" · ")],
    [l("状态","Status"),detail.status||record.status],
    [l("说明","Summary"),detail.note||detail.summary||record.summary],
    [l("当前步骤","Current step"),detail.checkpoint?.current_step],
    [l("资料路径","Resource path"),detail.path],
    [l("篇章目标","Chapter goal"),detail.goal],
  ];
  const evidence=(Array.isArray(detail.evidence)?detail.evidence:[]).map(item=>'<li>'+esc(item)+'</li>').join("");
  const checks=detail.verification||[];
  const sheet=labels.filter(([,value])=>value).map(([label,value])=>'<dt>'+esc(label)+'</dt><dd>'+esc(value)+'</dd>').join("");
  const receipt=checks.length?'<h3>'+l("验证回执","Verification receipts")+'</h3>'+
    table(checks,[["suite","检查","Check"],["result","结果","Result"],["finished_at","完成时间","Finished at"],["tests","测试数","Tests"]]):"";
  $("#drawer-body").innerHTML='<h2 id="drawer-title">'+esc(title)+'</h2><dl class="details">'+sheet+'</dl>'+
    (evidence?'<h3>'+l("证据","Evidence")+'</h3><ul class="workspace-list">'+evidence+'</ul>':"")+receipt;
  $("#drawer").classList.remove("hidden");
  $("#drawer-close").focus();
}
function resources(s){return panel(l("目录与登记","Folders & registration"),renderResourceView(s,state,l),"READ ONLY",`<button id="search-catalog">${l("搜索全部资料","Search all materials")}</button>`);}
function explain(s){return panel(l("中英文状态解释","Status glossary"),`<p class="muted">${l("展开类别并选择英文状态，查看中文含义、实际影响和安全下一步。","Expand a category and select a status to review its meaning, impact, and safe next step.")}</p><div class="glossary">${(s.status_glossary||[]).map(group=>{const section=glossaryText(group.section,group.items[0]||{},state.language).section;return `<details data-glossary-section="${esc(group.section)}" ${state.glossaryOpen.has(group.section)?"open":""}><summary>${esc(section)} <span>${group.items.length}</span></summary>${group.items.map(item=>{const translated=glossaryText(group.section,item,state.language);return `<button class="glossary-item" data-glossary="${esc(group.section)}:${esc(item.code)}"><b>${esc(item.code)}</b><span>${esc(translated.meaning)}</span></button>`;}).join("")}</details>`;}).join("")}</div>`,"STATUS GLOSSARY");}
function explorationRows(items){return items.length?`<div class="stage-records">${items.map(x=>`<article><header><b>${esc(x.goal)}</b><span class="badge ${esc(x.state)}">${esc(x.state)}</span></header><small>${esc(x.track||l("探索","Exploration"))} · ${esc(x.branch)}</small><p>${esc(x.conclusion||l("尚无进展记录","No progress recorded"))}</p></article>`).join("")}</div>`:`<p class="muted">${l("本篇章没有关联探索。","This chapter has no associated explorations.")}</p>`;}
function materialRows(items){return items.length?`<div class="stage-records">${items.map(x=>`<article><header><b>${esc(x.kind_label)} · ${esc(x.title)}</b><span class="badge">${esc(x.status)}</span></header><small>${x.is_bundle?`${esc(x.file_count||0)} ${l("个文件","files")}${x.entrypoint?` · ${l("入口","entry")} ${esc(x.entrypoint)}`:""} · `:""}${esc(x.path||l("未登记路径","No registered path"))}</small></article>`).join("")}</div>`:`<p class="muted">${l("本篇章没有登记资料。","This chapter has no indexed resources.")}</p>`;}
function revisionRows(items){return items.length?`<div class="stage-records revision-timeline">${items.map(x=>`<article><header><b>${esc(x.revision)}</b><span class="badge">${esc(x.occurred_at)}</span></header><p>${esc(x.summary)}</p><small>${l("证据","Evidence")} · ${esc((x.evidence||[]).join(" · "))}</small></article>`).join("")}</div>`:`<p class="muted">${l("尚无显式判断修订。","No explicit judgment revisions.")}</p>`;}
function pauseSummary(x){if(x.status!=="paused")return "";const p=x.pause||{kind:"legacy",note:""},labels={interruption:l("暂时中断","Interruption"),"temporary-closure":l("暂时收束","Temporary closure"),legacy:l("历史暂停","Legacy pause")};return `<section class="pause-summary"><h3>${l("暂停状态","Pause status")}</h3><p><span class="badge ${esc(p.kind)}">${esc(labels[p.kind]||p.kind)}</span> ${esc(p.note||l("旧日志未记录暂停说明","No pause details in legacy log"))}</p><small>${esc(p.occurred_at||"")}</small></section>`;}
function stageView(s){
  const projection=s.research?.stages||{items:[],current:null,unassigned_materials:[],unassigned_explorations:[]};
  if(!state.stageExpansionInitialized){if(projection.current?.stage_id)state.stageOpen.add(projection.current.stage_id);state.stageExpansionInitialized=true;}
  if(!projection.items.length)return panel(l("研究篇章","Research Chapters"),`<div class="empty">${l("尚未建立研究篇章","No Research Chapters have been created")}</div>`,"GOAL · WORK · EXPLORATION · MATERIALS");
  const cards=projection.items.map(x=>`<details class="stage-card ${x.is_current?"current":""}" data-stage-id="${esc(x.stage_id)}" ${state.stageOpen.has(x.stage_id)?"open":""}><summary><span class="stage-heading"><b>${l("篇章","Chapter")} ${esc(x.sequence)} · ${esc(x.title)}</b><small>${esc(x.started_at)}${x.finished_at?` → ${esc(x.finished_at)}`:""}</small></span><span class="stage-counts"><em class="badge ${esc(x.status)}">${esc(x.status)}</em><em>${x.material_count} ${l("资料","resources")}</em><em>${x.exploration_count} ${l("探索","explorations")}</em></span></summary><div class="stage-body"><section><h3>${l("篇章目标","Chapter goal")}</h3><p>${esc(x.goal||l("未登记","Not recorded"))}</p></section><section><h3>${l("当前篇章概况","Current chapter summary")}</h3><p>${esc(x.work_summary||l("尚无篇章概况或当前进展","No chapter summary or current progress"))}</p></section>${pauseSummary(x)}<section><h3>${l("判断演化","Judgment evolution")}</h3>${revisionRows(x.revisions||[])}</section><section><h3>${l("探索","Explorations")}</h3>${explorationRows(x.explorations||[])}</section><section><h3>${l("产生资料","Produced resources")}</h3>${materialRows(x.materials||[])}</section></div></details>`).join("");
  const unassignedMaterials=projection.unassigned_materials||[],unassignedExplorations=projection.unassigned_explorations||[];
  const unassigned=`${unassignedMaterials.length?`<details class="stage-card unassigned"><summary><span class="stage-heading"><b>${l("未归属资料","Unassigned resources")}</b></span><span class="stage-counts"><em>${unassignedMaterials.length}</em></span></summary><div class="stage-body">${materialRows(unassignedMaterials)}</div></details>`:""}${unassignedExplorations.length?`<details class="stage-card unassigned"><summary><span class="stage-heading"><b>${l("未归属探索","Unassigned explorations")}</b></span><span class="stage-counts"><em>${unassignedExplorations.length}</em></span></summary><div class="stage-body">${explorationRows(unassignedExplorations)}</div></details>`:""}`;
  return panel(l("研究篇章","Research Chapters"),`<p class="muted">${l("篇章允许反复更新、多轮探索、暂停插入其他研究并在新证据出现时显式修订判断；暂时收束仍可恢复，completed 才是永久封存。","Chapters support iteration, multiple explorations, pauses for other research, and explicit evidence-driven revisions; temporary closure remains resumable, while completed is permanently sealed.")}</p><div class="stage-list">${cards}${unassigned}</div>`,"GOAL · REVISION · EXPLORATION · MATERIALS");
}
function disclosure(id,title,count){return `<details class="panel disclosure" data-lazy="${id}" ${state.advancedOpen.has(id)?"open":""}><summary><span><small>ADVANCED</small><b>${esc(title)}</b></span><em>${esc(count)}</em></summary><div class="lazy-body empty">${l("展开后读取","Expand to load")}</div></details>`;}
function advanced(s){const c=s.context||{},issues=s.diagnostics?.issues||s.diagnostics?.active||[],actions=s.action_matrix?.actions||[],sourceCount=Object.keys(c.field_sources||{}).length;return disclosure("verification",l("验收门禁","Verification gate"),esc(c.verification?.status||"not-applicable"))+disclosure("field-sources",l("字段来源","Field sources"),`${sourceCount} ${l("项","items")}`)+disclosure("runtime",l("版本与运行状态","Version and runtime"),l("按需","On demand"))+disclosure("actions",l("动作可用性","Action availability"),`${actions.length} ${l("项","items")}`)+disclosure("diagnostics",l("诊断","Diagnostics"),`${issues.length} ${l("个问题","issues")}`)+disclosure("events",l("原始事件","Raw events"),`${(s.events||[]).length} ${l("条","records")}`);}
function fillDisclosure(node,s,force=false){const id=node.dataset.lazy,body=node.querySelector(".lazy-body");if(body.dataset.loaded&&!force)return;body.dataset.loaded="1";body.classList.remove("empty");const c=s.context||{};if(id==="verification"){const v=c.verification||{};body.innerHTML=`<dl class="details"><dt>${l("验证档位","Profile")}</dt><dd>${esc(v.profile||"not-applicable")}</dd><dt>${l("当前指纹","Current fingerprint")}</dt><dd><code>${esc(v.fingerprint||"-")}</code></dd><dt>${l("要求套件","Required suites")}</dt><dd>${esc((v.required_suites||[]).join(", ")||l("仅内置仓库检查","Built-in repository check only"))}</dd><dt>${l("测试回执","Test receipts")}</dt><dd>${esc(v.status||"not-applicable")}</dd><dt>${l("待处理","Required actions")}</dt><dd>${esc((c.required_actions||[]).join("；")||l("无","None"))}</dd></dl>`;}if(id==="field-sources"){const rows=Object.entries(c.field_sources||{}).map(([field,source])=>`<dt>${esc(field)}</dt><dd>${esc(source?.event_type||"-")} · ${esc(source?.event_id||"-")} · task=${esc(source?.task_id||"-")} · stage=${esc(source?.stage_id||"-")}</dd>`).join("");body.innerHTML=`<dl class="details">${rows||`<dt>-</dt><dd>${l("暂无来源","No sources")}</dd>`}</dl>`;}if(id==="runtime")body.innerHTML=`<div class="button-row"><button id="check-updates" class="primary-button">${l("检查更新","Check for updates")}</button><button id="software-refresh">${l("刷新软件交付状态","Refresh software delivery")}</button></div><pre>${esc(JSON.stringify({branch:s.branch,health:s.health,classification:s.classification,active_task_warning:s.active_task_warning},null,2))}</pre>`;if(id==="actions")body.innerHTML=table(s.action_matrix?.actions||[],[["category","类别","Category"],["label","动作","Action"],["status","状态","Status"],["missing_fields","等待输入","Waiting for input"]]);if(id==="diagnostics"){const d=s.diagnostics||{},issues=d.issues||d.active||[];body.innerHTML=`<div class="button-row"><button id="export-diagnostics" class="primary-button">${l("导出脱敏 ZIP","Export redacted ZIP")}</button><button id="report-bug">${l("报告 Bug","Report bug")}</button></div>${table(issues,[["occurred_at","时间","Time"],["code","代码","Code"],["summary","摘要","Summary"]],"diagnostic")}`;}if(id==="events"){const page=paginate([...(s.events||[])].reverse(),state.eventPage,50);state.eventPage=page.page;body.innerHTML=`<p class="count">${l(`第 ${page.page}/${page.pages} 页`,`Page ${page.page}/${page.pages}`)}</p>${table(page.items,[["occurred_at","时间","Time"],["event_type","类型","Type"],["task_id","任务","Task"],["branch","分支","Branch"]],"event")}<div class="pagination"><button id="event-prev" ${page.page<=1?"disabled":""}>${l("上一页","Previous")}</button><button id="event-next" ${page.page>=page.pages?"disabled":""}>${l("下一页","Next")}</button></div>`;}bindDynamic(s);}
const advancedDependencies={verification:new Set(["context"]),"field-sources":new Set(["context"]),runtime:new Set(["branch","health","classification","active_task_warning","software_delivery"]),actions:new Set(["action_matrix"]),diagnostics:new Set(["diagnostics"]),events:new Set(["events"])};
function updateAdvanced(s,changed=[],force=false){const c=s.context||{},issues=s.diagnostics?.issues||s.diagnostics?.active||[],counts={verification:c.verification?.status||"not-applicable","field-sources":`${Object.keys(c.field_sources||{}).length} ${l("项","items")}`,runtime:l("按需","On demand"),actions:`${(s.action_matrix?.actions||[]).length} ${l("项","items")}`,diagnostics:`${issues.length} ${l("个问题","issues")}`,events:`${(s.events||[]).length} ${l("条","records")}`},scrollY=window.scrollY;document.querySelectorAll("details[data-lazy]").forEach(node=>{node.querySelector("summary em").textContent=counts[node.dataset.lazy];const affected=force||changed.some(key=>advancedDependencies[node.dataset.lazy]?.has(key));if(node.open&&affected)fillDisclosure(node,s,true);});requestAnimationFrame(()=>window.scrollTo(0,scrollY));}
function languageSettings(){return panel(l("界面语言","Interface language"),`<p class="muted">${l("语言选择只保存在本机，不会写入项目文件、事件日志或数据库。","The language preference is stored only on this device and never written to project files, events, or the database.")}</p><div class="language-options" role="radiogroup" aria-label="${l("界面语言","Interface language")}"><label><input type="radio" name="language" value="zh-CN" ${state.language==="zh-CN"?"checked":""}>简体中文</label><label><input type="radio" name="language" value="en" ${state.language==="en"?"checked":""}>English</label></div>`,"LOCAL PREFERENCE");}
function productLink(key,label){return `<a class="about-link" href="${productLinks[key]}" target="_blank" rel="noopener noreferrer">${esc(label)} <span aria-hidden="true">↗</span></a>`;}
function aboutSettings(){const identity=state.runtime.build_identity||{},commit=String(identity.source_commit||"").slice(0,12)||l("未知","Unknown"),version=state.runtime.display_version||identity.display_version||state.runtime.version||l("未知","Unknown"),build=identity.build_id||l("未知","Unknown");return panel(l("关于 Workflow Monitor","About Workflow Monitor"),`<div class="about-hero"><img src="workflow-monitor-mark.svg" alt=""><div><h3>Workflow Monitor</h3><p>${l("面向 Windows 科研 Git 项目的本地优先工作流工具。","A local-first workflow tool for research Git projects on Windows.")}</p></div></div><div class="about-links">${productLink("repository",l("打开 GitHub 仓库","Open GitHub repository"))}${productLink("manual",l("查看用户手册","Read the user manual"))}${productLink("license",l("查看 MIT License","View MIT License"))}${productLink("security",l("私密报告安全漏洞","Privately report a vulnerability"))}<button id="about-report-bug">${l("报告问题","Report an issue")}</button></div><div class="button-row"><button id="show-build-notes">${l("查看测试版说明","View test build notes")}</button></div><dl class="details about-details"><dt>${l("作者","Author")}</dt><dd>DawnDust</dd><dt>${l("版本","Version")}</dt><dd><code>v${esc(version)}</code></dd><dt>${l("构建提交","Build commit")}</dt><dd><code>${esc(commit)}</code></dd><dt>${l("构建标识","Build ID")}</dt><dd><code>${esc(build)}</code></dd><dt>${l("许可证","License")}</dt><dd>MIT</dd><dt>${l("项目仓库","Repository")}</dt><dd><code>${esc(productLinks.repository)}</code></dd></dl><p class="muted">${l("外部链接仅在你点击后打开；程序不会自动上传项目内容或诊断数据。","External links open only after you click them. The application never uploads project content or diagnostic data automatically.")}</p>`,"OPEN SOURCE · LOCAL FIRST");}
function settings(s){const sections=[["language",l("语言","Language")],["explain",l("解释","Glossary")],["advanced",l("高级查看","Advanced")],["about",l("关于","About")]],content=state.settingsSection==="language"?languageSettings():state.settingsSection==="explain"?explain(s):state.settingsSection==="advanced"?advanced(s):aboutSettings();return `<div class="settings-layout"><nav class="settings-nav" aria-label="${l("设置分类","Settings sections")}">${sections.map(([id,label])=>`<button data-settings-section="${id}" class="${state.settingsSection===id?"active":""}" aria-current="${state.settingsSection===id?"page":"false"}">${label}</button>`).join("")}</nav><section>${content}</section></div>`;}
function render(){
  renderNavigation();
  const resourceFocus=document.activeElement?.dataset.resourceFocus;
  rememberResourceNavigation();
  state.records.clear();
  const scope=viewName(), previous=snapshots.get(scope);
  const s=state.snapshot=previous?.snapshot||null,p=pages.find(x=>x[0]===state.page);
  $("#page-title").textContent=state.page==="settings"?l("设置","Settings"):(p?pageLabel(p):l("概览","Overview"));
  if(state.page!=="workspaces")workspaces.leave();
  if(!s){
    $("#status-strip").className="status-strip loading";
    $("#status-strip").textContent=l("正在读取快照…","Loading snapshot…");
    const error=viewErrors.get(scope);
    $("#content").innerHTML='<div class="loading-card">'+(error?esc(error):'<span></span>'+
      l("正在准备本地只读快照…","Preparing the local read-only snapshot…"))+'</div>';
    if(connected&&!requests.has(scope)&&!error)void refresh();
    return;
  }
  statusStrip(s);
  if(state.page==="workspaces"){
    workspaces.show(s.project_monitor);
    return;
  }
  const views={workflow,overview,search,resources,"research-stages":stageView,settings};
  $("#content").innerHTML=views[state.page](s);
  bindDynamic(s);
  if(resourceFocus)restoreResourceFocus(resourceFocus);
}
function rememberResourceNavigation(){
  const navigation=$(".resource-navigation");if(navigation)state.resourceNavOpen=navigation.open;
  document.querySelectorAll("details[data-folder-path]").forEach(n=>{if(n.open)state.folderOpen.add(n.dataset.folderPath);else state.folderOpen.delete(n.dataset.folderPath);});
}
function restoreResourceFocus(key){const node=[...document.querySelectorAll("[data-resource-focus]")].find(n=>n.dataset.resourceFocus===key)||[...document.querySelectorAll(".folder-select")].find(n=>n.dataset.resourcePath===state.selectedResourcePath);node?.focus();}
function selectResource(path){state.selectedResourcePath=path;state.resourcePage=1;render();document.querySelectorAll("details[data-folder-path]").forEach(n=>{if(path.startsWith(n.dataset.folderPath+"/")){n.open=true;state.folderOpen.add(n.dataset.folderPath);}});restoreResourceFocus("tree:"+path);}
function openResourceDetail(path){const entry=state.records.get("resource:"+path);if(!entry)return;state.drawerReturnFocus=document.activeElement?.dataset.resourceFocus;$("#drawer-body").innerHTML=resourceDetails(entry,state.snapshot,l);$("#drawer").classList.remove("hidden");$("#drawer-close").focus();}
function closeDrawer(){$("#drawer").classList.add("hidden");if(state.drawerReturnFocus)restoreResourceFocus(state.drawerReturnFocus);state.drawerReturnFocus=null;if(state.drawerReturnSearch){
  const key=state.drawerReturnSearch;
  [...document.querySelectorAll("tr[data-record-key]")].find(row=>row.dataset.recordKey===key)?.focus();
  state.drawerReturnSearch=null;
}}
function openDrawer(title,value){$("#drawer-body").innerHTML=`<small>DETAIL</small><h2 id="drawer-title">${esc(title)}</h2><pre>${esc(JSON.stringify(value,null,2))}</pre>`;$("#drawer").classList.remove("hidden");}
function bindDynamic(s){
  document.querySelectorAll("[data-attention-target]").forEach(n=>n.onclick=()=>{state.page=n.dataset.attentionTarget;renderNavigation();render();});
  $(".review-pending")?.addEventListener("toggle",e=>{state.reviewOpen=e.target.open;});
  $(".review-help")?.addEventListener("toggle",e=>{state.reviewHelpOpen=e.target.open;});
  const input=$("#search-input");
  input?.addEventListener("compositionstart",()=>state.searchComposing=true);
  input?.addEventListener("compositionend",e=>{state.searchComposing=false;state.draftQuery=e.target.value;if(state.pendingSearchRender){state.pendingSearchRender=false;render();}});
  input?.addEventListener("input",e=>state.draftQuery=e.target.value);
  input?.addEventListener("blur",()=>{if(state.pendingSearchRender&&!state.searchComposing){state.pendingSearchRender=false;render();}});
  $("#search-form")?.addEventListener("submit",e=>{e.preventDefault();state.draftQuery=input?.value||state.draftQuery;state.committedQuery=state.draftQuery.trim();state.searchPage=1;state.pendingSearchRender=false;render();});
  $("#search-clear")?.addEventListener("click",()=>{state.draftQuery="";state.committedQuery="";state.selectedKind="all";state.selectedWorkspace=state.selectedBranch="";state.selectedArchiveStatus="unarchived";state.selectedMergeStatus="all";state.searchPage=1;render();});
  document.querySelectorAll("[data-search-kind]").forEach(n=>n.onclick=()=>{state.selectedKind=n.dataset.searchKind;state.searchPage=1;render();});
  $("#search-workspace")?.addEventListener("change",e=>{state.selectedWorkspace=e.target.value;state.searchPage=1;render();$("#search-workspace")?.focus({preventScroll:true});});
  $("#search-branch")?.addEventListener("change",e=>{state.selectedBranch=e.target.value;state.searchPage=1;render();$("#search-branch")?.focus({preventScroll:true});});
  $("#search-archive-status")?.addEventListener("change",e=>{state.selectedArchiveStatus=e.target.value;state.searchPage=1;render();});
  $("#search-merge-status")?.addEventListener("change",e=>{state.selectedMergeStatus=e.target.value;state.searchPage=1;render();});
  $("#search-prev")?.addEventListener("click",()=>{state.searchPage--;render();});$("#search-next")?.addEventListener("click",()=>{state.searchPage++;render();});
  const navigation=$(".resource-navigation");if(navigation){navigation.open=state.resourceNavOpen;navigation.addEventListener("toggle",()=>{if(navigation.isConnected)state.resourceNavOpen=navigation.open;});}
  document.querySelectorAll("[data-resource-path]").forEach(n=>n.onclick=e=>{e.preventDefault();e.stopPropagation();selectResource(n.dataset.resourcePath);});
  document.querySelectorAll("[data-resource-detail]").forEach(n=>n.onclick=e=>{e.stopPropagation();openResourceDetail(n.dataset.resourceDetail);});
  document.querySelectorAll("tr[data-resource-entry]").forEach(row=>{const open=()=>{const entry=state.records.get("resource:"+row.dataset.resourceEntry);if(entry?.entry_type==="directory")selectResource(entry.path);else openResourceDetail(row.dataset.resourceEntry);};row.onclick=open;row.onkeydown=e=>{if(e.target===row&&(e.key==="Enter"||e.key===" ")){e.preventDefault();open();}};});
  document.querySelectorAll("details[data-folder-path]").forEach(n=>n.addEventListener("toggle",()=>{if(!n.isConnected)return;if(n.open)state.folderOpen.add(n.dataset.folderPath);else state.folderOpen.delete(n.dataset.folderPath);}));
  document.querySelectorAll("[data-open-resource]").forEach(n=>n.onclick=async e=>{e.stopPropagation();const r=await invoke("open_resource_directory",n.dataset.openResource);notice(r.ok?l(`已打开 ${r.data.path}`,`Opened ${r.data.path}`):r.error.message,r.ok?"info":"error")});
  $("#open-physics-research-roadmap")?.addEventListener("click",async()=>{const r=await invoke("open_physics_research_roadmap");notice(r.ok?l("已在默认应用中打开理论物理研究路线","Opened the physics research roadmap in the default app"):r.error.message,r.ok?"info":"error")});
  $("#open-understanding-guide")?.addEventListener("click",async()=>{const r=await invoke("open_research_understanding_guide");notice(r.ok?l("已打开理解指南","Opened the understanding guide"):r.error.message,r.ok?"info":"error")});
  $("#show-build-notes")?.addEventListener("click",async()=>{const r=await invoke("get_test_build_notes");if(!r.ok){notice(r.error.message,"error");return;}$("#drawer-body").innerHTML=`<small>TEST BUILD</small><h2 id="drawer-title">${l("测试版说明","Test build notes")}</h2><pre>${esc(r.data.markdown)}</pre>`;$("#drawer").classList.remove("hidden");});
  $("#search-catalog")?.addEventListener("click",()=>{state.page="search";state.selectedKind="catalog";state.selectedWorkspace=state.selectedBranch="";state.selectedArchiveStatus="unarchived";state.selectedMergeStatus="all";state.draftQuery="";state.committedQuery="";state.searchPage=1;renderNavigation();render();});
  $("#resource-prev")?.addEventListener("click",()=>{state.resourcePage--;render();});$("#resource-next")?.addEventListener("click",()=>{state.resourcePage++;render();});
  document.querySelectorAll("tr[data-record-key]").forEach(row=>{const open=async()=>{const record=state.records.get(row.dataset.recordKey);if(row.dataset.kind==="search"){state.drawerReturnSearch=row.dataset.recordKey;openSearchRecord(record);return;}if(row.dataset.kind==="catalog"){const r=await invoke("reveal_resource_file",record.item_id);notice(r.ok?l(`已定位 ${r.data.path}`,`Revealed ${r.data.path}`):r.error.message,r.ok?"info":"error");}else openDrawer(l("记录详情","Record details"),record)};row.onclick=open;row.onkeydown=e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();open();}}});
  document.querySelectorAll("details[data-glossary-section]").forEach(n=>n.addEventListener("toggle",()=>{if(n.open)state.glossaryOpen.add(n.dataset.glossarySection);else state.glossaryOpen.delete(n.dataset.glossarySection);}));
  document.querySelectorAll("[data-settings-section]").forEach(n=>n.onclick=()=>{state.settingsSection=n.dataset.settingsSection;render();});
  document.querySelectorAll('input[name="language"]').forEach(n=>n.onchange=()=>{state.language=saveLanguage(n.value);applyChromeLanguage();renderNavigation();if(state.snapshot)statusStrip(state.snapshot);render();});
  $("#about-report-bug")?.addEventListener("click",async()=>{const r=await invoke("report_bug");notice(r.ok&&r.data.opened?l("已打开问题反馈页面","Opened the issue report page"):l("无法打开问题反馈页面","Could not open the issue report page"),r.ok?"info":"error")});
  document.querySelectorAll(".glossary-item").forEach(btn=>btn.onclick=()=>{const [section,code]=btn.dataset.glossary.split(":");const item=(s.status_glossary||[]).find(x=>x.section===section)?.items.find(x=>x.code===code),translated=glossaryText(section,item,state.language);openDrawer(`${code}｜${translated.meaning}`,state.language==="en"?{Category:translated.section,Status:code,Meaning:translated.meaning,Impact:translated.implication,"Safe next step":translated.nextStep}:{类别:section,英文状态:code,中文含义:item.meaning,意味着什么:item.implication,安全下一步:item.next_step});});
  document.querySelectorAll("details[data-stage-id]").forEach(n=>n.addEventListener("toggle",()=>{if(n.open)state.stageOpen.add(n.dataset.stageId);else state.stageOpen.delete(n.dataset.stageId);}));
  document.querySelectorAll("details[data-lazy]").forEach(n=>{if(!n.dataset.bound){n.dataset.bound="1";n.addEventListener("toggle",()=>{if(n.open){state.advancedOpen.add(n.dataset.lazy);fillDisclosure(n,state.snapshot);}else state.advancedOpen.delete(n.dataset.lazy);});}if(n.open)fillDisclosure(n,s);});
  $("#export-diagnostics")?.addEventListener("click",async()=>{const r=await invoke("export_diagnostics",null);notice(r.ok?l(`诊断包已保存：${r.data.output}`,`Diagnostics bundle saved: ${r.data.output}`):r.error.message,r.ok?"info":"error")});$("#report-bug")?.addEventListener("click",async()=>{const r=await invoke("report_bug");notice(r.ok&&r.data.opened?l("已打开 Bug 报告页面","Opened the bug report page"):l("无法打开 Bug 报告页面","Could not open the bug report page"),r.ok?"info":"error")});$("#check-updates")?.addEventListener("click",async()=>openDrawer(l("更新检查","Update check"),await invoke("check_updates")));$("#software-refresh")?.addEventListener("click",async()=>openDrawer(l("软件交付状态","Software delivery status"),await invoke("refresh_software_delivery",{})));$("#event-prev")?.addEventListener("click",()=>{state.eventPage--;const n=document.querySelector('[data-lazy="events"]');n.querySelector(".lazy-body").dataset.loaded="";fillDisclosure(n,s)});$("#event-next")?.addEventListener("click",()=>{state.eventPage++;const n=document.querySelector('[data-lazy="events"]');n.querySelector(".lazy-body").dataset.loaded="";fillDisclosure(n,s)});
}
async function refresh(force=false) {
  if(!connected||requests.has(viewName()))return;
  const view=viewName(), previous=snapshots.get(view), button=$("#refresh-button");
  requests.set(view,true);
  const returnFocus=document.activeElement===button;
  button.disabled=true;
  try {
    const reply=bridge().refresh_view
      ? await invoke("refresh_view",view,previous?.snapshot_revision||null,force)
      : await invoke("refresh",previous?.snapshot_revision||null,force);
    const data=reply.data||{};
    if(data.snapshot) snapshots.set(view,{snapshot:data.snapshot,snapshot_revision:data.state_token,time:data.updated_at});
    else if(previous&&data.updated_at) previous.time=data.updated_at;
    if(view!==viewName())return;
    state.snapshotToken=data.state_token||previous?.snapshot_revision;
    if(!reply.ok){
      const last=data.updated_at||previous?.time;
      const message=l("刷新失败","Refresh failed")+" · "+reply.error.message+
        (last?" · "+l("上次更新","Last updated")+" "+new Date(last).toLocaleString(state.language):"");
      viewErrors.set(view,message);
      notice(message,"error");
      if(!previous)render();
      return;
    }
    viewErrors.delete(view);
    if(previous&&!force&&!data.changed_sections?.length)return;
    if(view==="search"&&(state.searchComposing||document.activeElement?.id==="search-input")){
      state.pendingSearchRender=true;
      return;
    }
    render();
  } catch(error) {
    if(view===viewName()){
      const last=previous?.time;
      const message=error.message+(last?" · "+l("上次更新","Last updated")+" "+new Date(last).toLocaleString(state.language):"");
      viewErrors.set(view,message);
      notice(message,"error");
      if(!previous)render();
    }
  } finally {
    requests.delete(view);
    button.disabled=requests.has(viewName());
    if(returnFocus&&view===viewName()&&document.activeElement===document.body)button.focus({preventScroll:true});
    const next=viewName();
    if(next!==view&&!snapshots.has(next)&&!viewErrors.has(next))void refresh();
  }
}
function initialize(){const requested=new URLSearchParams(location.hash.slice(1)).get("theme"),preferred=matchMedia("(prefers-color-scheme: light)").matches?"light":"dark";$("#app").dataset.theme=["light","dark"].includes(requested)?requested:preferred;applyChromeLanguage();renderNavigation();render();$("#settings-button").onclick=()=>{state.page="settings";renderNavigation();render();};$("#refresh-button").onclick=()=>refresh(true);$("#theme-toggle").onclick=()=>{const a=$("#app");a.dataset.theme=a.dataset.theme==="dark"?"light":"dark"};$("#drawer-close").onclick=closeDrawer;document.addEventListener("keydown",e=>{if(e.key==="Escape")closeDrawer()});window.addEventListener("pywebviewready",async()=>{const ready=await invoke("ready");state.runtime=ready.data||state.runtime;connected=true;$("#runtime-version").textContent=`v${ready.data.version} · bridge ready`;await refresh(true);if(ready.data.refresh_seconds>0)setInterval(()=>refresh(false),ready.data.refresh_seconds*1000);});}
const workflowBase=workflow;
workflow=function(s){const html=workflowBase(s),step=s.action_matrix?.state?.lifecycle_step||{},labels=state.language==="en"?["Not started","Cycle started","Working","Progress recorded","Verification or review required","Gates passed","Finishing","Completed"]:["未开始","周期已建立","工作中","进展已记录","待验证或审阅","门禁已通过","正在收尾","已完成"],steps=labels.map((x,i)=>`<span class="${i===step.index?"current":""}">${x}</span>`).join("<i>→</i>"),warning=step.warning?`<div class="notice warn lifecycle-warning"><b>${l("进展记录需要更新","Progress checkpoint needs updating")}</b><br>${l("Checkpoint 已过期，请重新记录进展。","Checkpoint is stale. Record progress again.")} <code>${esc(step.warning.next_action||"state update")}</code></div>`:"";return html.replace(/<div class="lifecycle-steps">.*?<\/div>/,`<div class="lifecycle-steps">${steps}</div>${warning}`);};
initialize();
