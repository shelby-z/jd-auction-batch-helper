const $ = id => document.getElementById(id);
const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[c]));
let templates = [], project = null, validation = { errors: [], warnings: [] }, selected = '', step = 'import', revision = 0, savedRevision = 0, pendingSaves = 0, saveError = '', timer, saveQueue = Promise.resolve();
let aiConfig = { configured: false };
let auditSetup=null, progressStarted=0, progressTimer=null;
const expandedPhotos = new Set();
const photoLimit = 6;
const outputImageName = index => index < 10 ? `zt${index+1}.jpg` : `detail${String(index+1).padStart(3,'0')}.jpg`;
const commonKeys = ['拍卖具体类目','拍卖阶段','流转方式','保留价_价格','单价竞拍','处置机构','联系人','联系人电话_座机1','联系人电话_手机1','委托方名称','竞买人限制','尾款支付','付款时间限制','配送方式','委托方佣金类型','委托方佣金比例','委托方佣金封顶值'];
const baseKeys = ['拍卖业务线','拍卖具体类目','拍品名称','拍卖阶段','流转方式','起拍价','保证金金额','保留价_价格','最小加价幅度_卖家指定','处置机构','委托方名称','图片文件夹名称'];
const contactKeys = ['联系人','联系人电话_座机1','联系人电话_座机2','联系人电话_手机1','竞买人限制','尾款支付','付款时间限制','配送方式','委托方佣金类型','委托方佣金比例','委托方佣金封顶值'];
const fieldDefs = () => templates.find(t => t.id === (project?.template || $('template').value))?.fields || [];
const currentLot = () => project?.lots.find(l => l.id === selected);
const isCommon = key => project?.commonMode==='inherit'&&(Object.hasOwn(project.commonFields||{},key)||Object.hasOwn(project.priceRates||{},key));
function commonIndicators(lot) {
  for(const el of $('lot-fields').querySelectorAll('[data-key]')){
    const key=el.dataset.key,button=el.parentElement.querySelector('[data-inherit]');
    if(!isCommon(key))continue;
    const separate=lot.commonOverrides?.includes(key)||lot.sources[key]==='人工核对';
    el.parentElement.querySelector('.source').textContent=separate?'本标的单独设置':lot.sources[key]||'沿用项目统一设置';
    if(button)button.hidden=!separate;
  }
}
function userError(error) {
  const message=String(error?.message||error||'');
  if(/Failed to fetch|fail to fetch|NetworkError|Load failed|fetch failed/i.test(message))return '网页连接不到本机资料助手。请重新双击“资料助手.exe”，使用新打开的网页；如仍失败，请检查代理是否接管本机连接。';
  if(/AbortError|aborted|timeout/i.test(message))return '连接等待超时或已中断，请稍后重试。';
  return /[\u4e00-\u9fff]/.test(message)?message:'操作未完成，请重试；如仍失败，请复制错误信息反馈。';
}
function status(message, error = false) {
  $('status').textContent = error?userError(message):message; $('status').className = error ? 'error' : 'success';
  if(!error&&document.body.classList.contains('busy'))updateProgress({message});
  let button=$('copy-error');
  if(!button){button=document.createElement('button');button.id='copy-error';button.className='text-button';button.textContent='复制错误信息';$('status').after(button);}
  button.hidden=!error;
  if(error){const details=`时间：${new Date().toLocaleString()}\n页面：${location.origin}\n错误：${$('status').textContent}`;button.onclick=async()=>{try{await navigator.clipboard.writeText(details);button.textContent='已复制，可发给维护人员';}catch{button.textContent='复制失败，请选中上方错误文字复制';}};button.textContent='复制错误信息';}
}
async function api(path, body, binary = false) {
  let response;
  try { response = await fetch(path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': binary ? 'application/octet-stream' : 'application/json' }, body: binary ? body : JSON.stringify(body) }); }
  catch(error){const failure=new Error(userError(error));failure.network=true;throw failure;}
  let data; try { data = await response.json(); } catch { throw new Error('服务没有返回有效结果，请确认本地工具仍在运行。'); }
  if (!response.ok) throw new Error(userError(data.error || '操作失败，请重试。'));
  return data;
}
async function busy(work) {
  if (document.body.classList.contains('busy')) return;
  document.body.classList.add('busy');
  const main = document.querySelector('main'); main.setAttribute('aria-busy','true');
  progressStarted=Date.now();updateProgress({message:'正在准备处理',percent:0,remainingSeconds:null});
  $('progress-request').hidden=true;$('progress-request').dataset.started='';
  const opening=setTimeout(()=>{if(!$('progress-dialog').open)$('progress-dialog').showModal();},250);
  progressTimer=setInterval(()=>{$('progress-elapsed').textContent=`已用时 ${Math.floor((Date.now()-progressStarted)/1000)} 秒`;
    const request=$('progress-request');if(request.dataset.started)request.textContent=`第 ${request.dataset.count} 次 AI 请求 · 已等待 ${Math.max(0,Math.floor((Date.now()-Number(request.dataset.started))/1000))} 秒`;
    if(Date.now()-Number($('progress-time').dataset.updated||0)>15000)$('progress-time').textContent='本阶段预计剩余：正在重新估算';},1000);
  try { await work(); } catch (e) { status(e.message, true); }
  finally { clearTimeout(opening);clearInterval(progressTimer);$('progress-dialog').close();document.body.classList.remove('busy'); main.removeAttribute('aria-busy'); }
}
function updateProgress(event) {
  if(event.message)$('progress-message').textContent=event.message;
  if(Number.isFinite(event.percent)){$('progress-bar').value=event.percent;$('progress-percent').textContent=`${Math.round(event.percent)}%`;}
  if(Object.hasOwn(event,'remainingSeconds')){const updated=event.progressAt?event.progressAt*1000:Date.now();$('progress-time').dataset.updated=String(updated);$('progress-time').textContent=Number.isFinite(event.remainingSeconds)&&Date.now()-updated<15000?`本阶段预计剩余：约 ${Math.max(1,event.remainingSeconds)} 秒`:'本阶段预计剩余：正在估算';}
  if(event.requestCount){const request=$('progress-request');request.hidden=false;request.dataset.count=String(event.requestCount);request.dataset.started=event.requestStartedAt?String(event.requestStartedAt*1000):'';request.textContent=event.requestStartedAt?`第 ${event.requestCount} 次 AI 请求 · 正在等待模型返回`:`第 ${event.requestCount} 次 AI 请求完成 · 耗时 ${event.lastRequestSeconds??0} 秒`;}
}
$('progress-dialog').addEventListener('cancel',event=>event.preventDefault());
function saveState() {
  $('save-state').textContent = saveError ? '尚未保存，请重试' : pendingSaves ? '正在保存…' : revision > savedRevision ? '有未保存的修改' : '已保存在本机';
  $('retry-save').hidden = !saveError;
}
function changed() { revision++; project.reviewed = false; $('reviewed').checked = false; $('download-result').hidden = true; saveState(); clearTimeout(timer); timer = setTimeout(() => persist().catch(e => status('保存失败，修改仍在页面中：'+e.message, true)), 500); }
async function persist() {
  clearTimeout(timer); timer = null;
  if (!project) return;
  const id = project.id, rev = revision;
  const payload = structuredClone({ structureRevision:project.structureRevision||0, name: $('project-name').value || project.name, lots: project.lots.map(l=>({...l,editedFields:l.editedFields||[]})), reviewed: project.reviewed, ignored: project.files.filter(f => f.ignored).map(f => f.id) });
  pendingSaves++; saveState();
  saveQueue = saveQueue.catch(() => {}).then(async () => {
    let result;
    try {
      result = await api(`/api/projects/${id}/review`, payload);
      if (project?.id === id) { savedRevision = Math.max(savedRevision, rev); saveError = ''; }
    } catch (e) { if (project?.id === id) saveError = e.message; throw e; }
    finally { pendingSaves--; saveState(); }
    if (project?.id === id && revision === rev) {
      for (const lot of project.lots) {
        const saved = result.project.lots.find(l => l.id === lot.id);
        if (saved) {
          lot.sources=saved.sources;lot.conflicts=saved.conflicts;lot.commonOverrides=saved.commonOverrides;delete lot.inheritFields;delete lot.editedFields;
          for(const [key,value] of Object.entries(saved.fields)){
            if(lot.fields[key]!==value&&lot.id===selected){const el=[...$('lot-fields').querySelectorAll('[data-key]')].find(el=>el.dataset.key===key);if(el)el.value=value;}
          }
          lot.fields=saved.fields;if(lot.id===selected)commonIndicators(lot);
        }
      }
      validation = result.validation;
      renderLots(); renderValidation();
    }
  });
  return saveQueue;
}
function accept(result) { project = result.project; validation = result.validation; localStorage.setItem('jd-batch-project', project.id); $('project-name').value = project.name; $('template').value = project.template; $('template').disabled = true; revision++; savedRevision = revision; saveError = ''; saveState(); render(); }
async function ensureProject() {
  if (!project) { accept(await api('/api/projects', { name: $('project-name').value || '未命名项目', template: $('template').value })); await recent(); }
}
async function recent() {
  const list = await api('/api/projects');
  $('recent').innerHTML = '<option value="">选择已保存的项目</option>' + list.map(p => `<option value="${p.id}">${escape(p.name)} · ${p.count} 个标的</option>`).join('');
  if (project) $('recent').value = project.id;
}
async function showStep(value) {
  if (value !== 'import') { await ensureProject(); await persist(); }
  step = value;
  for (const name of ['import','review','export','audit']) $(`${name}-panel`).hidden = name !== value;
  document.querySelectorAll('nav [data-step]').forEach(b => { if (b.dataset.step === value) b.setAttribute('aria-current','step'); else b.removeAttribute('aria-current'); });
  render(); window.scrollTo({ top: 0, behavior: 'instant' });
  if(value==='audit'){
    if(auditSetup?.projectId!==project.id){auditSetup=null;$('audit-setup').hidden=true;$('audit-result').replaceChildren();$('audit-file').value='';$('audit-password').value='';}
    if(project.lastAudit)renderAudit(await api(`/api/projects/${project.id}/audits/${project.lastAudit}`));
  }
}
function canRetryRead(value) {
  const documents=(value?.files||[]).filter(f=>f.kind==='document');
  return Boolean(value?.analyzed && !value.lots.length && documents.some(f=>f.status==='读取失败') && !documents.some(f=>(f.text||'').trim()));
}
function render() {
  renderFiles(); renderLots(); renderEditor(); renderUnassigned(); renderValidation();
  $('reviewed').checked = project?.reviewed || false;
  $('analyze').disabled = !project?.files.length || (project.analyzed && !canRetryRead(project)) || project.lots.length > 0;
  $('analyze').textContent = canRetryRead(project) ? '重新读取资料并整理标的' : '读取资料并整理标的';
  $('choose-files').disabled = Boolean(project?.analyzed || project?.lots.length);
  $('choose-folder').disabled = $('choose-files').disabled;
  $('review-subtitle').textContent = `${project?.lots.length || 0} 个标的 · 空白必填项和来源冲突会提示补充`;
  $('ai-review').disabled = !project?.files.length;
  const common=[];
  if(project?.priceRates?.['最小加价幅度_卖家指定']){
    if(project.incrementOptimization?.multipleFive)common.push('加价幅度取最接近的 5 的倍数');
    else if(project.incrementOptimization?.roundInteger)common.push('加价幅度四舍五入到整数元');
    if(project.incrementMinimumEnabled)common.push(`最低加价幅度：${project.incrementMinimum} 元（阈值优先）`);
  }
  $('audit-project').textContent=project?`当前原始项目：${project.name}（${project.lots.length} 个整理结果）`:'请先选择原始项目';
  for(const [key,label] of [['保证金金额','保证金'],['最小加价幅度_卖家指定','加价幅度']]){
    if(project?.priceRates?.[key])common.push(`${label}：起拍价 × ${project.priceRates[key]}%${key==='保证金金额'&&project.depositMinimum?`，最低 ${project.depositMinimum} 元，不超过起拍价`:''}`);
    else if(project?.commonFields?.[key])common.push(`${label}：${project.commonFields[key]} 元`);
  }
  const count=Object.keys(project?.commonFields||{}).filter(k=>!['保证金金额','最小加价幅度_卖家指定'].includes(k)).length;
  if(count)common.push(`${count} 项其他统一字段`);
  $('common-summary').textContent=common.length?common.join('；')+'。单独修改的标的保留差异。':'填写一次，所有标的默认沿用；有差异时，在对应标的中单独修改。';
  const plan=project?.classification;
  $('classification-result').hidden=!plan;
  if(plan){
    const grouped=project.lots.filter(l=>(l.grouping?.codes.length||0)>1).length;
    const excluded=[...new Map((plan.excluded||[]).map(item=>[item.code,item])).values()];
    $('classification-result').innerHTML=`<strong>识别到 ${project.lots.length} 个拟上拍标的，${excluded.length} 个已排除。</strong><p>${plan.aiRecognized?'AI 已识别分类表并核验分组。':'已按分类表整理。'}${grouped?`其中 ${grouped} 个为合并上拍。`:''}${excluded.length?'鉴定不符合或明确不上拍的标的已排除，原始资料保留，不进入上拍包。':''}</p>${excluded.length?`<details><summary>查看 ${excluded.length} 个已排除标的及依据</summary>${excluded.map(item=>`<p><b>${escape(item.code)}</b> · ${escape(item.reason||'鉴定不符合')}<br>依据：${escape(item.source)}</p>`).join('')}</details>`:''}`;
  }
  const ai = project?.ai;
  $('ai-result').hidden = !ai;
  if (ai) {
    const completed = ai.status === 'completed';
    $('ai-result').innerHTML = `<strong>${completed ? 'AI 识别与复核已完成' : 'AI 尚未完成全部资料'}</strong><p>${completed ? `处理 ${ai.stats.completed}/${ai.stats.total} 项，补充 ${ai.stats.filled} 个字段，新增 ${ai.stats.created} 个标的。` : escape(userError(ai.message || '上次任务中断，可以重新复核。'))}</p>${ai.stats.requests?`<p>AI 请求 ${ai.stats.requests} 次，累计等待 ${ai.stats.requestSeconds} 秒。</p>`:''}${ai.files?.length?`<details><summary>查看逐文件处理结果</summary>${ai.files.map(f=>`<p><b>${escape(f.status)}</b> · ${escape(f.name)}<br>${escape(f.message||'已返回结构化结果并检查原文依据')}</p>`).join('')}</details>`:''}${project.aiIssues?.length ? `<details><summary>${project.aiIssues.length} 条识别提示</summary>${project.aiIssues.map(x=>`<p>${escape(userError(x))}</p>`).join('')}</details>` : ''}`;
  }
  $('source-texts').innerHTML = (project?.files || []).map(f => `<details class="source-file"><summary>${escape(f.name)} · ${escape(f.status)}</summary>${(f.notes || []).map(n => `<p class="danger">${escape(n)}</p>`).join('')}<pre>${escape(f.text || '未提取到文字，可参考原件补填。')}</pre></details>`).join('');
  const issues = (project?.files || []).filter(f => f.status === '读取失败' || (project.analyzed && f.kind === 'document' && (f.status === '未提取文字' || f.notes?.length)));
  for (const id of ['reading-notice','export-reading-notice']) {
    const notice = $(id); notice.hidden = !issues.length;
    notice.innerHTML = issues.length ? `<strong>${issues.length} 份资料未完整读取</strong><p>请对照原件补充，避免遗漏标的或字段。${issues.slice(0,3).map(f=>escape(f.name)).join('、')}${issues.length>3?'等':''}</p><button class="text-button">查看读取问题</button>` : '';
    if (issues.length) notice.querySelector('button').onclick = () => busy(async () => {
      await showStep('review'); $('source-details').open = true;
      for (const [i,detail] of [...$('source-texts').querySelectorAll('details')].entries()) if (issues.some(f => f.id === project.files[i].id)) detail.open = true;
      $('source-details').scrollIntoView({ block: 'start' });
    });
  }
}
function renderFiles() {
  const files = project?.files || [];
  $('file-summary').textContent = files.length ? `已保存 ${files.length} 个文件 · ${(files.reduce((n,f)=>n+f.size,0)/1048576).toFixed(1)} MB` : '';
  $('files').innerHTML = files.map(f => `<div class="file-row"><span>${escape(f.name)}${f.status==='读取失败'?(f.notes||[]).map(n=>`<br><small class="danger">${escape(n)}</small>`).join(''):''}</span><small>${escape(f.status)} · ${(f.size/1024).toFixed(0)} KB</small></div>`).join('');
}
function renderLots() {
  if (project?.lots.length && !project.lots.some(l => l.id === selected)) selected = project.lots[0].id;
  $('lots').innerHTML = (project?.lots || []).map((l,i) => {
    const count = validation.errors.filter(e=>e.lot===l.id).length;
    return `<button class="lot-button ${l.id===selected?'active':''}" data-lot="${l.id}"><strong>${String(i+1).padStart(2,'0')}　${escape(l.fields['拍品名称'] || '未命名标的')}</strong><small>${l.imageIds.length} 张图 · ${count ? count+' 项待补 / 核对' : '字段已齐'}</small></button>`;
  }).join('');
  $('lots').querySelectorAll('[data-lot]').forEach(b => b.onclick = () => { selected=b.dataset.lot; renderLots(); renderEditor(); });
}
function fieldMarkup(def, value, source, prefix = 'field') {
  const id = `${prefix}-${def.column}`;
  const control = def.options.length ? `<select id="${id}" data-key="${escape(def.key)}"><option value="">请选择</option>${def.options.map(o=>`<option ${o===value?'selected':''}>${escape(o)}</option>`).join('')}${value && !def.options.includes(value) ? `<option selected>${escape(value)}</option>` : ''}</select>` : `<input id="${id}" data-key="${escape(def.key)}" value="${escape(value)}" ${['拍品名称','委托方名称'].includes(def.key)?'maxlength="50"':''}>`;
  return `<div class="field"><label for="${id}">${escape(def.key)}${def.required?'<span class="required-mark">*</span>':''}</label>${control}<small class="source" title="${escape(def.hint)}">${escape(source || def.hint)}</small></div>`;
}
function renderEditor() {
  const lot = currentLot(); $('empty-lot').hidden = !!lot; $('lot-editor').hidden = !lot;
  if (!lot) return;
  $('lot-title').textContent = lot.fields['拍品名称'] || '未命名标的';
  $('grouping-detail').hidden=!lot.grouping;
  $('grouping-detail').textContent=lot.grouping?`包含素材：${lot.grouping.codes.join('、')}；图片目录：${lot.fields['图片文件夹名称']}；共 ${lot.imageIds.length} 张图片。`:'';
  const groups = [['标的与价格',baseKeys],['联系人与交易条件',contactKeys],['租赁、单价竞拍与其他设置',fieldDefs().map(f=>f.key).filter(k=>!baseKeys.includes(k)&&!contactKeys.includes(k))]];
  $('lot-fields').innerHTML = groups.map(([name,keys],i)=>`${i===2?'<details class="field-group"><summary>'+name+'</summary>':'<section class="field-group"><h3>'+name+'</h3>'}<div class="form-grid">${keys.map(k=>fieldDefs().find(f=>f.key===k)).filter(Boolean).map(f=>fieldMarkup(f,lot.fields[f.key]||'',lot.sources[f.key])).join('')}</div>${i===2?'</details>':'</section>'}`).join('');
  for (const el of $('lot-fields').querySelectorAll('[data-key]')) {
    const key = el.dataset.key;
    if (validation.errors.some(e=>e.lot===lot.id && e.field===key)) el.classList.add('invalid');
    if (lot.conflicts[key]) {
      const note=document.createElement('p'); note.className='conflict'; note.textContent='来源冲突：'+lot.conflicts[key].map(x=>`${x.value}（${x.source}）`).join(' / ')+'。请填写核对后的值。'; el.parentElement.append(note);
    }
    if(isCommon(key)){
      const restore=document.createElement('button');restore.type='button';restore.className='text-button inherit-button';restore.dataset.inherit=key;restore.textContent='恢复统一设置';
      restore.onclick=()=>busy(async()=>{lot.inheritFields=[key];changed();await persist();renderEditor();status('该字段已恢复项目统一设置。');});el.parentElement.append(restore);
    }
    el.oninput = () => { lot.fields[key]=el.value;lot.editedFields=[...new Set([...(lot.editedFields||[]),key])]; lot.sources[key]='人工核对'; delete lot.conflicts[key]; el.classList.remove('invalid'); el.parentElement.querySelector('.conflict')?.remove();if(isCommon(key)){lot.commonOverrides=[...new Set([...(lot.commonOverrides||[]),key])];commonIndicators(lot);} changed(); if(key==='拍品名称'){ $('lot-title').textContent=el.value || '未命名标的'; renderLots(); } };
  }
  commonIndicators(lot);
  const expanded=expandedPhotos.has(lot.id),visible=expanded?lot.imageIds:lot.imageIds.slice(0,photoLimit);
  $('image-count').textContent=`共 ${lot.imageIds.length} 张 · 导出时自动重命名`;
  $('lot-images').innerHTML = visible.map((id,i)=>{
    const f=project.files.find(f=>f.id===id);
    return `<div class="photo ${i===0?'main-photo':''}"><img src="/api/projects/${project.id}/preview/${id}" alt="${escape(f?.name)}" loading="lazy"><p><strong>${i===0?'主图 · ':''}${outputImageName(i)}</strong></p><p class="original-name" title="${escape(f?.name)}">原文件：${escape(f?.name.split('/').pop())}</p><button data-main="${id}" ${i===0?'disabled':''}>${i===0?'当前主图':'设为主图'}</button><details class="photo-actions"><summary>排序 / 移出</summary><div class="actions"><button data-move="${i}" data-direction="-1" ${i===0?'disabled':''}>前移</button><button data-move="${i}" data-direction="1" ${i===lot.imageIds.length-1?'disabled':''}>后移</button><button data-unassign="${id}">移出</button></div></details></div>`;
  }).join('') || '<p class="hint">从下方待分配区选择此标的的图片。</p>';
  $('lot-images-more').hidden=lot.imageIds.length<=photoLimit;
  $('lot-images-more').textContent=expanded?'收起，只看前 6 张':`展开其余 ${lot.imageIds.length-photoLimit} 张图片`;
  $('lot-images-more').setAttribute('aria-expanded',String(expanded));
  $('lot-images-more').onclick=()=>{expanded?expandedPhotos.delete(lot.id):expandedPhotos.add(lot.id);renderEditor();if(expanded)$('image-count').scrollIntoView({block:'center'});};
  $('lot-images').querySelectorAll('[data-main]').forEach(b=>b.onclick=()=>{lot.imageIds=[b.dataset.main,...lot.imageIds.filter(id=>id!==b.dataset.main)];expandedPhotos.delete(lot.id);changed();renderEditor();$('image-count').scrollIntoView({block:'center'});status('主图已选定，导出时将命名为 zt1.jpg，其余图片依次重命名。');});
  $('lot-images').querySelectorAll('[data-move]').forEach(b=>b.onclick=()=>{const i=Number(b.dataset.move),j=i+Number(b.dataset.direction); [lot.imageIds[i],lot.imageIds[j]]=[lot.imageIds[j],lot.imageIds[i]]; changed(); renderEditor(); });
  $('lot-images').querySelectorAll('[data-unassign]').forEach(b=>b.onclick=()=>{lot.imageIds=lot.imageIds.filter(id=>id!==b.dataset.unassign); changed(); renderEditor(); renderUnassigned(); renderLots(); });
}
function renderUnassigned() {
  const used=new Set(project?.lots.flatMap(l=>l.imageIds) || []);
  const files=(project?.files || []).filter(f=>f.kind==='image' && !used.has(f.id));
  const pending=files.filter(f=>!f.ignored),ignored=files.filter(f=>f.ignored);
  $('unassigned-count').textContent=pending.length+' 张待分配';
  $('unassigned-active').hidden=!pending.length;
  $('ignored-details').hidden=!ignored.length;
  $('ignored-summary').textContent=`不上拍的资料图片（${ignored.length} 张）`;
  function gallery(id,items,show=true){
    const expanded=expandedPhotos.has(id),visible=show?(expanded?items:items.slice(0,photoLimit)):[];
    $(id+'-images').innerHTML=visible.map(f=>`<div class="photo"><img src="/api/projects/${project.id}/preview/${f.id}" alt="${escape(f.name)}" loading="lazy"><p class="original-name" title="${escape(f.name)}">${escape(f.name.split('/').pop())}</p><label>图片用途<select data-image="${f.id}"><option value="">待分配</option><option value="ignore" ${f.ignored?'selected':''}>资料图片，不上拍</option>${project.lots.map(l=>`<option value="${l.id}">${escape(l.fields['拍品名称']||'未命名标的')}</option>`).join('')}</select></label></div>`).join('');
    $(id+'-more').hidden=!show||items.length<=photoLimit;
    $(id+'-more').textContent=expanded?'收起，只看前 6 张':`展开其余 ${items.length-photoLimit} 张图片`;
    $(id+'-more').setAttribute('aria-expanded',String(expanded));
    $(id+'-more').onclick=()=>{expanded?expandedPhotos.delete(id):expandedPhotos.add(id);renderUnassigned();};
    $(id+'-images').querySelectorAll('[data-image]').forEach(s=>s.onchange=()=>{const f=project.files.find(f=>f.id===s.dataset.image); f.ignored=s.value==='ignore'; const lot=project.lots.find(l=>l.id===s.value); if(lot)lot.imageIds.push(f.id); changed(); renderEditor(); renderUnassigned(); renderLots(); });
  }
  gallery('unassigned',pending);gallery('ignored',ignored,$('ignored-details').open);
}
$('ignored-details').ontoggle=()=>renderUnassigned();
function renderValidation() {
  $('validation').innerHTML=validation.errors.length ? `<h3>${validation.errors.length} 项需要补充或核对</h3><ul class="validation-list">${validation.errors.map((e,i)=>`<li><button data-error="${i}">${escape(e.message)}</button></li>`).join('')}</ul>` : '<h3 class="pass">本地检查通过，可以生成上拍包</h3><p class="hint">京东后台解析结果仍需上传后确认。</p>';
  $('validation').innerHTML += validation.warnings.map(w=>`<p class="hint">${escape(w)}</p>`).join('');
  $('validation').querySelectorAll('[data-error]').forEach(b=>b.onclick=()=>busy(async()=>{const err=validation.errors[Number(b.dataset.error)]; if(err.field==='核对'){$('reviewed').focus();return;} if(err.lot)selected=err.lot; await showStep('review'); const def=fieldDefs().find(f=>f.key===err.field); if(def){const el=$('field-'+def.column); if(el){const d=el.closest('details');if(d)d.open=true;el.focus();}} else $('unassigned-images').scrollIntoView({block:'center'}); }));
  $('export-ready').disabled=validation.errors.length>0;
  $('package-tree').textContent='项目批量上拍_时间编号.zip\n├─ 项目批量上拍_时间编号.xlsx\n└─ 图片/\n'+(project?.lots||[]).map(l=>`    └─ ${l.fields['图片文件夹名称']||'待填写目录名'}/\n        ${l.imageIds.length?'zt1.jpg'+(l.imageIds.length>1?' …':''):'（待分配图片）'}`).join('\n');
}
async function upload(files) {
  if (project?.analyzed || project?.lots.length) throw new Error('本项目已进入核对，请新建项目后导入另一批资料。');
  await ensureProject();
  const failures=[];
  for (let i=0;i<files.length;i++) {
    const {file,name}=files[i]; status(`正在保存 ${i+1}/${files.length}：${name}`);
    try { const response=await uploadFile(`/api/projects/${project.id}/files?async=1&name=${encodeURIComponent(name)}`,file,(message,percent)=>{status(`上传 ${i+1}/${files.length}：${name} · ${message}`);updateProgress({percent,remainingSeconds:null});});
      if(response.job)await trackJob(response.job);else accept(response); }
    catch(e){failures.push(`${name}：${e.message}`);}
  }
  status(failures.length ? `部分文件未导入：${failures.join('；')}` : `已保存 ${files.length} 份资料，可以开始读取。`, failures.length>0);
}
async function entryFiles(entry,prefix='') {
  if(entry.isFile) return [{file:await new Promise((r,j)=>entry.file(r,j)),name:prefix+entry.name}];
  if(!entry.isDirectory)return [];
  const reader=entry.createReader(); let entries=[],batch;
  do{batch=await new Promise((r,j)=>reader.readEntries(r,j));entries.push(...batch);}while(batch.length);
  const all=[];for(const e of entries)all.push(...await entryFiles(e,prefix+entry.name+'/'));return all;
}
$('choose-files').onclick=()=>$('file-input').click(); $('choose-folder').onclick=()=>$('folder-input').click();
for(const id of ['file-input','folder-input'])$(id).onchange=()=>busy(async()=>{await upload([...$(id).files].map(file=>({file,name:file.webkitRelativePath||file.name})));$(id).value='';});
const drop=$('dropzone');drop.ondragover=e=>{e.preventDefault();drop.classList.add('drag');};drop.ondragleave=()=>drop.classList.remove('drag');
drop.ondrop=e=>{e.preventDefault();drop.classList.remove('drag');const entries=[...e.dataTransfer.items].map(i=>i.webkitGetAsEntry?.()).filter(Boolean);const fallback=[...e.dataTransfer.files];busy(async()=>{let files=[];if(entries.length)for(const entry of entries)files.push(...await entryFiles(entry));else files=fallback.map(file=>({file,name:file.name}));await upload(files);});};
$('analyze').onclick=()=>busy(()=>runAnalysis($('use-ai').checked));
$('ai-review').onclick=()=>busy(()=>runAnalysis(true));
document.querySelectorAll('[data-step]').forEach(b=>b.onclick=()=>busy(()=>showStep(b.dataset.step)));
$('go-export').onclick=()=>busy(()=>showStep('export'));
$('new-project').onclick=()=>busy(async()=>{await persist();project=null;selected='';validation={errors:[],warnings:[]};$('project-name').value='';$('template').disabled=false;$('recent').value='';localStorage.removeItem('jd-batch-project');$('download-result').hidden=true;await showStep('import');status('新项目已准备好。之前的项目保留在“继续上次的整理”中。');});
$('recent').onchange=()=>busy(async()=>{const id=$('recent').value;if(!id)return;await persist();accept(await api(`/api/projects/${id}`));$('download-result').hidden=true;await showStep(project.analyzed||project.lots.length?'review':'import');status('已恢复上次保存的项目。');});
$('project-name').onchange=()=>{if(project){project.name=$('project-name').value;changed();}};
$('add-lot').onclick=()=>busy(async()=>{await ensureProject();const folders=new Set(project.lots.map(l=>l.fields['图片文件夹名称']));let n=1;while(folders.has(String(n).padStart(3,'0')))n++;const lot={id:crypto.randomUUID().replaceAll('-',''),fields:{'拍卖业务线':templates.find(t=>t.id===project.template).name,'图片文件夹名称':String(n).padStart(3,'0')},sources:{},conflicts:{},imageIds:[]};project.lots.push(lot);selected=lot.id;changed();await persist();render();});
$('remove-lot').onclick=()=>busy(async()=>{if(!confirm('移除此标的？原始资料保留，已分配的照片会回到待分配区。'))return;project.lots=project.lots.filter(l=>l.id!==selected);selected='';changed();await persist();render();});
function moneyMode(prefix){const mode=$(prefix+'-mode').value,label=prefix==='deposit'?'保证金':'加价幅度';$(prefix+'-value-label').textContent=mode==='fixed'?label+'统一金额（元）':label+'占起拍价比例（%）';const input=$(prefix+'-rate');input.disabled=mode==='none';input.step=mode==='fixed'?'0.01':'0.0001';input.min=mode==='fixed'?'0.01':'0.0001';if(mode==='rate')input.max='100';else input.removeAttribute('max');if(prefix==='deposit'){$('deposit-minimum-field').hidden=mode!=='rate';$('deposit-minimum').disabled=mode!=='rate';}}
function incrementMode(){const enabled=$('increment-mode').value==='rate';$('increment-options').hidden=!enabled;$('increment-minimum').disabled=!enabled||!$('increment-minimum-enabled').checked;}
$('increment-mode').addEventListener('change',incrementMode);$('increment-minimum-enabled').onchange=incrementMode;
for(const prefix of ['deposit','increment'])$(prefix+'-mode').onchange=()=>moneyMode(prefix);
$('common-toggle').onclick=()=>{
  if(!project){status('请先导入资料。',true);return;}
  const renderFields=defs=>defs.map(f=>fieldMarkup(f,project.commonFields?.[f.key]||'','','common')).join('');
  $('common-fields').innerHTML=renderFields(commonKeys.map(k=>fieldDefs().find(f=>f.key===k)).filter(Boolean));
  $('common-extra-fields').innerHTML=renderFields(fieldDefs().filter(f=>!commonKeys.includes(f.key)&&!['拍卖业务线','拍品名称','起拍价','图片文件夹名称','保证金金额','最小加价幅度_卖家指定'].includes(f.key)));
  for(const [prefix,key] of [['deposit','保证金金额'],['increment','最小加价幅度_卖家指定']]){
    const rate=project.priceRates?.[key],fixed=project.commonFields?.[key];
    $(prefix+'-mode').value=rate?'rate':fixed?'fixed':project.commonMode==='inherit'?'none':'rate';
    $(prefix+'-rate').value=rate||fixed||(prefix==='deposit'&&project.commonMode!=='inherit'?'20':prefix==='increment'?'1':'');moneyMode(prefix);
  }
  $('deposit-minimum').value=project.depositMinimum||'';$('common-message').textContent='';$('common-extra').open=false;$('common-dialog').showModal();$('common-dialog').scrollTop=0;
  $('increment-round').checked=project.incrementOptimization?.roundInteger??true;$('increment-five').checked=project.incrementOptimization?.multipleFive??true;
  $('increment-minimum-enabled').checked=Boolean(project.incrementMinimumEnabled);$('increment-minimum').value=project.incrementMinimum||'5';incrementMode();
};
$('apply-common').onclick=()=>busy(async()=>{
  try{
    if(!$('common-dialog').querySelector('form').reportValidity())return;
    await persist();const fields=Object.fromEntries([...$('common-dialog').querySelectorAll('[data-key]')].filter(el=>el.value).map(el=>[el.dataset.key,el.value]));const rates={};
    for(const [prefix,key] of [['deposit','保证金金额'],['increment','最小加价幅度_卖家指定']]){const mode=$(prefix+'-mode').value,value=$(prefix+'-rate').value;if(mode==='rate'&&value)rates[key]=value;else if(mode==='fixed'&&value)fields[key]=value;}
    accept(await api(`/api/projects/${project.id}/common`,{fields,rates,depositMinimum:$('deposit-mode').value==='rate'?$('deposit-minimum').value:'',incrementOptimization:{roundInteger:$('increment-round').checked,multipleFive:$('increment-five').checked},incrementMinimum:$('increment-minimum').value,incrementMinimumEnabled:$('increment-mode').value==='rate'&&$('increment-minimum-enabled').checked}));
    $('common-dialog').close();$('download-result').hidden=true;status('项目统一设置已保存，所有沿用设置的标的已更新；单独修改的值已保留。');
  }catch(e){$('common-message').textContent=e.message;throw e;}
});
$('reviewed').onchange=()=>busy(async()=>{project.reviewed=$('reviewed').checked;revision++;await persist();renderValidation();});
for(const [id,draft]of[['export-ready',false],['export-draft',true]])$(id).onclick=()=>busy(async()=>{await persist();status('正在填写模板、处理图片并生成 ZIP…');const {job}=await api(`/api/projects/${project.id}/export`,{draft,async:true});await trackJob(job);});
$('retry-save').onclick = () => busy(async () => { await persist(); status('修改已保存。'); });
window.addEventListener('beforeunload',e=>{if(revision > savedRevision || pendingSaves > 0 || saveError){e.preventDefault();e.returnValue='';}});
function uploadFile(url,file,onProgress) {
  return new Promise((resolve,reject)=>{
    const xhr=new XMLHttpRequest();xhr.open('POST',url);xhr.setRequestHeader('Content-Type','application/octet-stream');
    xhr.upload.onprogress=e=>{if(e.lengthComputable)onProgress(e.loaded===e.total?'上传完成，正在准备归档…':`${Math.round(e.loaded/e.total*100)}%`,Math.min(99,Math.round(e.loaded/e.total*100)));};
    xhr.onerror=()=>reject(new Error('上传连接中断。请重新双击“资料助手.exe”，确认网页能打开后重试上传。'));
    xhr.onload=()=>{try{const data=JSON.parse(xhr.responseText);if(xhr.status>=200&&xhr.status<300)resolve(data);else reject(new Error(userError(data.error||'上传失败')));}catch{reject(new Error('上传响应异常，请查看项目后重试'));}};
    xhr.send(file);
  });
}
function applyAIConfig(config) {
  aiConfig=config;$('ai-provider').value=config.provider||'custom';$('ai-base').value=config.baseUrl||'';$('ai-model').value=config.model||'';$('ai-json').checked=Boolean(config.jsonMode);$('ai-key').value='';
  $('ai-key').placeholder=config.keyConfigured?'已保存；留空保留当前 Key':'填写你的 API Key';$('ai-config-status').textContent=config.configured?`已配置 · ${config.model}`:'未设置';$('use-ai').checked=config.configured;
}
async function saveAI() {
  const result=await api('/api/ai/config',{provider:$('ai-provider').value,baseUrl:$('ai-base').value,model:$('ai-model').value,apiKey:$('ai-key').value,jsonMode:$('ai-json').checked});
  applyAIConfig(result);$('ai-message').textContent='设置已保存，Key 不会回显。';return result;
}
$('ai-provider').onchange=()=>{const presets={deepseek:'https://api.deepseek.com',volcengine_agent:'https://ark.cn-beijing.volces.com/api/plan/v3',openai:'https://api.openai.com/v1'};if(presets[$('ai-provider').value])$('ai-base').value=presets[$('ai-provider').value];};
$('ai-save').onclick=()=>busy(async()=>{try{await saveAI();status('API 设置已保存。');}catch(e){$('ai-message').textContent=e.message;throw e;}});
$('ai-test').onclick=()=>busy(async()=>{try{await saveAI();$('ai-message').textContent='正在测试连接，不发送项目资料…';const result=await api('/api/ai/test',{});$('ai-message').textContent=result.message;status(result.message);}catch(e){$('ai-message').textContent=e.message;throw e;}});
async function runAnalysis(useAI) {
  if(useAI&&!aiConfig.configured){await showStep('import');$('ai-settings').open=true;$('ai-base').focus();throw new Error('请先填写并保存 API 设置。');}
  const savedJob=JSON.parse(localStorage.getItem('jd-batch-job')||'null');
  if(savedJob?.projectId===project?.id)return waitAnalysis(savedJob);
  await persist();status(useAI?'开始按文件逐份读取与 AI 复核…':'开始读取资料…');
  const {job}=await api(`/api/projects/${project.id}/analyze`,{ocr:$('use-ocr').checked,ai:useAI,async:true});
  localStorage.setItem('jd-batch-job',JSON.stringify(job));
  return waitAnalysis(job);
}
async function trackJob(job){localStorage.setItem('jd-batch-job',JSON.stringify(job));return waitAnalysis(job);}
function renderAuditSetup(result){
  auditSetup={...result,projectId:project.id};$('audit-setup').hidden=false;$('audit-result').replaceChildren();
  const options=(list)=>'<option value="">请选择</option>'+list.map(s=>`<option value="${escape(s.id)}">${escape(s.name)} · ${s.count} 个${s.id.match(/^\d+$/)?' · '+escape(s.id):''}</option>`).join('');
  $('audit-session').innerHTML=options(result.sessions);$('audit-source').innerHTML=options(result.sources);
  if(result.sessions.length===1){$('audit-session').value=result.sessions[0].id;const source=result.sources.find(s=>s.id===result.sessions[0].name);if(source)$('audit-source').value=source.id;}
  $('audit-notice').textContent=`原表已排除 ${result.excludedCount} 个素材。${result.duplicateRows?`已合并 ${result.duplicateRows} 行同一拍品的重复记录。`:''} ${result.issues.join('；')}`;
}
function renderAudit(result){
  const details=result.differences.concat(result.unknown);
  const summary=`<div class="ai-result"><strong>${escape(result.state)}</strong><p>${escape(result.sessionName)} · 专场 ${escape(result.sessionId)}</p><p>原始清单 ${result.expectedCount} 个 · 京东导出 ${result.actualCount} 个 · 原表排除 ${result.excludedCount} 个素材</p><p>差异 ${result.differences.length} 项 · 未核实 ${result.unknown.length} 项 · 图片复核${result.imageEnabled?'已开启':'未开启'}</p><a class="button" href="/api/projects/${project.id}/audits/${result.auditId}/report">下载完整复核记录</a></div>`;
  const issues=details.length?`<details open><summary>先看差异和未核实项（${details.length}）</summary><ul class="audit-issues">${details.map(x=>`<li>${escape(x)}</li>`).join('')}</ul></details>`:'';
  const rows=result.rows.map(row=>`<details><summary>${escape(row.state)} · ${escape(row.name)}</summary><p>${escape(row.message||'')} ${escape(row.source)}</p>${row.lotId?`<p><a href="https://paimai.jd.com/${encodeURIComponent(row.lotId)}" target="_blank" rel="noopener noreferrer">打开京东拍品 ${escape(row.lotId)}</a></p>`:''}<div class="audit-table"><table><thead><tr><th>字段</th><th>预期值</th><th>京东值</th><th>结果 / 依据</th></tr></thead><tbody>${row.checks.map(c=>`<tr><td>${escape(c.field)}</td><td>${escape(c.expected)}</td><td>${escape(c.actual)}</td><td>${escape(c.state)}<br><small>${escape(c.basis)} ${escape(c.message)}</small></td></tr>`).join('')}</tbody></table></div><p>图片：${escape(row.imageState)} ${escape(row.imageMessage||'')}</p>${(row.imageChecks||[]).map(c=>`<p class="hint">${escape(c.message)}</p>`).join('')}</details>`).join('');
  $('audit-result').innerHTML=summary+issues+`<h3>逐个拍品查看</h3>`+rows;
}
$('audit-images').onchange=()=>{$('audit-model-field').hidden=!$('audit-images').checked;};
$('audit-read').onclick=()=>busy(async()=>{
  const file=$('audit-file').files[0];if(!file)throw new Error('请先选择京东专场导出的 ZIP');
  await ensureProject();await persist();
  const {auditId}=await uploadFile(`/api/projects/${project.id}/audit-upload?name=${encodeURIComponent(file.name)}`,file,(message,percent)=>{status('专场导出包：'+message);updateProgress({percent});});
  const password=$('audit-password').value;$('audit-password').value='';
  const {job}=await api(`/api/projects/${project.id}/audit-unlock`,{auditId,password,async:true});await trackJob(job);
});
$('audit-run').onclick=()=>busy(async()=>{
  if(!auditSetup||auditSetup.projectId!==project.id)throw new Error('请先读取本项目的专场导出包');
  if(!$('audit-session').value||!$('audit-source').value)throw new Error('请选择要复核的京东专场和对应的原始表格范围');
  await persist();const {job}=await api(`/api/projects/${project.id}/audit-run`,{auditId:auditSetup.auditId,sessionId:$('audit-session').value,sourceSession:$('audit-source').value,images:$('audit-images').checked,visionModel:$('audit-model').value,async:true});await trackJob(job);
});
async function waitAnalysis(job) {
  let current=job;
  let disconnected=0;
  while(current.state==='running') {
    status(disconnected?'连接暂时中断，正在恢复进度；已保存结果保留…':current.message);
    updateProgress(current);
    await new Promise(resolve=>setTimeout(resolve,disconnected?2000:900));
    try{current=await api(`/api/jobs/${job.id}`);disconnected=0;}
    catch(error){if(error.network&&++disconnected<6)continue;if(!error.network)localStorage.removeItem('jd-batch-job');throw error;}
  }
  localStorage.removeItem('jd-batch-job');
  updateProgress(current);
  accept(await api(`/api/projects/${job.projectId}`));await recent();
  if(current.state==='failed')throw new Error(current.message+(job.kind==='analyze'||!job.kind?'；已经读出的资料和已完成批次保留，可重试 AI 复核。':''));
  if(job.kind==='files')return current.result;
  if(job.kind==='audit-unlock'){await showStep('audit');renderAuditSetup(current.result);status('导出包已读取，请确认复核范围。');return current.result;}
  if(job.kind==='audit-run'){await showStep('audit');renderAudit(current.result);status('专场复核完成：'+current.result.state);return current.result;}
  if(job.kind==='export'){
    await showStep('export');$('download-result').hidden=false;$('download-link').href=`/api/projects/${project.id}/download`;$('download-link').textContent='下载已生成的 ZIP';$('report-link').href=`/api/projects/${project.id}/report`;status('资料包已生成，请下载。');return current.result;
  }
  await showStep('review');
  status(`已整理 ${project.lots.length} 个拟上拍标的，请查看读取结果与排除提示。`);
}
await busy(async()=>{templates=(await api('/api/templates')).templates;applyAIConfig(await api('/api/ai/status'));$('ai-settings').open=!aiConfig.configured;await recent();const job=JSON.parse(localStorage.getItem('jd-batch-job')||'null');if(job){await waitAnalysis(job);return;}const last=localStorage.getItem('jd-batch-project');if(last){try{accept(await api(`/api/projects/${last}`));if(project.lots.length||project.analyzed)await showStep('review');}catch{localStorage.removeItem('jd-batch-project');}}render();});
