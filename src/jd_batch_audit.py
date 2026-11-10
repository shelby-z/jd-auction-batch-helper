"""Read-only comparison of a JD session export with retained source worksheets."""
from copy import deepcopy
from pathlib import Path, PurePosixPath
from decimal import Decimal, InvalidOperation
from datetime import datetime
import hashlib, html, json, re, unicodedata, uuid, zipfile
import jd_batch as core

ALIASES={
 'sessionId':('专场编号','专场ID'),'sessionName':('专场名称',),
 'lotId':('拍品编号','拍品ID'),'skuId':('sku编号','skuID'),
 '拍品名称':('拍品名称','标的名称'),'起拍价':('起拍价(元)','起拍价','起拍价格'),
 '保证金金额':('保证金(元)','保证金金额','保证金'),
 '处置机构':('处置机构',),'开始时间':('拍品拍卖开始时间','拍卖开始时间','开始时间'),
 '结束时间':('拍品拍卖结束时间','拍卖结束时间','结束时间'),
 '最小加价幅度_卖家指定':('加价幅度','最小加价幅度','加价幅度(元)'),
 '尾款支付':('尾款走线上/线下','尾款支付'),'配送方式':('收货方式','配送方式'),
 '预计处置数量':('处置数量','预计处置数量'),'拍品状态':('拍品状态',),'专场状态':('专场状态',),
}
def label(value):return re.sub(r'[\s*＊()（）]', '',unicodedata.normalize('NFKC',str(value or ''))).casefold()
def clean(value):
    text=str(value if value is not None else '').strip()
    return '' if text in {'--','-','暂无','无','None'} else text
def source_key(value,definitions):
    return core.field_for(value,definitions) or next((k for k,names in ALIASES.items() if k in {'开始时间','结束时间'} and label(value) in {label(x) for x in names}),'')
def title(value):
    value=unicodedata.normalize('NFKC',str(value)).casefold().strip()
    value=re.sub(r'^拟处置','',value)
    value=re.sub(r'[（(]?以实物为准[）)]?','',value)
    return re.sub(r'[\W_]+','',value)
def same(key,a,b):
    if key=='拍品名称':return title(a)==title(b)
    if key in core.MONEY or key=='预计处置数量':
        try:return Decimal(core.normalize_value(key,a))==Decimal(core.normalize_value(key,b))
        except InvalidOperation:return False
    if key=='尾款支付':
        convert={'线上':'通过京东平台','线下':'不通过京东平台'}
        return convert.get(a,a)==convert.get(b,b)
    if key in {'开始时间','结束时间'}:
        def date(value):
            try:return datetime.fromisoformat(str(value).replace('/','-').replace('年','-').replace('月','-').replace('日','')).isoformat(sep=' ')
            except ValueError:return str(value)
        return date(a)==date(b)
    return label(a)==label(b)

def folder(p,identifier):
    if not re.fullmatch(r'[a-f0-9]{32}',str(identifier)):raise ValueError('复核记录编号无效')
    return core.project_path(p['id'])/'audits'/identifier

def upload(p,path,name):
    if Path(name).suffix.lower()!='.zip' or not zipfile.is_zipfile(path):raise ValueError('请选择京东专场导出的 ZIP 压缩包')
    identifier=uuid.uuid4().hex;target=folder(p,identifier);target.mkdir(parents=True)
    path.replace(target/'export.zip')
    core.json_write(target/'meta.json',{'name':PurePosixPath(name.replace('\\','/')).name})
    return {'auditId':identifier}

