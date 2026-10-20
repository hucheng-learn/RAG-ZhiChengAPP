'use strict';
const $ = selector => document.querySelector(selector);
// 一言独立加载，外部服务不可用时保留默认文案，不阻塞助教初始化。
async function loadHitokoto(){
  const controller=new AbortController();
  const timeout=setTimeout(()=>controller.abort(),5000);
  try{
    const response=await fetch('https://v1.hitokoto.cn/?c=d',{signal:controller.signal});
    if(!response.ok)return;
    const data=await response.json();
    if(data.type!=='d'||typeof data.hitokoto!=='string'||!data.hitokoto.trim())return;
    const author=typeof data.from_who==='string'?data.from_who.trim():'';
    const source=typeof data.from==='string'?data.from.trim():'';
    $('#hitokoto-text').textContent=data.hitokoto.trim();
    $('#hitokoto-source').textContent=[author,source?`《${source}》`:''].filter(Boolean).join(' · ').replace(/^/,author||source?'—— ':'')||'文学一言';
    if(typeof data.uuid==='string'&&data.uuid.trim())$('#hitokoto-link').href='https://hitokoto.cn/?uuid='+encodeURIComponent(data.uuid);
  }catch{
    // 网络错误或超时均使用 HTML 中的默认文案。
  }finally{clearTimeout(timeout);}
}
loadHitokoto();
setInterval(loadHitokoto,60_000);
// 分隔条支持鼠标、触屏与键盘；存储不可用时仍可正常调整。
function initSidebarResize(){
  const sidebar=$('#sidebar'),handle=$('#sidebar-resizer');
  const desktop=window.matchMedia('(min-width:801px)');
  const storageKey='zhichengapp.sidebarWidth';
  let preferredWidth=280,drag=null;
  try{const saved=Number(localStorage.getItem(storageKey));if(Number.isFinite(saved)&&saved>=220)preferredWidth=saved;}catch{}
  function applyWidth(width){
    const maximum=Math.max(220,Math.min(480,window.innerWidth-480));
    const actual=Math.round(Math.max(220,Math.min(maximum,width)));
    document.body.style.setProperty('--sidebar-width',actual+'px');
    handle.setAttribute('aria-valuemax',String(maximum));
    handle.setAttribute('aria-valuenow',String(actual));
    return actual;
  }
  function save(){try{localStorage.setItem(storageKey,String(preferredWidth));}catch{}}
  function finish(){
    if(!drag)return;
    const pointerId=drag.pointerId;
    drag=null;
    document.body.classList.remove('sidebar-resizing');
    if(handle.hasPointerCapture(pointerId))handle.releasePointerCapture(pointerId);
    save();
  }
  handle.addEventListener('pointerdown',event=>{
    if(!desktop.matches||document.body.classList.contains('sidebar-collapsed')||!event.isPrimary||event.button!==0)return;
    event.preventDefault();
    drag={pointerId:event.pointerId,startX:event.clientX,startWidth:sidebar.getBoundingClientRect().width};
    handle.setPointerCapture(event.pointerId);
    handle.focus();
    document.body.classList.add('sidebar-resizing');
  });
  handle.addEventListener('pointermove',event=>{
    if(!drag||drag.pointerId!==event.pointerId)return;
    preferredWidth=applyWidth(drag.startWidth+event.clientX-drag.startX);
  });
  for(const event of ['pointerup','pointercancel','lostpointercapture'])handle.addEventListener(event,finish);
  window.addEventListener('blur',finish);
  handle.addEventListener('keydown',event=>{
    if(!desktop.matches)return;
    const current=Number(handle.getAttribute('aria-valuenow'));
    const step=event.shiftKey?40:10;
    const widths={ArrowLeft:current-step,ArrowRight:current+step,Home:220,End:Math.min(480,window.innerWidth-480)};
    if(!(event.key in widths))return;
    event.preventDefault();preferredWidth=applyWidth(widths[event.key]);save();
  });
  handle.addEventListener('dblclick',()=>{preferredWidth=280;applyWidth(preferredWidth);save();});
  window.addEventListener('resize',()=>{finish();applyWidth(preferredWidth);});
  applyWidth(preferredWidth);
}
initSidebarResize();
let speechInput = null;
const state = {session:null, courses:[], series:[], sessions:[], projectId:null, controller:null, busy:false, historyBusy:false};
function setSidebarCollapsed(collapsed){
  document.body.classList.toggle('sidebar-collapsed',collapsed);
  const toggle=$('#sidebar-toggle');
  toggle.setAttribute('aria-expanded',String(!collapsed));
  toggle.setAttribute('aria-label',collapsed?'展开侧栏':'收起侧栏');
  toggle.title=collapsed?'展开侧栏':'收起侧栏';
  $('#sidebar-resizer').tabIndex=collapsed?-1:0;
  try{localStorage.setItem('zhichengapp.sidebarCollapsed',String(collapsed));}catch{}
}
try{setSidebarCollapsed(localStorage.getItem('zhichengapp.sidebarCollapsed')==='true');}catch{}
$('#sidebar-toggle').onclick=()=>setSidebarCollapsed(!document.body.classList.contains('sidebar-collapsed'));
$('#search-sessions-toggle').onclick=()=>{
  const panel=$('#session-search-panel');panel.hidden=!panel.hidden;
  $('#search-sessions-toggle').setAttribute('aria-expanded',String(!panel.hidden));
  if(panel.hidden){$('#session-search').value='';renderSessions();}else $('#session-search').focus();
};
$('#session-search').oninput=()=>renderSessions();
$('#session-search').onkeydown=event=>{if(event.key==='Escape'){$('#search-sessions-toggle').click();$('#search-sessions-toggle').focus();}};
const names = {queued:'排队中',processing:'处理中',completed:'已完成',failed:'失败',interrupted:'已中断',needs_review:'待确认',ready:'可用',building:'构建中',deleting:'删除中',deleted:'已删除',cancelled:'已取消'};
// 所有资料内容通过 textContent 渲染；文档或模型输出不能变成 HTML 或脚本。
function node(tag,text,cls){const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(cls)el.className=cls;return el;}
const noticeBox=$('#notice');
let noticeTimer,noticeField;
function markNoticeField(field){
  noticeField=field;field.setAttribute('aria-invalid','true');
  field.setAttribute('aria-describedby',[field.getAttribute('aria-describedby'),'notice'].filter(Boolean).join(' '));
  field.addEventListener('change',()=>{if(noticeField===field)notice('');},{once:true});
}
// 操作反馈跟随对应的表单或按钮；待补充项和错误保留到用户处理。
function notice(text,{target,tone='info',field}={}){
  clearTimeout(noticeTimer);
  if(noticeField){
    noticeField.removeAttribute('aria-invalid');
    const describedBy=(noticeField.getAttribute('aria-describedby')||'').split(' ').filter(id=>id&&id!=='notice');
    if(describedBy.length)noticeField.setAttribute('aria-describedby',describedBy.join(' '));else noticeField.removeAttribute('aria-describedby');
    noticeField=null;
  }
  noticeBox.replaceChildren();noticeBox.hidden=!text;
  if(!text)return;
  const anchor=typeof target==='string'?$(target):target;
  const host=anchor?.isConnected?(anchor.closest('.actions,form,.toolbar,.item,.suggestion')||anchor):($('#chat:not([hidden]) #chat-form')||$('.view:not([hidden])>header'));
  if(field&&!field.closest('.actions'))field.after(noticeBox);
  else if(anchor?.matches('h1,h2'))anchor.after(noticeBox);
  else host.append(noticeBox);
  noticeBox.dataset.tone=tone;
  noticeBox.setAttribute('role',tone==='error'?'alert':'status');
  noticeBox.setAttribute('aria-live',tone==='error'?'assertive':'polite');
  const icon=node('span',({info:'i',warning:'!',error:'!',success:'✓'})[tone]||'i','notice-icon');
  icon.setAttribute('aria-hidden','true');noticeBox.append(icon,node('span',text));
  if(field){
    markNoticeField(field);field.focus();
  }
  noticeBox.scrollIntoView({block:'nearest',inline:'nearest'});
  if(tone==='info'||tone==='success')noticeTimer=setTimeout(()=>notice(''),8000);
}
async function api(path,options={}){const response=await fetch('/api/v1'+path,options);if(response.status===204)return null;const data=await response.json();if(!response.ok)throw new Error(data.message||'请求失败');return data;}
const json=(method,body)=>({method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
function scope(){return {course_ids:state.projectId?[]:($('#course').value?[$('#course').value]:[]),project_id:state.projectId};}
function act(label,callback){const button=node('button',label,'outline');button.type='button';button.onclick=async()=>{button.disabled=true;try{await callback(button);}catch(error){notice(error.message,{target:button,tone:'error'});}finally{button.disabled=false;}};return button;}
function view(name){speechInput?.cancel();notice('');closeCoursePicker();closeUploadCoursePicker();document.querySelectorAll('.view').forEach(el=>el.hidden=el.id!==name);document.querySelectorAll('nav button').forEach(button=>{const selected=button.dataset.view===name;button.classList.toggle('active',selected);if(selected)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current');});}
document.querySelectorAll('nav button').forEach(button=>button.onclick=()=>view(button.dataset.view));
function sourceLabel(loc){
  if(loc.format==='docx')return '原文可按上方章节标题在 Word 中查找。';
  if(loc.format==='normalized')return `解析后文本第 ${loc.start}–${Math.max(loc.start,(loc.end_exclusive||loc.start+1)-1)} 行，可下载原件核对`;
  if(!loc.start)return '原文位置暂不可用';
  const end=Math.max(loc.start,(loc.end_exclusive||loc.start+1)-1);
  const range=end===loc.start?String(loc.start):`${loc.start}–${end}`;
  return `原文第 ${range} 行（Markdown 文件行号）`;
}
function citations(container,items){if(!items?.length)return;const wrapper=node('div',undefined,'citations');wrapper.append(node('strong','引用与原文'));for(const item of items){const details=node('details');details.append(node('summary',`[${item.id}] ${item.source_name} · ${(item.title_path||[]).join(' / ')}${item.title_path?.length?'（章节标题）':''}`),node('blockquote',item.text));documentImages(details,item.text,item);const loc=item.source||{};details.append(node('div',sourceLabel(loc)));if(item.source_available!==false){const link=node('a','下载对应版本原件 ↗');link.href=`/api/v1/documents/${encodeURIComponent(item.document_id)}/source?version_id=${encodeURIComponent(item.version_id)}`;link.target='_blank';details.append(link);}else details.append(node('span','原件已不可访问，以上为当时引用快照。'));wrapper.append(details);}container.append(wrapper);}
function documentImages(container,text,document){
  if(!document?.document_id||!document?.version_id)return;
  const paths=document.images||[...String(text||'').matchAll(/!\[[^\]]*\]\(<?([^\s)>]+)>?(?:\s+["'][^"']*["'])?\)/g)].map(match=>match[1]);
  for(const path of new Set(paths)){
    const image=node('img',undefined,'document-image');image.alt='资料图片';image.loading='lazy';
    image.src=`/api/v1/documents/${encodeURIComponent(document.document_id)}/asset?version_id=${encodeURIComponent(document.version_id)}&path=${encodeURIComponent(path)}`;
    image.onerror=()=>image.remove();container.append(image);
  }
}
function questionCard(question){const item=node('article',undefined,'item');item.append(node('div',`${question.code} · ${question.question_type}`,'badge'),node('p',question.stem,'question-text'),node('p',question.options||'','question-text'));documentImages(item,(question.stem||'')+'\n'+(question.options||''),question);const answer=node('div',undefined,'solution');answer.hidden=true;item.append(act('查看标准答案与解析',async()=>{const data=await api(`/questions/${encodeURIComponent(question.question_id)}/solution`);answer.textContent=`标准答案：${data.answer}\n解析：${data.explanation}\n审核状态：${data.review_status==='unreviewed'?'尚未人工审核':data.review_status}`;documentImages(answer,data.answer+'\n'+data.explanation,{...question,images:data.images||[]});answer.hidden=false;}),answer);return item;}
function message(role,text,result={}){const item=node('article',undefined,'message '+role);item.append(node('div',role==='user'?'我的问题':'知成助教','role'));const answer=node('div',text,'answer');item.append(answer);citations(item,result.citations);courseChoices(item,result);for(const question of result.questions||[])item.append(questionCard(question));$('#messages').append(item);$('#messages').scrollTop=$('#messages').scrollHeight;return {item,answer};}
async function allCatalogRows(path){
  const rows=[];
  for(let offset=0;;offset+=200){const data=await api(`${path}?limit=200&offset=${offset}`);rows.push(...data.items);if(data.items.length<200)return rows;}
}
async function loadCourses(){
  const selected=$('#course').value;
  [state.courses,state.series]=await Promise.all([allCatalogRows('/courses'),allCatalogRows('/courses/series')]);
  $('#course').replaceChildren(node('option','请选择课程'));$('#course').firstChild.value='';
  for(const course of state.courses){const option=node('option',course.name);option.value=course.course_id;$('#course').append(option);}
  $('#course').value=selected;
  const direction=$('#direction-filter').value;
  $('#direction-filter').replaceChildren(node('option','全部方向'));$('#direction-filter').firstChild.value='';
  for(const label of [...new Set(catalogGroups().map(group=>catalogDirection(group.series||group.course)).filter(Boolean))].sort()){
    const option=node('option',label);option.value=label;$('#direction-filter').append(option);
  }
  $('#direction-filter').value=direction;syncCourse();loadUploadCourses();renderCourses($('#course-search').value);
}
let activeSeriesKey=null;
function catalogDirection(record){return (record.description?.includes(' / ')?record.description:record.category||record.description||'').split(' / ')[0];}
function catalogText(record){return [record.name,record.course_id,record.series_id,record.description,record.category,...(record.aliases||[])].join(' ').toLocaleLowerCase();}
function courseCatalogText(course){
  const series=state.series.find(series=>series.series_id===course.series_id&&series.document_id===course.document_id&&series.version_id===course.version_id);
  return catalogText(course)+(series?' '+catalogText(series):'');
}
function catalogGroups(){
  const groups=state.series.map(series=>({key:series.id,series,courses:[]}));
  const byIdentity=new Map(groups.map(group=>[JSON.stringify([group.series.document_id,group.series.version_id,group.series.series_id]),group]));
  for(const course of state.courses){
    const group=byIdentity.get(JSON.stringify([course.document_id,course.version_id,course.series_id]));
    if(group)group.courses.push(course);else groups.push({key:'course:'+course.course_id,course,courses:[course]});
  }
  return groups;
}
let coursePage=0;
let recentCourses=[];
try{recentCourses=JSON.parse(localStorage.getItem('zhichengapp.recentCourses')||'[]');if(!Array.isArray(recentCourses))recentCourses=[];}catch{}
function syncCourse(){
  const course=state.courses.find(c=>c.course_id===$('#course').value);
  $('#sections-heading').textContent=course?course.name+' · 课程章节':'当前课程章节';
  $('#scope-change').textContent=course||state.projectId?'更换课程':'选择课程';
  $('#chat-scope').textContent=course?'当前课程 · '+course.name:state.projectId?'当前范围 · 项目资料':'未限定课程 · 可以先描述问题，帮你找方向';
  if(!$('#chat-course-picker').hidden)renderCoursePicker();
}
async function selectCourse(id){
  if(state.busy||state.historyBusy||speechInput?.busy)throw new Error('请等待当前操作结束后切换课程。');
  if(id&&!state.courses.some(c=>c.course_id===id))throw new Error('课程已不可用，请刷新课程列表。');
  $('#course').value=id;state.projectId=null;
  if(id)recentCourses=[id,...recentCourses.filter(c=>c!==id)].slice(0,6);
  try{localStorage.setItem('zhichengapp.recentCourses',JSON.stringify(recentCourses));}catch{}
  $('#questions').replaceChildren();syncCourse();renderCourses($('#course-search').value);await loadSections();
}
function courseCatalogCard(course){
  const selected=course.course_id===$('#course').value;
  const card=node('article',undefined,'card course-card'+(selected?' selected':''));
  card.append(node('div',recentCourses.includes(course.course_id)?'最近学习':'课程','eyebrow'),node('h2',course.name),node('p',course.description||'查看课程章节，围绕知识点向助教提问。','course-description'));
  const footer=node('div',undefined,'course-card-footer'),actions=node('div',undefined,'course-card-actions');
  footer.append(node('span',course.material_status==='available'?'讲义可用':'尚无讲义','badge'));
  actions.append(act(selected?'当前课程':'查看章节',async()=>{await selectCourse(course.course_id);notice('已选择：'+course.name,{target:'#sections-heading',tone:'success'});$('#sections-heading').scrollIntoView({behavior:'smooth',block:'start'});}),act('向助教提问',async()=>{await selectCourse(course.course_id);$('#intent').value='auto';view('chat');$('#question').focus();notice('已选择：'+course.name,{target:'#chat-form',tone:'success'});}),act('添加资料',async()=>openCourseMaterials(course.course_id)));
  footer.append(actions);card.append(footer);
  return card;
}
function renderCourses(search){
  const parent=$('#course-results');parent.replaceChildren();
  const term=search.trim().toLocaleLowerCase(),direction=$('#direction-filter').value,material=$('#material-filter').value;
  const groups=catalogGroups();
  let active=groups.find(group=>group.series&&group.key===activeSeriesKey);
  if(!active)activeSeriesKey=null;
  $('#series-navigation').hidden=!active;
  $('#series-title').textContent=active?.series.name||'';
  const matchesCourse=(course,seriesMatches)=>((!term||seriesMatches||catalogText(course).includes(term))&&(!material||course.material_status===material));
  let rows;
  if(active){
    rows=(!direction||catalogDirection(active.series)===direction)?active.courses.filter(course=>matchesCourse(course,catalogText(active.series).includes(term))):[];
    $('#course-count').textContent=`${active.series.name} · ${rows.length} 门课程`;
  }else{
    rows=groups.filter(group=>!direction||catalogDirection(group.series||group.course)===direction).map(group=>{
      const seriesMatches=group.series&&catalogText(group.series).includes(term);
      return {...group,matched:group.courses.filter(course=>matchesCourse(course,seriesMatches))};
    }).filter(group=>group.matched.length||group.series&&!material&&(!term||catalogText(group.series).includes(term)));
    const seriesCount=rows.filter(group=>group.series).length;
    const standalone=rows.filter(group=>!group.series).length;
    $('#course-count').textContent=`${seriesCount} 个课程系列 · ${rows.reduce((sum,group)=>sum+group.matched.length,0)} 门课程${standalone?`（含 ${standalone} 门独立课程）`:''}`;
  }
  const rank=course=>{const i=recentCourses.indexOf(course.course_id);return i<0?999:i;};
  if(!term)rows.sort((a,b)=>(active?rank(a):Math.min(999,...a.courses.map(rank)))-(active?rank(b):Math.min(999,...b.courses.map(rank))));
  const pages=Math.max(1,Math.ceil(rows.length/8));coursePage=Math.max(0,Math.min(coursePage,pages-1));
  $('#course-page').textContent=`${coursePage+1} / ${pages}`;$('#course-prev').disabled=coursePage===0;$('#course-next').disabled=coursePage===pages-1;
  for(const row of rows.slice(coursePage*8,coursePage*8+8)){
    if(active||!row.series){parent.append(courseCatalogCard(active?row:row.course));continue;}
    const card=node('article',undefined,'card course-card series-card');
    const available=row.courses.filter(course=>course.material_status==='available').length;
    card.append(node('div','课程系列','eyebrow'),node('h2',row.series.name),node('p',row.series.description||row.series.category||'查看该系列包含的课程。','course-description'),node('span',`${row.courses.length} 门课程 · ${available} 门有讲义`,'badge'));
    if(term||material)card.append(node('p',row.matched.length?`匹配课程：${row.matched.map(course=>course.name).join('、')}`:'匹配课程系列名称或介绍','series-matches'));
    card.append(act('查看系列课程',async()=>{activeSeriesKey=row.key;coursePage=0;renderCourses($('#course-search').value);$('#series-back').focus();}));
    parent.append(card);
  }
  if(!rows.length)parent.append(node('p',active?'该系列暂无符合条件的课程。':state.courses.length||state.series.length?'没有匹配的课程系列或课程，试试其他关键词。':'还没有课程，请先在资料管理中创建课程或导入目录。','empty-state'));
}
$('#series-back').onclick=()=>{activeSeriesKey=null;coursePage=0;renderCourses($('#course-search').value);$('#course-search').focus();};
function browseCourses(){view('courses');$('#course-search').focus();}
function closeCoursePicker(){
  $('#chat-course-picker').hidden=true;$('#scope-change').setAttribute('aria-expanded','false');
}
function renderCoursePicker(){
  const term=$('#chat-course-search').value.trim().toLocaleLowerCase();
  const rows=state.courses.filter(course=>!term||courseCatalogText(course).includes(term));
  const rank=course=>course.course_id===$('#course').value?-1:recentCourses.includes(course.course_id)?recentCourses.indexOf(course.course_id):999;
  rows.sort((a,b)=>rank(a)-rank(b));
  const list=$('#chat-course-results');list.replaceChildren();
  for(const course of rows.slice(0,50)){
    const selected=course.course_id===$('#course').value;
    const button=act(course.name,async()=>{
      await selectCourse(course.course_id);closeCoursePicker();$('#intent').value='auto';$('#question').focus();
      notice('已选择：'+course.name,{target:'#chat-form',tone:'success'});
    });
    button.className='course-picker-option';button.setAttribute('aria-pressed',String(selected));
    button.append(node('span',selected?'当前':course.material_status==='available'?'有讲义':'暂无讲义','course-picker-meta'));
    list.append(button);
  }
  if(!rows.length)list.append(node('p',state.courses.length?'没有匹配课程，试试其他关键词。':'还没有课程，可前往资料管理创建课程或导入目录。','muted'));
  $('#chat-course-count').textContent=rows.length>50?`找到 ${rows.length} 门，显示前 50 门；输入关键词缩小范围。`:`${rows.length} 门课程`;
}
function toggleCoursePicker(){
  if(!$('#chat-course-picker').hidden){closeCoursePicker();return;}
  if(state.busy||state.historyBusy||speechInput?.busy){notice('请等待当前操作结束后切换课程。',{target:'#chat-form',tone:'warning'});return;}
  $('#chat-course-search').value='';renderCoursePicker();$('#chat-course-picker').hidden=false;
  $('#scope-change').setAttribute('aria-expanded','true');$('#chat-course-search').focus();
}
$('#chat-course-search').oninput=renderCoursePicker;
$('#chat-course-search').onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();$('#chat-course-results button')?.focus();}};
$('#chat-course-close').onclick=()=>{closeCoursePicker();$('#scope-change').focus();};
$('#chat-course-browse').onclick=browseCourses;
$('#chat-course-clear').onclick=async()=>{
  try{await selectCourse('');closeCoursePicker();$('#question').focus();notice('已取消课程限定，可以直接描述问题。',{target:'#chat-form',tone:'success'});}
  catch(error){notice(error.message,{target:'#chat-form',tone:'warning'});}
};
$('#chat-course-picker').addEventListener('keydown',event=>{
  if(event.key==='Escape'){event.preventDefault();event.stopPropagation();closeCoursePicker();$('#scope-change').focus();}
});
document.addEventListener('click',event=>{if(!event.target.closest('#chat-course-picker,#scope-change'))closeCoursePicker();});
function guideCourses(){
  if(state.busy||state.historyBusy||speechInput?.busy){notice('请等待当前操作结束。',{target:'#chat-form',tone:'warning'});return;}
  state.session=null;state.projectId=null;$('#course').value='';syncCourse();renderSessions();
  $('#messages').replaceChildren();message('assistant','先不用选课程。你想解决什么问题，或者想学会做什么？也可以说说你已有的基础。比如：想做一个网页，或用 Python 处理 Excel。');
  $('#intent').value='recommend';view('chat');$('#question').value='';$('#question').focus();notice('');
}
$('#catalog-guide').onclick=guideCourses;$('#scope-change').onclick=toggleCoursePicker;
$('#welcome-browse').onclick=browseCourses;$('#welcome-guide').onclick=guideCourses;
function searchCourses(){coursePage=0;renderCourses($('#course-search').value);}
$('#search-courses').onclick=searchCourses;$('#course-search').oninput=searchCourses;$('#material-filter').onchange=searchCourses;$('#direction-filter').onchange=searchCourses;
$('#course-prev').onclick=()=>{coursePage--;renderCourses($('#course-search').value);};
$('#course-next').onclick=()=>{coursePage++;renderCourses($('#course-search').value);};
function courseChoices(container,result){
  if(!result.choices?.length)return;
  const list=node('div',undefined,'course-suggestions');
  for(const course of result.choices){
    const card=node('div',undefined,'suggestion');
    card.append(node('strong',course.name),node('p',course.reason||'可能与你的问题相关','muted'),node('span',course.material_status==='available'?'讲义可用':'尚无讲义','badge'),act('选择这门课程',async()=>{await selectCourse(course.course_id);$('#intent').value='auto';$('#question').value=result.followup_question||'';view('chat');$('#question').focus();notice('已选择：'+course.name+'。可修改输入框中的问题后发送。',{target:'#chat-form',tone:'success'});}));
    list.append(card);
  }
  container.append(list);
}
let sectionsRequest=0;
async function loadSections(){
  const request=++sectionsRequest,parent=$('#sections'),courseId=$('#course').value;
  parent.replaceChildren();if(!courseId){parent.textContent='先选择课程。';return;}
  const data=await api(`/courses/${encodeURIComponent(courseId)}/sections`);
  if(request!==sectionsRequest)return;
  const groups=new Map();
  for(const section of data.items){
    if(!groups.has(section.document_id))groups.set(section.document_id,[]);
    groups.get(section.document_id).push(section);
  }
  for(const rows of groups.values()){
    const document=node('details',undefined,'section-document');
    document.append(node('summary',`${rows[0].source_name||'课程讲义'} · ${rows.length} 个章节`));
    const tree=node('ul',undefined,'section-tree'),nodes=new Map();
    for(const section of rows){
      const li=node('li'),branch=node('details'),children=node('ul');
      branch.append(node('summary',section.title||section.title_path?.at(-1)||'正文'),children);
      li.append(branch);nodes.set(section.id||section.section_id,{li,branch,children});
    }
    for(const section of rows){
      const entry=nodes.get(section.id||section.section_id),ancestor=nodes.get(section.parent_section_id);
      (ancestor?.children||tree).append(entry.li);
    }
    for(const section of rows){
      const entry=nodes.get(section.id||section.section_id);
      if(!entry.children.children.length)entry.li.replaceChildren(node('span',section.title||section.title_path?.at(-1)||'正文'));
    }
    document.append(tree);parent.append(document);
  }
  if(!data.items.length)parent.textContent='该课程尚无可用讲义章节。';
}
$('#course').onchange=()=>{state.projectId=null;syncCourse();loadSections().catch(error=>notice(error.message));};
$('#search-questions').onclick=async()=>{try{const params=new URLSearchParams({limit:'100'});for(const [key,selector] of [['course_id','#course'],['code','#question-code'],['question_type','#question-type']])if($(selector).value)params.set(key,$(selector).value.trim());const data=await api('/questions?'+params);$('#questions').replaceChildren(...data.items.map(questionCard));if(!data.items.length)$('#questions').append(node('p','当前条件下未找到题目。','muted'));}catch(error){notice(error.message,{tone:'error'});}};
async function loadSessions(){const data=await api('/sessions');state.sessions=data.items;renderSessions();}
function renderSessions(){
  const search=$('#session-search').value.trim().toLocaleLowerCase();
  const sessions=state.sessions.filter(session=>session.title.toLocaleLowerCase().includes(search));
  $('#sessions').replaceChildren();
  for(const session of sessions){
    const row=node('div',undefined,'session-row');
    const button=act(session.title,async()=>{
      if(state.busy||state.historyBusy||speechInput?.busy)throw new Error('请先等待当前操作结束。');
      state.historyBusy=true;
      try{
      const history=await api(`/sessions/${encodeURIComponent(session.id)}/messages`);
      state.session=session.id;state.projectId=session.project_id||null;
      $('#course').value=session.course_ids?.[0]||'';
      $('#messages').replaceChildren();
      for(const row of history.items)message(row.role,row.content+(row.status==='completed'?'':`\n（${names[row.status]||row.status}）`),row);
      syncCourse();$('#intent').value='auto';renderSessions();view('chat');await loadSections();
      }finally{state.historyBusy=false;}
    });
    button.classList.add('session-open');
    button.title=session.title;
    if(session.id===state.session){button.classList.add('active');button.setAttribute('aria-current','true');}
    const remove=act('删除',async()=>{
      if(state.busy||state.historyBusy||speechInput?.busy)throw new Error('请先等待当前操作结束后删除会话。');
      if(!confirm(`删除对话“${session.title}”及全部消息？删除后无法恢复。`))return;
      state.historyBusy=true;
      try{
        await api(`/sessions/${encodeURIComponent(session.id)}`,{method:'DELETE'});
        state.sessions=state.sessions.filter(item=>item.id!==session.id);
        if(state.session===session.id){
          state.session=null;state.projectId=null;
          $('#messages').replaceChildren();$('#question').value='';$('#intent').value='auto';syncCourse();
        }
        renderSessions();notice('对话及消息已删除。',{target:'#chat-form',tone:'success'});
        const focusTarget=$('#sessions .session-open')||$('#new-session');focusTarget.focus();
      }finally{state.historyBusy=false;}
    });
    remove.classList.add('session-delete');
    remove.setAttribute('aria-label',`删除对话：${session.title}`);
    remove.title='删除对话及全部消息';
    row.append(button,remove);$('#sessions').append(row);
  }
  $('#session-empty').hidden=sessions.length>0;
  $('#session-empty').textContent=search?'没有找到相关对话':'还没有历史对话';
}
$('#new-session').onclick=()=>{if(state.busy||state.historyBusy||speechInput?.busy)return;state.session=null;state.projectId=null;$('#messages').replaceChildren();$('#intent').value='auto';syncCourse();view('chat');renderSessions();notice('新对话已准备好。',{target:'#chat-form',tone:'success'});$('#question').focus();};
// 增量解码并按 SSE 空行分帧；只有 done 才确认完整答案，断流文本不能当最终结果。
async function readStream(response,rendered){const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='',answer='',ended=false;try{while(true){const {done,value}=await reader.read();if(done)break;buffer+=decoder.decode(value,{stream:true}).replace(/\r\n/g,'\n');let boundary;while((boundary=buffer.indexOf('\n\n'))>=0){const lines=buffer.slice(0,boundary).split('\n');buffer=buffer.slice(boundary+2);const kind=lines.find(line=>line.startsWith('event:'))?.slice(6).trim(),raw=lines.filter(line=>line.startsWith('data:')).map(line=>line.slice(5).trimStart()).join('\n');if(!raw)continue;const pane=$('#messages'),follow=pane.scrollHeight-pane.scrollTop-pane.clientHeight<90;const data=JSON.parse(raw);if(kind==='status'&&!answer)rendered.answer.textContent=data.message;if(kind==='token'){rendered.item.classList.add('provisional');rendered.item.querySelector('.role').textContent='知成助教 · 正在生成，引用待核对';answer+=data.text;rendered.answer.textContent=answer;}if(kind==='done'){ended=true;rendered.item.classList.remove('provisional');rendered.item.querySelector('.role').textContent='知成助教';rendered.answer.textContent=data.result.answer;citations(rendered.item,data.result.citations);courseChoices(rendered.item,data.result);for(const item of data.result.questions||[])rendered.item.append(questionCard(item));}if(follow)pane.scrollTop=pane.scrollHeight;if(kind==='error'){ended=true;throw new Error(data.message);}}}if(!ended)throw new Error('连接中断，本次输出未确认完成。');}finally{reader.releaseLock();}}
speechInput = ChatSpeech.create({
  button: $('#speech-record'), cancelButton: $('#speech-cancel'), status: $('#speech-status'),
  question: $('#question'), send: $('#send'), isBusy: () => state.busy || state.historyBusy,
  notify: (text, tone) => notice(text, {target: '#chat-form', tone}),
  transcribe: (audio, signal) => {
    const body = new FormData(); body.append('audio', audio, 'recording.wav');
    return api('/speech/transcribe', {method: 'POST', body, signal});
  }
});
$('#chat-form').onsubmit=async event=>{event.preventDefault();if(state.busy||state.historyBusy||speechInput?.busy)return;closeCoursePicker();const question=$('#question').value.trim();if(!question)return;state.busy=true;$('#send').disabled=true;$('#stop').hidden=false;notice('');let rendered;try{if(!state.session){const session=await api('/sessions',json('POST',{title:question.slice(0,50),...scope()}));state.session=session.id;await loadSessions();}message('user',question);rendered=message('assistant','正在检索并核对证据…');$('#question').value='';state.controller=new AbortController();const response=await fetch('/api/v1/query/stream',{...json('POST',{session_id:state.session,question,...scope(),intent:$('#intent').value}),signal:state.controller.signal});if(!response.ok){const error=await response.json();throw new Error(error.message||'请求失败');}await readStream(response,rendered);}catch(error){const text=error.name==='AbortError'?'已停止，本次输出未确认完成。':error.message;if(rendered)rendered.answer.textContent=text;notice(text,{target:'#chat-form',tone:error.name==='AbortError'?'info':'error'});}finally{state.busy=false;state.controller=null;$('#send').disabled=false;$('#stop').hidden=true;}};
$('#question').addEventListener('keydown',event=>{
  if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing&&event.keyCode!==229){event.preventDefault();if(!state.busy&&!state.historyBusy)$('#chat-form').requestSubmit();}
});
$('#stop').onclick=()=>state.controller?.abort();
let uploadBusy=false;
let uploadLimits=null;
function uploadSelectionError(files,limits){
  if(!limits)return '正在获取上传限制，请稍后重试。';
  if(files.length>limits.max_batch_files)return `每批最多选择 ${limits.max_batch_files} 份资料，当前选择了 ${files.length} 份，请分批上传。`;
  const oversized=files.find(file=>file.size>limits.max_file_bytes);
  if(oversized)return `“${oversized.name}”超过每份 ${limits.max_file_bytes/(1024*1024)} MB 限制，请缩小文件后重试。`;
  if(files.reduce((size,file)=>size+file.size,0)>limits.max_batch_bytes)return `本批文件合计超过 ${limits.max_batch_bytes/(1024*1024)} MB，请分批上传。`;
  return '';
}
function selectedUploadFiles(){return Array.from($('#folder').files.length?$('#folder').files:$('#file').files);}
for(const selector of ['#file','#folder'])$(selector).addEventListener('change',()=>{
  $(selector==='#file'?'#folder':'#file').value='';
  const files=selectedUploadFiles();
  $('#upload-selection').textContent=files.length?`已选择 ${files.length} 个文件 · ${(files.reduce((sum,file)=>sum+file.size,0)/(1024*1024)).toFixed(1)} MB`:'';
  const message=uploadSelectionError(files,uploadLimits);
  if(message)notice(message,{target:'#upload-form',tone:'warning',field:$(selector)});
});
let manageCourseId=null;
let uploadCoursePage=0;
const uploadCoursePageSize=10;
function closeUploadCoursePicker(){
  $('#upload-course-picker').hidden=true;
  $('#upload-course-trigger').setAttribute('aria-expanded','false');
}
function syncUploadCourseLabel(){
  const course=state.courses.find(course=>course.course_id===$('#upload-course').value);
  $('#upload-course-label').textContent=course?.name||'请选择课程';
  $('#upload-course-trigger').classList.toggle('has-selection',Boolean(course));
}
function renderUploadCoursePicker(){
  const term=$('#upload-course-search').value.trim().toLocaleLowerCase();
  const rows=state.courses.filter(course=>!term||courseCatalogText(course).includes(term));
  const pages=Math.max(1,Math.ceil(rows.length/uploadCoursePageSize));
  uploadCoursePage=Math.max(0,Math.min(uploadCoursePage,pages-1));
  const list=$('#upload-course-results');list.replaceChildren();
  for(const course of rows.slice(uploadCoursePage*uploadCoursePageSize,(uploadCoursePage+1)*uploadCoursePageSize)){
    const selected=course.course_id===$('#upload-course').value;
    const button=node('button',undefined,'course-picker-option');button.type='button';
    button.setAttribute('aria-pressed',String(selected));
    button.append(node('span',course.name),node('span',selected?'✓ 已选':'','course-picker-meta'));
    button.onclick=()=>{
      $('#upload-course').value=course.course_id;
      $('#upload-course').dispatchEvent(new Event('change',{bubbles:true}));
      $('#upload-course-trigger').dispatchEvent(new Event('change',{bubbles:true}));
      closeUploadCoursePicker();$('#upload-course-trigger').focus();
    };
    list.append(button);
  }
  if(!rows.length)list.append(node('p',state.courses.length?'未找到相关课程，试试其他关键词。':'还没有课程，请先创建课程或导入课程目录。','empty-state'));
  $('#upload-course-count').textContent=`${term?'找到':'共'} ${rows.length} 门课程`;
  $('#upload-course-pagination').hidden=rows.length<=uploadCoursePageSize;
  $('#upload-course-page').textContent=`${uploadCoursePage+1} / ${pages}`;
  $('#upload-course-prev').disabled=uploadCoursePage===0;
  $('#upload-course-next').disabled=uploadCoursePage===pages-1;
}
$('#upload-course-trigger').onclick=()=>{
  if(!$('#upload-course-picker').hidden){closeUploadCoursePicker();return;}
  $('#upload-course-search').value='';
  const selectedIndex=state.courses.findIndex(course=>course.course_id===$('#upload-course').value);
  uploadCoursePage=Math.floor(Math.max(0,selectedIndex)/uploadCoursePageSize);
  renderUploadCoursePicker();$('#upload-course-picker').hidden=false;
  $('#upload-course-trigger').setAttribute('aria-expanded','true');$('#upload-course-search').focus();
};
$('#upload-course').addEventListener('change',syncUploadCourseLabel);
$('#upload-course-search').oninput=()=>{uploadCoursePage=0;renderUploadCoursePicker();};
$('#upload-course-search').onkeydown=event=>{
  if(event.key==='Enter'){event.preventDefault();$('#upload-course-results button')?.focus();}
};
for(const [id,step] of [['prev',-1],['next',1]])$('#upload-course-'+id).onclick=()=>{uploadCoursePage+=step;renderUploadCoursePicker();};
$('#upload-course-close').onclick=()=>{closeUploadCoursePicker();$('#upload-course-trigger').focus();};
$('#upload-course-field').addEventListener('keydown',event=>{
  if(event.key==='Escape'){event.preventDefault();event.stopPropagation();closeUploadCoursePicker();$('#upload-course-trigger').focus();}
  if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)&&event.target.closest('#upload-course-results')){
    event.preventDefault();const buttons=Array.from($('#upload-course-results').children);
    const index=buttons.indexOf(event.target);
    buttons[event.key==='Home'?0:event.key==='End'?buttons.length-1:Math.max(0,Math.min(buttons.length-1,index+(event.key==='ArrowDown'?1:-1)))]?.focus();
  }
});
document.addEventListener('click',event=>{if(!event.target.closest('#upload-course-field'))closeUploadCoursePicker();});
$('#upload-course-field').addEventListener('focusout',event=>{if(!event.currentTarget.contains(event.relatedTarget))closeUploadCoursePicker();});
// 上传归属独立于助教与课程浏览范围；刷新课程列表时只保留用户明确选择的上传目标。
function loadUploadCourses(){
  const select=$('#upload-course'),selected=select.value;
  select.replaceChildren(node('option','请选择课程'));
  select.firstChild.value='';
  for(const course of state.courses){const option=node('option',course.name);option.value=course.course_id;select.append(option);}
  select.value=state.courses.some(course=>course.course_id===selected)?selected:'';
  syncUploadCourseLabel();renderUploadCoursePicker();syncUploadScope();
}
function openCourseMaterials(id){
  if(uploadBusy)return;
  if(!state.courses.some(course=>course.course_id===id))return;
  manageCourseId=id;
  $('#upload-scope-kind').value='course';$('#upload-course').value=id;
  $('#content-type').value='lecture';$('#upload-options').open=false;
  $('#file').value='';$('#folder').value='';$('#upload-selection').textContent='';$('#upload-progress').replaceChildren();
  view('manage');syncUploadScope();
  $('#manage-title').scrollIntoView({block:'start'});$('#file').focus({preventScroll:true});
}
$('#manage-back').onclick=()=>{
  if(uploadBusy)return;
  manageCourseId=null;$('#file').value='';$('#folder').value='';$('#upload-selection').textContent='';$('#upload-progress').replaceChildren();
  notice('');syncUploadScope();$('#upload-course-trigger').focus();
};
function syncUploadScope(){
  if(manageCourseId&&!state.courses.some(course=>course.course_id===manageCourseId))manageCourseId=null;
  const course=state.courses.find(course=>course.course_id===manageCourseId);
  const inCourse=Boolean(course);
  if(inCourse){$('#upload-scope-kind').value='course';$('#upload-course').value=manageCourseId;}
  const catalog=$('#content-type').value==='catalog',kind=$('#upload-scope-kind').value;
  $('#manage-title').textContent=inCourse?course.name+' → 添加教学资料':'管理课程与教学资料';
  $('#manage-intro').textContent=inCourse?'上传讲义后，系统会自动整理章节，供学习和提问使用。':'先创建课程，再为课程添加讲义。已有课程？可直接选择课程上传。';
  $('#manage-back').hidden=!inCourse;$('#manage-back').disabled=uploadBusy;
  $('#create-course').hidden=inCourse;$('#manage-grid').classList.toggle('course-materials',inCourse);
  $('#upload-course-context').hidden=!inCourse;
  $('#upload-course-context').textContent=inCourse?'文件将自动添加到「'+course.name+'」。':'';
  $('#content-type option[value="catalog"]').disabled=inCourse;
  const types={lecture:'课程讲义',project:'项目资料',catalog:'课程目录',question_bank:'结构化题库'};
  $('#upload-options summary').textContent='资料设置（当前：'+types[$('#content-type').value]+'）';
  $('#upload-attribution').hidden=catalog||inCourse;
  $('#upload-course-field').hidden=catalog||inCourse||kind!=='course';
  $('#upload-project-field').hidden=catalog||kind!=='project';
  $('#upload-scope-kind').disabled=catalog||uploadBusy;
  $('#upload-course').disabled=catalog||kind!=='course'||uploadBusy;
  $('#upload-course').required=!catalog&&kind==='course';
  $('#upload-course-trigger').disabled=$('#upload-course').disabled;
  if($('#upload-course-trigger').disabled)closeUploadCoursePicker();
  syncUploadCourseLabel();
  $('#upload-project').disabled=catalog||kind!=='project'||uploadBusy;
  $('#upload-project').required=!catalog&&kind==='project';
  const extensions=uploadLimits?.supported_extensions||['.md','.markdown','.txt','.docx','.pdf','.doc','.ppt','.pptx','.png','.jpg','.jpeg','.jp2','.webp','.gif','.bmp'];
  $('#file').accept=extensions.join(',');
  $('#file').disabled=uploadBusy;$('#folder').disabled=uploadBusy;
  const format='Markdown / Word / PDF / PPT / 图片';
  $('#upload-file-label').textContent=uploadLimits?`${format} · 单文件最大 ${uploadLimits.max_file_bytes/(1024*1024)} MB`:`${format} · 正在获取上传限制…`;
  $('#upload-batch-help').textContent='可多选文件或选择整个文件夹；Markdown 引用的本地图片会随正文保存。';
  if(uploadLimits){
    $('#upload-batch-help').textContent+=` 每批最多 ${uploadLimits.max_batch_files} 个文件，合计 ${uploadLimits.max_batch_bytes/(1024*1024)} MB；解压内容上限 ${uploadLimits.max_docx_unpacked_bytes/(1024*1024)} MB。`;
    $('#upload-batch-help').textContent+=uploadLimits.mineru?' 可识别 PDF、Word、PPT 和图片。':' 当前可导入 Markdown、TXT 和 DOCX；其他格式及图片文字识别暂不可用。';
  }
  $('#manage-scope').textContent=inCourse?($('#content-type').value==='lecture'?'默认按课程讲义处理；上传题库等文件时，可展开资料设置。':'本次按'+types[$('#content-type').value]+'处理，请使用对应格式的文件。'):catalog?'课程目录由大模型识别系列与课程，无需固定标题格式；存在疑问时请先核对识别结果。':kind==='pending'?'先保存原文件，选择课程后再开始整理内容。':kind==='project'?'本次选择的文件将添加到下方填写的项目。':'本次选择的文件将添加到下方所选课程。';
}
function uploadFields(){
  const fields={content_type:$('#content-type').value};
  if(fields.content_type==='catalog')return fields;
  const kind=$('#upload-scope-kind').value;
  if(kind==='course'){
    fields.course_id=$('#upload-course').value;
    if(!state.courses.some(course=>course.course_id===fields.course_id))throw new Error('请先选择要添加资料的课程。');
  }else if(kind==='project'){
    fields.project_id=$('#upload-project').value.trim();
    if(!fields.project_id)throw new Error('请填写本次上传的项目名称。');
  }
  return fields;
}
$('#content-type').onchange=syncUploadScope;
$('#upload-scope-kind').onchange=syncUploadScope;
$('#upload-form').addEventListener('invalid',event=>{
  event.preventDefault();
  if(event.target!==event.currentTarget.querySelector(':invalid'))return;
  const prompts={'upload-course':'请先选择要添加资料的课程。','upload-project':'请填写本次上传的项目名称。',file:'请选择要上传的文件。'};
  notice(prompts[event.target.id]||'请补全本次上传信息。',{target:'#upload-form',tone:'warning',field:event.target.id==='upload-course'?$('#upload-course-trigger'):event.target});
},true);
syncUploadScope();
const stages={recognizing_document:'识别文档与图片',recognizing_questions:'识别题干与答案',recognizing_catalog:'模型识别课程结构',catalog_review:'等待确认目录结构',waiting:'等待处理',starting:'开始处理',parsing:'解析章节',embedding:'生成检索索引',done:'处理完成'};
function renderUploadTask(row,task){
  row.dataset.taskStatus=task.status;
  const reused=row.dataset.reused==='true'?'已有相同资料，复用原任务 · ':'';
  const processing=task.status==='processing'?` · ${stages[task.stage]||task.stage||'处理中'}${Number.isFinite(task.progress)?` · ${task.progress}%`:''}`:'';
  row.textContent=`${row.dataset.fileName} · ${reused}${names[task.status]||task.status}${processing}${task.error?' · '+task.error:''}`;
}
async function refreshUploadProgress(tasks){
  const byId=new Map(tasks.items.map(task=>[task.id,task]));
  await Promise.all(Array.from($('#upload-progress').querySelectorAll('[data-task-id]'),async row=>{
    let task=byId.get(row.dataset.taskId);
    if(!task&&!['completed','failed','interrupted','cancelled'].includes(row.dataset.taskStatus)){
      // 复用的旧任务可能不在最近 50 条记录中，按 ID 获取实际状态。
      try{task=await api(`/import-tasks/${encodeURIComponent(row.dataset.taskId)}`);}catch{return;}
    }
    if(task&&row.isConnected)renderUploadTask(row,task);
  }));
}
function uploadFiles(files,fields,onProgress){
  return new Promise((resolve,reject)=>{
    const xhr=new XMLHttpRequest(),data=new FormData();
    for(const file of files)data.append('files',file,file.webkitRelativePath||file.name);for(const [key,value] of Object.entries(fields))if(value)data.set(key,value);
    xhr.open('POST','/api/v1/documents/import-folder');
    xhr.upload.onprogress=event=>{if(event.lengthComputable)onProgress(Math.round(event.loaded/event.total*100));};
    xhr.onerror=()=>reject(new Error('上传连接失败，请重试'));
    xhr.ontimeout=()=>reject(new Error('上传超时，请重试'));xhr.timeout=600000;
    xhr.onload=()=>{try{const result=JSON.parse(xhr.responseText);if(xhr.status<200||xhr.status>=300)reject(new Error(result.message||'上传失败'));else resolve(result);}catch{reject(new Error('服务返回无效响应'));}};
    xhr.send(data);
  });
}
$('#upload-form').onsubmit=async event=>{
  event.preventDefault();if(uploadBusy)return;
  const files=selectedUploadFiles();if(!files.length){notice('请选择文件或资料文件夹。',{target:'#upload-form',tone:'warning',field:$('#file')});return;}
  const button=$('#upload-form button[type="submit"]'),progress=$('#upload-progress');
  let fields;try{fields=uploadFields();}catch(error){notice(error.message,{target:'#upload-form',tone:'warning',field:$('#upload-scope-kind').value==='project'?$('#upload-project'):$('#upload-course-trigger')});return;}
  uploadBusy=true;button.disabled=true;$('#content-type').disabled=true;syncUploadScope();progress.replaceChildren();let accepted=0;
  try{
    uploadLimits=(await api('/capabilities')).upload_limits;syncUploadScope();
    const selectionError=uploadSelectionError(files,uploadLimits);
    if(selectionError){notice(selectionError,{target:'#upload-form',tone:'warning',field:$('#file')});return;}
    const transferring=node('p');progress.append(transferring);
    const result=await uploadFiles(files,fields,percent=>{transferring.textContent=`正在上传 ${files.length} 个文件 · ${percent}%`;});
    transferring.remove();
    for(const item of result.items){
      const row=node('p');progress.append(row);
      if(item.task){
        accepted++;row.dataset.taskId=item.task.task_id;row.dataset.fileName=item.path;
        row.dataset.reused=String(Boolean(item.task.reused));renderUploadTask(row,item.task);
      }else row.textContent=`${item.path} · ${item.skipped?'已跳过':'上传失败'}：${item.message}`;
    }
    progress.prepend(node('strong',`已提交 ${accepted} 份资料，关联图片 ${result.attachments.length} 份`));
    if(result.items.every(item=>item.task||item.skipped)){
      $('#file').value='';$('#folder').value='';$('#upload-selection').textContent='';
    }
    await loadManage();
  }catch(error){notice(error.message,{target:'#upload-form',tone:'error'});}finally{uploadBusy=false;button.disabled=false;$('#content-type').disabled=false;syncUploadScope();}
};
$('#create-course').onsubmit=async event=>{
  event.preventDefault();if(uploadBusy)return;
  const button=event.target.querySelector('button');button.disabled=true;
  try{
    const name=$('#new-course-name').value.trim();
    if(!name)throw new Error('请填写课程名称。');
    const id='course_'+Array.from(crypto.getRandomValues(new Uint8Array(16)),value=>value.toString(16).padStart(2,'0')).join('');
    await api('/courses',json('POST',{course_id:id,name}));await loadCourses();
    openCourseMaterials(id);event.target.reset();
    notice('课程已创建，接下来可以上传讲义。',{target:'#upload-title',tone:'success'});
  }catch(error){notice(error.message,{target:'#create-course',tone:'error'});}
  finally{button.disabled=false;}
};
function documentCard(id){return Array.from($('#documents').children).find(card=>card.dataset.documentId===id);}
let catalogReview=null,catalogRevision=null;
async function showCatalogReview(documentId){
  const result=await api(`/documents/${encodeURIComponent(documentId)}/catalog-review`);
  catalogReview={documentId,versionId:result.version_id};
  $('#catalog-review-summary').textContent=`识别 ${result.series_count} 个课程系列、${result.course_count} 门课程。${result.status==='needs_review'?'当前结果尚未启用。':'当前结果已启用。'}`;
  const issues=$('#catalog-review-issues');issues.replaceChildren();
  for(const issue of result.issues)issues.append(node('p',issue,'catalog-review-warning'));
  const records=$('#catalog-review-records');records.replaceChildren();
  const appendCourse=(container,course)=>container.append(node('p',`${course.name} · ${course.course_id} · ${sourceLabel(course.source)}${course.review_status==='needs_review'?' · 待确认':''}`));
  for(const series of result.series){
    const details=node('details');details.append(node('summary',`${series.name} · ${sourceLabel(series.source)}`));
    for(const course of result.courses.filter(course=>course.series_id===series.series_id))appendCourse(details,course);
    records.append(details);
  }
  for(const course of result.courses.filter(course=>!course.series_id))appendCourse(records,course);
  const source=node('a','下载本次识别对应的原文 ↗');source.href=`/api/v1/documents/${encodeURIComponent(documentId)}/source?version_id=${encodeURIComponent(result.version_id)}`;source.target='_blank';records.prepend(source);
  $('#catalog-review-confirm').hidden=result.status!=='needs_review';
  $('#catalog-review-panel').hidden=false;$('#catalog-review-panel').scrollIntoView({block:'start',behavior:'smooth'});
}
$('#catalog-review-close').onclick=()=>{$('#catalog-review-panel').hidden=true;catalogReview=null;};
$('#catalog-review-confirm').onclick=async()=>{
  if(!catalogReview)return;
  const button=$('#catalog-review-confirm'),review=catalogReview;button.disabled=true;
  try{
    await api(`/documents/${encodeURIComponent(review.documentId)}/catalog-review`,json('POST',{version_id:review.versionId}));
    $('#catalog-review-panel').hidden=true;catalogReview=null;await loadCourses();await loadManage();
    notice('已启用确认后的目录结构。',{target:'#refresh-manage',tone:'success'});
  }catch(error){notice(error.message,{target:'#catalog-review-panel',tone:'error'});}finally{button.disabled=false;}
};
let manageRequest=0;
async function loadManage({preserveDocumentFocus=false}={}){
  const request=++manageRequest;
  const [documents,tasks]=await Promise.all([api('/documents?limit=200'),api('/import-tasks?limit=50')]);
  if(request!==manageRequest)return;
  const revision=JSON.stringify(documents.items.filter(doc=>doc.content_type==='catalog').map(doc=>[doc.id,doc.active_version]));
  if(catalogRevision!==null&&catalogRevision!==revision)await loadCourses();
  catalogRevision=revision;
  if(request!==manageRequest)return;
  renderTasks(tasks);
  await refreshUploadProgress(tasks);
  if(request!==manageRequest)return;
  // 保留正在操作的课程选择和文档按钮；任务刷新不受文档焦点影响。
  if(preserveDocumentFocus&&document.activeElement?.closest('#documents'))return;
  const targets=new Map(Array.from(document.querySelectorAll('.document-course-target'),select=>[select.dataset.documentId,select.value]));
  const feedbackDocument=noticeBox.closest('#documents>.item')?.dataset.documentId;
  const feedbackField=noticeField?.dataset.documentId;
  $('#documents').replaceChildren();
  for(const doc of documents.items){
    const card=node('article',undefined,'item'),actions=node('div',undefined,'actions');
    card.dataset.documentId=doc.id;
    const attribution=doc.content_type==='catalog'?'课程目录':state.courses.find(course=>course.course_id===doc.course_id)?.name||(doc.project_id?'项目 · '+doc.project_id:doc.course_id?'课程资料':'未指定归属');
    card.append(node('strong',doc.source_name),node('p',`${names[doc.status]||doc.status} · ${attribution}`,'muted'));
    if(doc.source_path)card.append(node('p',doc.source_path,'muted'));for(const warning of doc.warnings||[])card.append(node('p',warning,'muted'));
    const link=node('a','下载原件');link.href=`/api/v1/documents/${doc.id}/source`;actions.append(link);
    if(doc.content_type==='catalog'&&(doc.catalog_review_available||tasks.items.some(task=>task.document_id===doc.id&&task.catalog_report)))actions.append(act('查看目录识别结果',async()=>showCatalogReview(doc.id)));
    if(doc.content_type==='catalog')actions.append(act('识别目录结构',async button=>{
      const task=await api(`/documents/${encodeURIComponent(doc.id)}/recognize-catalog`,{method:'POST'});
      button.blur();
      await loadManage();notice(task.reused?'此目录已有相同配置的识别任务，可查看结果或重试失败任务。':'已提交模型识别任务，完成后可查看目录结构。',{target:documentCard(doc.id),tone:'success'});
    }));
    if(doc.content_type!=='catalog'){
      const move=node('div',undefined,'document-move'),destination=node('select');
      destination.setAttribute('aria-label','目标课程：'+doc.source_name);
      destination.className='document-course-target';destination.dataset.documentId=doc.id;
      destination.append(node('option','选择目标课程'));destination.firstChild.value='';
      for(const course of state.courses){const option=node('option',course.name);option.value=course.course_id;destination.append(option);}
      destination.value=targets.get(doc.id)||'';
      move.append(destination,act(doc.status==='needs_review'?'指定课程并开始导入':'移至所选课程',async button=>{
        if(!destination.value){notice('请先选择目标课程，再移动这份资料。',{target:button,tone:'warning',field:destination});return;}
        if(doc.course_id===destination.value&&!doc.project_id&&doc.status!=='needs_review'){
          notice('这份资料已属于所选课程，无需重复移动。',{target:button,tone:'warning',field:destination});return;
        }
        if(!confirm(`将“${doc.source_name}”归入“${state.courses.find(course=>course.course_id===destination.value)?.name||destination.value}”并重建索引？`))return;
        const task=await api(`/documents/${doc.id}`,json('PATCH',{course_id:destination.value,project_id:null}));
        await loadManage();
        notice(task.reused?'归属未变，已复用原任务。':'已提交归属变更，正在为目标课程重新建立索引。',{target:documentCard(doc.id),tone:'success'});
      }));
      actions.append(move);
      if(feedbackField===doc.id&&!noticeBox.hidden)markNoticeField(destination);
    }
    actions.append(act('上传新版文件',async button=>{
      const input=node('input');input.type='file';input.accept=(uploadLimits?.supported_extensions||['.md','.docx','.pdf','.png','.jpg']).join(',');
      input.onchange=async()=>{
        if(!input.files.length)return;
        try{
          const data=new FormData();data.set('file',input.files[0]);
          const task=await api(`/documents/${doc.id}/versions`,{method:'POST',body:data});
          await loadManage();
          notice(task.reused?'新版内容与当前版本相同，已复用。':'已提交新版；构建完成后切换，失败则继续使用旧版。',{target:documentCard(doc.id),tone:'success'});
        }catch(error){notice(error.message,{target:documentCard(doc.id)||button,tone:'error'});}
      };
      input.click();
    }),act('删除文档',async()=>{
      if(!confirm('删除文档及索引？会话引用快照会保留。'))return;
      await api(`/documents/${doc.id}`,{method:'DELETE'});await loadManage();
      notice('已提交文档删除任务。',{target:'#refresh-manage',tone:'success'});
    }));
    const help=doc.content_type==='catalog'?'上传新版文件：更新课程目录，完成后替换活动版本。':'移至所选课程：使用已有原件建立目标课程索引。上传新版文件：更新内容，完成后替换活动版本。';
    card.append(node('p',help,'muted operation-help'),actions);$('#documents').append(card);
    if(feedbackDocument===doc.id&&!noticeBox.hidden)actions.append(noticeBox);
  }
  if(!documents.items.length)$('#documents').append(node('p','还没有导入资料。','muted'));
}
function renderTasks(tasks){
  const feedbackTask=noticeBox.closest('#tasks>.item')?.dataset.taskId;
  $('#tasks').replaceChildren();
  for(const task of tasks.items){
    const row=node('div',undefined,'item task-row'),details=node('div',undefined,'task-details'),actions=node('div',undefined,'actions');
    row.dataset.taskId=task.id;
    const kind=task.kind==='delete'?'删除资料':task.content_type==='catalog'||task.catalog_batches?'识别课程目录':'解析并建立索引';
    let progress=`${task.progress}% · ${stages[task.stage]||task.stage}`;
    if(task.stage==='recognizing_catalog'&&task.catalog_batches){
      const completed=task.catalog_completed_batches??Math.max(0,task.catalog_batch-1);
      progress=`已完成 ${completed}/${task.catalog_batches} 批`;
      if(task.status==='processing'){
        const started=Date.parse(task.catalog_batch_started_at||task.started_at);
        const elapsed=Number.isFinite(started)?` · 本批已等待 ${Math.max(0,Math.floor((Date.now()-started)/1000))} 秒`:'';
        progress+=` · 正在识别第 ${task.catalog_batch} 批${elapsed}`;
      }
    }
    details.append(node('span',names[task.status]||task.status,'badge'),node('span',`${task.source_name||'原文档已不可用'} · ${kind} · ${progress}`));
    if(task.error)details.append(node('p',task.error));
    if(['failed','interrupted'].includes(task.status))actions.append(act('重试任务',async()=>{await api(`/import-tasks/${encodeURIComponent(task.id)}/retry`,{method:'POST'});await loadManage();}));
    const remove=act('删除任务',async()=>{
      if(!confirm('从最近的任务中删除这条记录？已导入的资料会保留。'))return;
      await api(`/import-tasks/${encodeURIComponent(task.id)}`,{method:'DELETE'});
      await loadManage();notice('任务记录已删除。',{target:'#refresh-manage',tone:'success'});
    });
    remove.setAttribute('aria-label',`删除任务：${task.source_name||'资料任务'}`);
    remove.disabled=!['completed','failed','interrupted','cancelled'].includes(task.status);
    remove.title=remove.disabled?'任务尚未结束，暂不能删除':'删除任务记录';
    actions.append(remove);row.append(details,actions);$('#tasks').append(row);
    if(feedbackTask===task.id&&!noticeBox.hidden)actions.append(noticeBox);
  }
  if(!tasks.items.length)$('#tasks').append(node('p','还没有任务记录。','muted'));
}

