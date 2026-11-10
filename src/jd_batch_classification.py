"""Read explicit auction grouping from an inventory's listing-name and amount columns."""
import re
from pathlib import PurePosixPath

ID_HEADERS={'标的物编号','标的编号','素材编号','资产编号'}
TITLE_HEADERS={'拍品名称','上拍名称'}
PRICE_HEADERS={'金额','起拍价','起拍价格','起拍金额'}

def header(value):
    return re.sub(r'[\s*＊（）()元万元]', '', str(value or ''))

def code(value):
    value=str(value or '').strip().upper()
    return value if re.fullmatch(r'[A-Z]{1,8}\d{1,10}',value) else ''

def title_codes(title,known):
    found=set(re.findall(r'(?<![A-Z0-9])([A-Z]{1,8}\d{1,10})(?![A-Z0-9])',title.upper())) & set(known)
    for prefix,start,other,end in re.findall(r'([A-Z]+)(\d+)\s*[-—–~～至]\s*([A-Z]*)(\d+)',title.upper()):
        if other and other!=prefix:continue
        for item in known:
            m=re.fullmatch(r'([A-Z]+)(\d+)',item)
            if m and m[1]==prefix and int(start)<=int(m[2])<=int(end):found.add(item)
    return found

def parse_tables(tables,merges,file_name,normalize,column_hints=None):
    plans=[];inventory=[];covered=set()
    for rows,sheet in tables:
        hint=(column_hints or {}).get(sheet)
        candidates=[(-1,['']*max(map(len,rows),default=0))] if hint and hint['headerRow']==0 else enumerate(rows)
        for h,values in candidates:
            labels=[header(v) for v in values]
            if hint and h!=hint['headerRow']-1:continue
            idcol=hint['id'] if hint else next((i for i,v in enumerate(labels) if v in ID_HEADERS),None)
            if idcol is None:continue
            get=lambda row,col:row[col] if col is not None and col<len(row) else ''
            titlecol=hint['name'] if hint else next((i for i,v in enumerate(labels) if v in TITLE_HEADERS),None)
            pricecol=hint['price'] if hint else next((i for i,v in enumerate(labels) if v in PRICE_HEADERS),None)
            statuscol=hint.get('status') if hint and 'status' in hint else next((i for i,v in enumerate(labels) if v in {'鉴定结论','是否上拍'}),None)
            sessioncol=hint.get('session') if hint and 'session' in hint else next((i for i,v in enumerate(labels) if v in {'专场名称','所属专场','拍卖专场','场次名称'}),None)
            start=hint.get('dataStartRow',h+2) if hint else h+2
            code_rows={r+1:code(get(row,idcol)) for r,row in enumerate(rows) if r+1>=start and code(get(row,idcol))}
            rejected={r for r in code_rows if str(get(rows[r-1],statuscol)).strip() in {'不符合','不上拍','否'}}
            inventory.extend({'code':c,'source':f'{file_name} · {sheet} · 第 {r} 行','excluded':r in rejected,'reason':('鉴定不符合' if str(get(rows[r-1],statuscol)).strip()=='不符合' else '原表明确不上拍') if r in rejected else ''} for r,c in code_rows.items())
            if column_hints is not None and sheet not in column_hints:break
            if titlecol is None or pricecol is None:break
            consumed=set()
            for rownum,itemcode in code_rows.items():
                if rownum in consumed or rownum in rejected:continue
                title=str(get(rows[rownum-1],titlecol) or '').strip()
                if not title:continue
                groupcol=hint.get('group',titlecol) if hint else titlecol
                span=next((m for m in merges.get(sheet,[]) if m[0]==rownum and m[2]==groupcol+1),None)
                member_rows=[r for r in code_rows if span and span[0]<=r<=span[1]] or [rownum]
                if hint and 'group' in hint and not span:
                    group=get(rows[rownum-1],groupcol)
                    if group not in ('',None):member_rows=[r for r in code_rows if get(rows[r-1],groupcol)==group]
                named=title_codes(title,list(code_rows.values()))
                if not span and len(named)>1:member_rows=[r for r,c in code_rows.items() if c in named]
                members=[code_rows[r] for r in member_rows]
                if any(r in rejected for r in member_rows):
                    raise ValueError(f'{sheet} 第 {rownum} 行：合拍组合包含鉴定不符合或明确不上拍的素材，未生成该组合')
                if named and set(members)!=named:
                    raise ValueError(f'{sheet} 第 {rownum} 行：拍品名称中的编号与合并行不一致，请检查分类表')
                if any(c in covered for c in members):raise ValueError(f'{sheet} 第 {rownum} 行：素材编号重复分配到多个拍品')
                price=normalize('起拍价',get(rows[rownum-1],pricecol),str(values[pricecol]))
                source=f'{file_name} · {sheet} · 第 '+','.join(map(str,member_rows))+' 行'
                session=str(get(rows[rownum-1],sessioncol) or '').strip()
                if sessioncol is not None and not session:
                    merged=next((m for m in merges.get(sheet,[]) if m[0]<=rownum<=m[1] and m[2]==sessioncol+1),None)
                    if merged:session=str(get(rows[merged[0]-1],sessioncol) or '').strip()
                extras={}
                for key,col in (hint or {}).get('fields',{}).items():
                    values_in_group={normalize(key,get(rows[r-1],col),str(values[col])) for r in member_rows if get(rows[r-1],col) not in ('',None)}
                    if len(values_in_group)>1:raise ValueError(f'{sheet} 第 {rownum} 行：同一拍品的{key}有多个不同值，请核对原表')
                    if values_in_group:extras[key]=values_in_group.pop()
                plans.append({'fields':{**extras,'拍品名称':title,'起拍价':price},'source':source,'grouping':{'file':file_name,'sheet':sheet,'rows':member_rows,'codes':members,'titleColumn':titlecol+1,'priceColumn':pricecol+1,'idColumn':idcol+1,'headerRow':h+1,'groupColumn':groupcol+1,'sessionName':session,**({'dataStartRow':start} if h<0 else {}),**({'fieldColumns':hint['fields']} if hint and hint.get('fields') else {}),**({'statusColumn':statuscol+1} if statuscol is not None else {}),**({'sessionColumn':sessioncol+1} if sessioncol is not None else {})}})
                consumed.update(member_rows);covered.update(members)
            break
    if not plans and not any(x['excluded'] for x in inventory):return None
    excluded=[x for x in inventory if x['code'] not in covered and x['excluded']]
    pending=[x for x in inventory if x['code'] not in covered and not x['excluded']]
    return {'plans':plans,'excluded':excluded,'pending':pending}