def read_export(path,password='',progress=None):
    records=[];issues=[];found=False
    try:
        with zipfile.ZipFile(path) as archive:
            members=[x for x in archive.infolist() if not x.is_dir() and Path(x.filename).suffix.lower() in {'.xls','.xlsx','.csv'}]
            if not members:raise ValueError('压缩包中没有找到 XLS、XLSX 或 CSV 专场表格')
            for number,member in enumerate(members):
                # Read bytes only; no archive path is ever extracted to disk.
                if progress:progress({'message':f'正在读取专场表格 {number+1}/{len(members)}','completed':number,'total':len(members)})
                blob=archive.read(member,pwd=password.encode('utf8') if password else None)
                _,tables,notes=core.read_document(blob,member.filename,False);issues.extend(notes)
                for rows,sheet in tables:
                    for h,row in enumerate(rows):
                        mapping={i:key for i,v in enumerate(row) for key,names in ALIASES.items() if label(v) in {label(x) for x in names}}
                        if not {'sessionId','lotId','拍品名称'}<=set(mapping.values()):continue
                        found=True
                        for r,values in enumerate(rows[h+1:],h+2):
                            item={key:clean(values[i]) for i,key in mapping.items() if i<len(values)}
                            for key in core.MONEY & item.keys():item[key]=core.normalize_value(key,item[key])
                            if not any(item.get(k) for k in ('lotId','拍品名称')):continue
                            if item.get('拍品名称') in {'合计','总计'}:continue
                            if not re.fullmatch(r'\d+',item.get('lotId','')) or not re.fullmatch(r'\d+',item.get('sessionId','')):
                                issues.append(f'{sheet} 第 {r} 行的专场编号或拍品编号缺失/无效');continue
                            item['source']=f'{member.filename} · {sheet} · 第 {r} 行';records.append(item)
                        break
    except RuntimeError as error:
        raise ValueError('压缩包密码不正确或尚未填写，请重新输入') from None
    except NotImplementedError:raise ValueError('此压缩包的加密或压缩方式暂不支持，请重新从京东导出 ZIP') from None
    except zipfile.BadZipFile:raise ValueError('压缩包损坏或密码不正确，请检查后重新上传') from None
    if not found or not records:raise ValueError('未读到含专场编号、拍品编号和拍品名称的有效表格')
    unique={};duplicates=0
    for record in records:
        key=(record['sessionId'],record['lotId'])
        if key in unique:
            old=unique[key];keys=(set(record)|set(old))-{'source'}
            if any(record.get(k,'')!=old.get(k,'') for k in keys):issues.append('同一拍品 '+record['lotId']+' 的重复记录存在信息冲突')
            duplicates+=1
        else:unique[key]=record
    return {'records':list(unique.values()),'issues':list(dict.fromkeys(issues)),'duplicateRows':duplicates}