$('#refresh-manage').onclick=()=>loadCourses().then(()=>loadManage()).catch(error=>notice(error.message,{target:'#refresh-manage',tone:'error'}));
async function boot(){try{const capabilities=await api('/capabilities');speechInput.configure(capabilities.speech);uploadLimits=capabilities.upload_limits;syncUploadScope();$('#connection').dataset.state=capabilities.query?'ready':'pending';$('#connection').textContent=capabilities.query?'助教服务已就绪':capabilities.storage?'资料服务可用 · 模型待就绪':'基础服务未就绪';if(!capabilities.storage){notice(capabilities.message+'。请检查基础服务并重启应用。',{tone:'error'});return;}if(!capabilities.models)notice(capabilities.model_message,{tone:'warning'});await loadCourses();await Promise.all([loadSessions(),loadManage()]);}catch(error){$('#connection').dataset.state='error';$('#connection').textContent='连接失败';notice(error.message,{tone:'error'});}}
boot();

// 仅在资料页轮询，页面隐藏或上传期间暂停，避免重叠请求。
let managePolling=false;
setInterval(async()=>{if(document.hidden||$('#manage').hidden||uploadBusy||managePolling)return;managePolling=true;try{await loadManage({preserveDocumentFocus:true});}catch{}finally{managePolling=false;}},2500);