def image_codes(name,known):
    parts=PurePosixPath(name.replace('\\','/')).parts
    # The nearest numbered directory identifies the photographed object.
    for part in reversed(parts[:-1]):
        matches=title_codes(part,known)
        if matches:return matches
    return title_codes(PurePosixPath(name).stem,known)

def natural_key(name):
    return [int(s) if s.isdigit() else s.casefold() for s in re.split(r'(\d+)',name)]

def assign_images(p,apply_exclusions=False):
    lots=p['lots'];used={i for l in lots for i in l['imageIds']}
    excluded={x['code'] for x in p.get('classification',{}).get('excluded',[])}
    known=excluded|{c for l in lots for c in l.get('grouping',{}).get('codes',[])}
    for f in sorted(p['files'],key=lambda f:natural_key(f['name'])):
        if f['kind']!='image' or f['id'] in used or f.get('ignored'):continue
        codes=image_codes(f['name'],known);parts=PurePosixPath(f['name']).parts;stem=PurePosixPath(f['name']).stem
        matches=[]
        for lot in lots:
            title=lot['fields'].get('拍品名称','');members=set(lot.get('grouping',{}).get('codes',[]))
            folder=lot['fields'].get('图片文件夹名称','')
            if members:
                match=bool(codes) and codes<=members
            else:
                match=bool(title) and (title in parts[:-1] or stem==title or stem.startswith(title+'_') or stem.startswith(title+'-'))
                if folder and lot['sources'].get('图片文件夹名称')!='自动编号':match=match or ('图片' in parts and parts[-2]==folder)
            if match:matches.append(lot)
        if len(matches)==1:
            lot=matches[0];lot['imageIds'].append(f['id']);f['lotId']=lot['id']
        elif apply_exclusions and codes and codes<=excluded:
            f['ignored']=True;f['ignoreReason']='分类表未列入上拍，鉴定结论为不符合'