def baseline(p,progress=None):
    """Re-read source rows so removing an editor lot cannot erase the audit baseline."""
    plans=[];issues=[];excluded=[];pending=[]
    chosen=set(p.get('classification',{}).get('files',[]))
    definitions=core.templates()[p['template']]['fields']
    files=[f for f in p['files'] if (not chosen or f['id'] in chosen) and Path(f['name']).suffix.lower() in {'.xls','.xlsx','.xlsm','.csv','.docx'}]
    for index,file in enumerate(files):
        if progress:progress({'message':'正在重读原始上拍表格：'+file['name'],'phase':'reference','completed':index,'total':len(files)})
        metadata={}
        try:
            _,tables,notes=core.read_document(core.project_path(p['id'])/'files'/file['id'],file['name'],False,metadata)
            issues.extend(notes);hints={}
            groups=p.get('classification',{}).get('sourceGroups',[]) or [lot.get('grouping',{}) for lot in p['lots']]
            for group in groups:
                if group.get('file')!=file['name']:continue
                sheet=group['sheet'];rows=next((rows for rows,s in tables if s==sheet),[])
                header=group.get('headerRow')
                if not header:
                    header=next((i+1 for i,row in enumerate(rows) if any(core.classification.header(v) in core.classification.ID_HEADERS for v in row)),None)
                if not header:continue
                idcol=group.get('idColumn') or next((i+1 for i,v in enumerate(rows[header-1]) if core.classification.header(v) in core.classification.ID_HEADERS),None)
                if not idcol:continue
                hint={'headerRow':header,'id':idcol-1,'name':group['titleColumn']-1,'price':group['priceColumn']-1}
                for prop,key in [('groupColumn','group'),('sessionColumn','session')]:
                    if prop in group:hint[key]=group[prop]-1
                hints[sheet]=hint
            parsed=core.classification.parse_tables(tables,metadata,file['name'],core.normalize_value,hints or None)
            if parsed:
                for plan in parsed['plans']:
                    g=plan['grouping'];rows=next(rows for rows,s in tables if s==g['sheet'])
                    header=rows[g['headerRow']-1];row=rows[g['rows'][0]-1]
                    extras={source_key(v,definitions):core.normalize_value(source_key(v,definitions),row[i],str(v)) for i,v in enumerate(header) if i<len(row) and source_key(v,definitions) and row[i] not in ('',None)}
                    plan['fields']={**extras,**plan['fields']}
                plans+=parsed['plans'];excluded+=parsed['excluded'];pending+=parsed['pending']
            else:
                if chosen:
                    issues.append('原分类表未能重建分组：'+file['name']+'；请重新识别分类列后复核');continue
                for rows,sheet in tables:
                    for fields,source in core.table_rows(rows,definitions,file['name']+' · '+sheet):
                        rownum=int(re.search(r'第 (\d+) 行$',source)[1]);session=''
                        for header in rows[:rownum-1]:
                            column=next((i for i,v in enumerate(header) if label(v) in {label(x) for x in ('专场名称','所属专场','拍卖专场','场次名称')}),None)
                            if column is not None:
                                row=rows[rownum-1];session=clean(row[column]) if column<len(row) else ''
                                span=next((m for m in metadata.get(sheet,[]) if m[0]<=rownum<=m[1] and m[2]==column+1),None)
                                if not session and span:session=clean(rows[span[0]-1][column])
                                break
                        plans.append({'fields':fields,'source':source,'grouping':{'codes':[],'sessionName':session}})
        except Exception:
            issues.append('原始表格未能完整读取：'+file['name']+'；请先完成资料识别并检查分类列')
    if not plans:issues.append('没有可复核的原始上拍表格，请先导入并识别原始材料')
    if pending:issues.append('原始分类表还有 '+str(len(pending))+' 个素材未明确上拍分组，专场完整性无法确认')
    return {'plans':plans,'issues':list(dict.fromkeys(issues)),'excluded':excluded,'pending':pending}

def preview(p,identifier,password,progress=None):
    root=folder(p,identifier);export=read_export(root/'export.zip',password,progress);reference=baseline(p,progress)
    core.json_write(root/'parsed.json',export) # Password is not stored.
    sessions={}
    for row in export['records']:
        key=row['sessionId'];s=sessions.setdefault(key,{'id':key,'name':row.get('sessionName',''),'count':0});s['count']+=1
        if s['name']!=row.get('sessionName',''):export['issues'].append('同一专场编号出现不同专场名称')
    core.json_write(root/'parsed.json',export)
    names=sorted({x['grouping'].get('sessionName','') for x in reference['plans']})
    sources=[{'id':name or '__unassigned__','name':name or '原表未标专场的全部拍品（请确认属于本专场）','count':sum(x['grouping'].get('sessionName','')==name for x in reference['plans'])} for name in names]
    return {'auditId':identifier,'sessions':list(sessions.values()),'sources':sources,'issues':export['issues']+reference['issues'],'excludedCount':len(reference['excluded']),'duplicateRows':export['duplicateRows']}

def matched(plan,lot,known):
    expected=set(plan.get('grouping',{}).get('codes',[]))
    actual=set(lot.get('grouping',{}).get('codes',[])) or core.classification.title_codes(lot.get('fields',lot).get('拍品名称',''),known)
    return expected==actual if expected and actual else title(plan['fields'].get('拍品名称',''))==title(lot.get('fields',lot).get('拍品名称',''))

