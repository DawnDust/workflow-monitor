import { escapeHtml as esc, paginate } from "./state.js";

const englishKinds = {literature:"Literature",data:"Data",theory:"Theory",simulation:"Analysis & programs",output:"Output",other:"Other",report:"Report",spark:"Spark",tutorial:"Tutorial",translation:"Translation",plan:"Plan"};
const englishPurposes = {literature:"Original literature and source evidence",data:"Raw and processed data",theory:"Hypotheses, definitions and derivations",simulation:"Analysis code, notebooks and experiments",output:"Program outputs",other:"Materials awaiting classification",report:"Project reports",spark:"Ideas and questions",tutorial:"Literature tutorials and learning materials",translation:"Literature translations",plan:"Next actions from Sparks"};
export const resourceName = (folder,language) => language==="en"&&folder.registration==="preset"?(folder.path==="resources"?"Resources":englishKinds[folder.kind]||folder.label):folder.label||folder.name;
const purpose = (folder,language) => language==="en"&&folder.registration==="preset"?(folder.path==="resources"?"Place materials by folder purpose and keep their registration current":englishPurposes[folder.kind]||folder.description):folder.description||"";
const timestamp = (value,language) => value?new Date(value).toLocaleString(language==="en"?"en-GB":"zh-CN",{timeZone:"Asia/Shanghai",year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit"}):"—";

export function resourceIndex(snapshot){
  if(snapshot.resource_index)return snapshot.resource_index;
  const folders=[{path:"resources",label:"资料",registration:"preset",exists:true,status:"ok",kind:"other",description:"按用途存放资料"},...(snapshot.resource_directories||[]).map(d=>({...d,registration:d.registration||"preset",exists:d.exists??d.status!=="missing"}))];
  const entries={};
  folders.forEach(d=>{entries[d.path]=[];if(d.path!=="resources")(entries[d.path.slice(0,d.path.lastIndexOf("/"))]??=[]).push({...d,entry_type:"directory",name:d.label});});
  (snapshot.catalog_items||[]).filter(i=>i.path&&i.status!=="archived").forEach(i=>(entries[i.path.slice(0,i.path.lastIndexOf("/"))]??=[]).push({...i,name:i.path.split("/").pop(),entry_type:"file",registration:"registered",exists:i.status!=="missing"}));
  return {folders,entries};
}

export function renderResourceView(snapshot,state,l){
  const index=resourceIndex(snapshot),dirs=index.folders;
  if(!state.selectedResourcePath)state.selectedResourcePath="resources";
  if(!dirs.some(d=>d.path===state.selectedResourcePath)&&!Object.hasOwn(index.entries,state.selectedResourcePath))state.selectedResourcePath="resources";
  const selected=dirs.find(d=>d.path===state.selectedResourcePath)||{path:state.selectedResourcePath,label:state.selectedResourcePath.split("/").pop(),registration:"bundle-content",exists:true,description:l("资料包内目录","Folder inside a material bundle")};
  const badge=e=>e.status==="missing"||e.exists===false?`<span class="badge warn">${l("缺失","Missing")}</span>`:e.registration==="unregistered"?`<span class="badge warn">${l("待登记","Unregistered")}</span>`:"";
  const tree=parent=>dirs.filter(d=>{const ancestors=dirs.filter(a=>d.path.startsWith(a.path+"/")).sort((a,b)=>b.path.length-a.path.length);return (ancestors[0]?.path||null)===parent;}).map(d=>{
    const children=dirs.some(c=>c.path.startsWith(d.path+"/")),count=d.indexed_items||0;
    const row=`<button class="folder-select ${d.path===state.selectedResourcePath?"active":""}" data-resource-path="${esc(d.path)}" data-resource-focus="tree:${esc(d.path)}" aria-pressed="${d.path===state.selectedResourcePath}"><span>▱ ${esc(resourceName(d,state.language))}</span>${count?`<small>${count}</small>`:""}${badge(d)}</button>`;
    return children?`<details class="folder-group" data-folder-path="${esc(d.path)}" ${state.folderOpen.has(d.path)?"open":""}><summary data-resource-focus="expand:${esc(d.path)}">${row}</summary><div class="folder-children">${tree(d.path)}</div></details>`:row;
  }).join("");
  const values=index.entries[selected.path]||[],page=paginate(values,state.resourcePage,15);state.resourcePage=page.page;
  const parts=selected.path.split("/"),breadcrumbs=parts.map((name,i)=>{const path=parts.slice(0,i+1).join("/"),directory=dirs.find(d=>d.path===path);return `<button data-resource-path="${esc(path)}" data-resource-focus="crumb:${esc(path)}">${esc(directory?resourceName(directory,state.language):name)}</button>`;}).join('<span aria-hidden="true">/</span>');
  const registered=values.filter(e=>e.entry_type==="file"&&e.registration==="registered"&&e.exists).length,unregistered=values.filter(e=>e.entry_type==="file"&&e.registration==="unregistered"&&e.exists).length,missing=values.filter(e=>e.exists===false).length;
  const counts=[registered?`${registered} ${l("已登记","registered")}`:"",unregistered?`${unregistered} ${l("待登记","unregistered")}`:"",missing?`${missing} ${l("缺失","missing")}`:""].filter(Boolean).join(" · ");
  const rows=page.items.map(e=>{state.records.set("resource:"+e.path,e);const dir=e.entry_type==="directory",kind=dir?l(e.item_id?"资料包":"文件夹",e.item_id?"Bundle":"Folder"):state.language==="en"?(englishKinds[e.kind]||e.kind):e.kind_label||e.kind;return `<tr tabindex="0" data-resource-focus="row:${esc(e.path)}" data-resource-entry="${esc(e.path)}"><td><button class="resource-entry-name" ${dir?`data-resource-path="${esc(e.path)}"`:`data-resource-detail="${esc(e.path)}"`} data-resource-focus="entry:${esc(e.path)}">${dir?"▱":"▤"} ${esc(dir?resourceName(e,state.language):e.name)}</button>${badge(e)}${e.registration==="bundle-content"?`<small class="muted">${l("包内内容","Bundle content")}</small>`:""}</td><td>${esc(kind)}</td><td>${esc(timestamp(e.modified_at,state.language))}</td></tr>`;}).join("");
  const canOpen=selected.exists&&["preset","registered","bundle"].includes(selected.registration);
  return `<div class="resource-layout"><details class="resource-navigation" open><summary>${l("目录用途索引","Folder guide")}</summary><nav class="folder-list" aria-label="${l("资料目录","Resource folders")}">${tree(null)}</nav></details><section class="resource-content"><header class="resource-heading"><div><nav class="resource-breadcrumbs" aria-label="${l("目录位置","Folder location")}">${breadcrumbs}</nav><h3>${esc(resourceName(selected,state.language))}</h3><p>${esc(purpose(selected,state.language))}</p><code>${esc(selected.path)}</code></div><button data-open-resource="${esc(selected.path)}" ${canOpen?"":"disabled"} title="${canOpen?"":l("请先登记目录或核对缺失位置","Register the folder or reconcile its missing path first")}">${l("打开目录","Open folder")}</button></header>${counts?`<p class="count">${counts}</p>`:""}${values.length?`<div class="table-wrap"><table><thead><tr><th>${l("名称","Name")}</th><th>${l("类型","Type")}</th><th>${l("修改时间","Modified")}</th></tr></thead><tbody>${rows}</tbody></table></div>`:`<div class="empty">${l("当前目录暂无资料。按上方用途存放文件，再让 AI 登记。","This folder is empty. Place files according to its purpose, then ask the AI to register them.")}</div>`}${page.pages>1?`<div class="pagination"><button id="resource-prev" ${page.page<=1?"disabled":""}>${l("上一页","Previous")}</button><span>${page.page}/${page.pages}</span><button id="resource-next" ${page.page>=page.pages?"disabled":""}>${l("下一页","Next")}</button></div>`:""}</section></div>`;
}

export function resourceDetails(entry,snapshot,l){
  const labels={registered:l("已登记","Registered"),unregistered:l("待登记","Unregistered"),"bundle-content":l("资料包整体登记","Managed by its bundle")};
  const fields=[[l("路径","Path"),entry.path],[l("登记","Registration"),entry.exists===false?l("已登记资料缺失，请先核对移动位置","Registered material is missing; reconcile its location first"):labels[entry.registration]||entry.registration],[l("登记标题","Registered title"),entry.title],[l("大小","Size"),entry.size==null?null:`${entry.size.toLocaleString()} bytes`],[l("标签","Tags"),(entry.tags||[]).join(" · ")],[l("来源","Source"),entry.source],[l("摘要","Summary"),entry.summary]];
  const names=new Map((snapshot.catalog_items||[]).map(i=>[i.item_id,i.title]));
  const relations=(snapshot.catalog_relations||[]).filter(r=>r.source_id===entry.item_id||r.target_id===entry.item_id).map(r=>`${names.get(r.source_id)||r.source_id} · ${r.relation_type} → ${names.get(r.target_id)||r.target_id}`);
  if(relations.length)fields.push([l("关联","Relationships"),relations.join("\n")]);
  return `<h2 id="drawer-title">${esc(entry.name)}</h2><dl class="details resource-details">${fields.filter(([,v])=>v!=null&&v!=="").map(([k,v])=>`<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>`;
}