def compare(p,export,reference,session_id,source_session,progress=None):
    source_name='' if source_session=='__unassigned__' else source_session
    plans=[x for x in reference['plans'] if x['grouping'].get('sessionName','')==source_name]
    if not plans:raise ValueError('请选择有效的原始专场范围；没有读到原始清单时不能复核')
    records=export['records'];current=[x for x in records if x['sessionId']==session_id]
    if not current:raise ValueError('请选择导出包中的专场')
    known={c for x in reference['plans'] for c in x['grouping'].get('codes',[])}|{x['code'] for x in reference['excluded']}
    rows=[];used=set();differences=[];unknown=list(export['issues'])+list(reference['issues'])
    expected_titles={}
    for plan in plans:expected_titles[title(plan['fields']['拍品名称'])]=expected_titles.get(title(plan['fields']['拍品名称']),0)+1
    for i,plan in enumerate(plans):
        if progress:progress({'message':f'正在复核拍品 {i+1}/{len(plans)}：'+plan['fields']['拍品名称'],'completed':i,'total':len(plans)})
        matches=[x for x in records if matched(plan,x,known)]
        row={'name':plan['fields']['拍品名称'],'source':plan['source'],'codes':plan['grouping'].get('codes',[]),'checks':[],'imageState':'未启用'}
        rows.append(row)
        local=[lot for lot in p['lots'] if matched(plan,lot,known)];lot=local[0] if len(local)==1 else None
        row['localLotId']=lot['id'] if lot else ''
        candidates=[x for x in matches if x['sessionId']==session_id]
        if not candidates:
            row['state']='差异';row['message']='漏拍品'+('；同名/同编号拍品在其他专场 '+','.join(sorted({x['sessionId'] for x in matches})) if matches else '')
            differences.append(row['name']+'：'+row['message']);continue
        if len(candidates)!=1 or (not row['codes'] and expected_titles[title(row['name'])]>1):
            row['state']='未核实';row['message']='存在同名或重复拍品，不能唯一对应';unknown.append(row['name']+'：'+row['message']);continue
        actual=candidates[0];identity=(actual['sessionId'],actual['lotId'])
        if identity in used:row.update(state='差异',message='一个京东拍品对应多条原始分组');differences.append(row['name']+'：'+row['message']);continue
        used.add(identity);row.update(lotId=actual['lotId'],url='https://paimai.jd.com/'+actual['lotId'],sessionId=session_id)
        expected=deepcopy(plan['fields'])
        basis={k:'原始表格' for k in expected}
        for key,value in p.get('commonFields',{}).items():
            if key not in {'起拍价','拍品名称','图片文件夹名称'}:expected[key]=value;basis[key]='项目统一设置'
        computed=core.common_values(p,{'fields':expected})
        for key in p.get('priceRates',{}):expected[key]=computed[key];basis[key]='按原表起拍价及项目比例/阈值计算'
        if lot:
            for key,value in lot['fields'].items():
                if key in {'拍品名称','图片文件夹名称','起拍价'}:continue
                if key in lot.get('commonOverrides',[]) or lot.get('sources',{}).get(key)=='人工核对':expected[key]=value;basis[key]='本标的单独设置'
                elif key not in expected and value:expected[key]=value;basis[key]='项目已识别字段'
        for key in sorted((set(expected)|set(actual)|{'起拍价','保证金金额','拍品名称'})-{'图片文件夹名称','sessionId','sessionName','lotId','skuId','source','拍品状态','专场状态','拍卖业务线'}):
            a=clean(expected.get(key,''));b=clean(actual.get(key,''))
            if not a and not b and key not in {'起拍价','保证金金额'}:continue
            state='一致' if a and b and same(key,a,b) else '差异' if a and b else '未核实'
            message='' if a and b else '原材料/项目缺少依据' if not a else '导出表没有此字段或为空'
            row['checks'].append({'field':key,'expected':a,'actual':b,'state':state,'basis':basis.get(key,'未提供'),'message':message})
        if len(matches)>1:row['checks'].append({'field':'专场归属','state':'差异','expected':session_id,'actual':','.join(x['sessionId'] for x in matches),'basis':'原始专场范围','message':'同一素材对应多个拍品或跨专场重复'})
        row['state']='差异' if any(x['state']=='差异' for x in row['checks']) else '未核实' if any(x['state']=='未核实' for x in row['checks']) else '一致'
        differences.extend(row['name']+'：'+x['field'] for x in row['checks'] if x['state']=='差异')
        unknown.extend(row['name']+'：'+x['field']+' '+x['message'] for x in row['checks'] if x['state']=='未核实')
    extra=[x for x in current if (x['sessionId'],x['lotId']) not in used]
    for x in extra:differences.append('多出或未对应的拍品：'+x['lotId']+' '+x['拍品名称'])
    return {'sessionId':session_id,'sessionName':current[0].get('sessionName',''),'sourceSession':source_name or '用户选择：原表未标专场的全部拍品','expectedCount':len(plans),'actualCount':len(current),'excludedCount':len(reference['excluded']),'rows':rows,'extra':extra,'differences':differences,'unknown':unknown,'duplicateRows':export['duplicateRows'],'imageEnabled':False,'createdAt':datetime.now().isoformat(timespec='seconds')}

def run(p,body,progress=None):
    identifier=body.get('auditId');root=folder(p,identifier)
    export=json.loads((root/'parsed.json').read_text(encoding='utf8'));reference=baseline(p,progress)
    result=compare(p,export,reference,str(body.get('sessionId','')),str(body.get('sourceSession','')),progress)
    result['auditId']=identifier
    if body.get('images') is True:
        import jd_batch_jd
        result['imageEnabled']=True
        jd_batch_jd.review_images(p,result,str(body.get('visionModel','')).strip(),progress)
    result['state']='发现差异' if result['differences'] else '部分未核实' if result['unknown'] else '已核对字段一致'
    core.json_write(root/'result.json',result)
    (root/'report.html').write_text(report(result),encoding='utf8')
    p['lastAudit']=identifier;core.save(p)
    return result

def report(result):
    e=lambda v:html.escape(str(v))
    rows=[]
    for item in result['rows']:
        rows.append('<h3>'+e(item['name'])+' · '+e(item['state'])+'</h3><p>'+e(item['source'])+'</p><p>'+e(item.get('message',''))+'</p>')
        if item.get('url'):rows.append('<p><a href="'+e(item['url'])+'">京东拍品 '+e(item['lotId'])+'</a></p>')
        rows.append('<table><tr><th>字段</th><th>预期</th><th>京东</th><th>结果</th><th>依据/说明</th></tr>'+''.join('<tr>'+''.join('<td>'+e(v)+'</td>' for v in [c['field'],c.get('expected',''),c.get('actual',''),c['state'],c.get('basis','')+' '+c.get('message','')])+'</tr>' for c in item['checks'])+'</table>')
        rows.append('<p>图片：'+e(item.get('imageState','未启用'))+' '+e(item.get('imageMessage',''))+'</p>')
        for image in item.get('imageChecks',[]):rows.append('<p>'+e(image['message'])+'</p>')
    return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>专场复核记录</title><style>body{font:15px/1.7 sans-serif;max-width:1100px;margin:40px auto;padding:20px}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:8px;text-align:left}h3{margin-top:32px}</style><h1>专场复核：'+e(result['sessionName'])+'</h1><p>'+e(result['state'])+' · 专场 '+e(result['sessionId'])+' · '+e(result['createdAt'])+'</p><p>原表 '+str(result['expectedCount'])+' 个；导出 '+str(result['actualCount'])+' 个；排除 '+str(result['excludedCount'])+' 个素材。</p><p>原始范围：'+e(result['sourceSession'])+'。图片识别为 AI 辅助判断；未启用或未读到的内容不计为通过。</p><h2>差异与未核实项</h2><ul>'+''.join('<li>'+e(x)+'</li>' for x in result['differences']+result['unknown'])+'</ul>'+''.join(rows)+'</html>'
