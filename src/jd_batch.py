"""Local JD batch-package preparation. Supplied documents are data, never instructions."""
from pathlib import Path, PurePosixPath
from io import BytesIO
from copy import deepcopy
from contextlib import ExitStack
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime, timezone, timedelta
import csv, hashlib, json, os, re, threading, uuid, zipfile, posixpath
import xml.etree.ElementTree as ET
import openpyxl
from PIL import Image, ImageOps
from pypdf import PdfReader
from docx import Document
import jd_batch_classification as classification
from jd_batch_errors import chinese_error

ROOT = Path(__file__).resolve().parents[1]
INPUTS = ROOT / 'templates'
DATA = Path(os.environ.get('JD_BATCH_DATA_DIR', ROOT / 'data/jd-batch'))
OUTPUTS = Path(os.environ.get('JD_BATCH_OUTPUT_DIR', ROOT / 'outputs/jd-batch'))
NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
ET.register_namespace('', NS)
IMAGE_EXT = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff'}
MONEY = {'起拍价', '保证金金额', '保留价_价格', '最小加价幅度_卖家指定', '委托方佣金封顶值'}
REQUIRED = {'拍卖业务线','拍卖具体类目','拍品名称','拍卖阶段','流转方式','起拍价','保证金金额','最小加价幅度_卖家指定','处置机构','竞买人限制','尾款支付','配送方式','联系人','委托方佣金类型','委托方佣金比例','委托方佣金封顶值','委托方名称','图片文件夹名称'}
ALIASES = {
 '拍品名称':['标的名称','资产名称','标的物名称','拍卖标的','名称'],
 '起拍价':['起拍价格','起拍金额','起拍价（元）','起拍价（万元）','变卖价'],
 '保证金金额':['保证金','竞买保证金','竞拍保证金'],
 '保留价_价格':['保留价'], '最小加价幅度_卖家指定':['加价幅度','最小加价幅度','加价阶梯','增价幅度'],
 '处置机构':['处置单位'], '联系人':['联系人员','咨询联系人'],
 '联系人电话_手机1':['联系电话','联系人电话','手机','手机号','手机号码'],
 '委托方名称':['委托人','委托方'], '委托方佣金比例':['委托方佣金比例（%）'],
 '图片文件夹名称':['图片目录','照片文件夹'], '拍卖具体类目':['拍品类目','资产类别'],
 '付款时间限制':['付款时间限制（天）'],
}
_templates = None
_ocr = None
_ocr_lock = threading.Lock()

def json_write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.pending')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8')
    temp.replace(path)

def zip_name(info):
    name = info.filename
    if not info.flag_bits & 2048:
        for encoding in ('utf8','gb18030'):
            try: return name.encode('cp437').decode(encoding)
            except UnicodeError: pass
    return name

def templates():
    global _templates
    if _templates is not None: return _templates
    result = {}
    with zipfile.ZipFile(INPUTS/'批量上拍空白模板.zip') as archive:
        for info in archive.infolist():
            name = zip_name(info)
            if '__MACOSX' in name or '/~$' in name or not name.endswith('.xlsx'): continue
            blob = archive.read(info)
            ws = openpyxl.load_workbook(BytesIO(blob)).active
            kind = 'customs' if Path(name).name.startswith('海关') else 'government'
            fields = []
            for cell in ws[2]:
                options = []
                for rule in ws.data_validations.dataValidation:
                    if f'{cell.column_letter}3' in rule and rule.type == 'list' and rule.formula1.startswith('"'):
                        options = rule.formula1.strip('"').split(',')
                fields.append({'key':cell.value,'column':cell.column_letter,'hint':ws.cell(1,cell.column).value or '', 'options':options,'required':cell.value in REQUIRED})
            result[kind] = {'id':kind,'name':'海关拍卖' if kind=='customs' else '政府拍卖','filename':Path(name).name,'fields':fields,'blob':blob,'sha256':hashlib.sha256(blob).hexdigest()}
    _templates = result
    return result

def metadata():
    return [{k:v for k,v in t.items() if k!='blob'} for t in templates().values()]

def project_path(project_id):
    if not re.fullmatch(r'[a-f0-9]{32}', project_id): raise ValueError('项目编号无效')
    return DATA / project_id

def load(project_id):
    return json.loads((project_path(project_id)/'project.json').read_text(encoding='utf8'))

def save(project): json_write(project_path(project['id'])/'project.json', project)

def new_project(name, kind):
    if kind not in templates(): raise ValueError('请选择政府或海关模板')
    p = {'id':uuid.uuid4().hex,'name':str(name or '未命名项目')[:100],'template':kind,'files':[],'lots':[],'warnings':[],'analyzed':False,'reviewed':False}
    save(p)
    return p

def safe_relative(name):
    name = str(name).replace('\\','/')
    path = PurePosixPath(name)
    if path.is_absolute() or any(x in ('..','') for x in path.parts) or ':' in name or '\x00' in name:
        raise ValueError('文件路径不安全')
    return str(path)[:600]

def add_file(p, name, blob):
    return add_file_stream(p,name,BytesIO(blob))

def add_file_stream(p,name,stream):
    name = safe_relative(name)
    file_id = uuid.uuid4().hex
    ext = Path(name).suffix.lower()
    dest = project_path(p['id'])/'files'/file_id
    dest.parent.mkdir(parents=True, exist_ok=True)
    digest=hashlib.sha256(); size=0
    try:
        with dest.open('wb') as out:
            while True:
                chunk=stream.read(1024*1024)
                if not chunk: break
                out.write(chunk); digest.update(chunk); size+=len(chunk)
        digest=digest.hexdigest()
        if any(f['sha256']==digest and f['name']==name for f in p['files']): dest.unlink(); return
    except Exception:
        dest.unlink(missing_ok=True); raise
    p['files'].append({'id':file_id,'name':name,'size':size,'sha256':digest,'kind':'image' if ext in IMAGE_EXT else 'document','lotId':'','ignored':False,'status':'待识别','text':''})
    p['reviewed']=False

def ingest(p, name, blob):
    return ingest_source(p,name,BytesIO(blob))

def ingest_source(p,name,source,progress=None):
    before=len(p['files'])
    try: _ingest_source(p,name,source,progress)
    except Exception:
        for f in p['files'][before:]: (project_path(p['id'])/'files'/f['id']).unlink(missing_ok=True)
        p['files']=p['files'][:before]
        raise
    save(p)
    return p

def _ingest_source(p,name,source,progress=None):
    if Path(name).suffix.lower()!='.zip':
        if isinstance(source,Path):
            with source.open('rb') as stream: add_file_stream(p,name,stream)
        else: add_file_stream(p,name,source)
    else:
        with zipfile.ZipFile(source) as archive:
            infos = [i for i in archive.infolist() if not i.is_dir()]
            # Validate the entire archive before touching files.
            for i in infos:
                safe_relative(zip_name(i))
                if i.flag_bits & 1: raise ValueError('压缩包已加密，请先解密后导入')
            for index,i in enumerate(infos):
                if progress:progress({'message':f'正在解压归档 {index+1}/{len(infos)}','completed':index,'total':len(infos)})
                n=zip_name(i)
                if '__MACOSX/' in n or Path(n).name.startswith(('.','~$')): continue
                if Path(n).suffix.lower()=='.zip':
                    p['warnings'].append(f'{n}：嵌套压缩包未展开，请单独导入')
                    continue
                with archive.open(i) as stream: add_file_stream(p,n,stream)

def clean_header(value):
    return re.sub(r'[\s*＊:：_（）()元万元%％/]', '', str(value or '')).lower()

def field_for(value, fields):
    v=clean_header(value)
    for f in fields:
        if v in [clean_header(x) for x in [f['key']]+ALIASES.get(f['key'],[])]: return f['key']
    return None

def normalize_value(key, value, header=''):
    s=str(value if value is not None else '').strip()
    if key in MONEY or key in {'委托方佣金比例','预计处置数量','付款时间限制','租期','佣金收取时长'}:
        numeric=re.fullmatch(r'[￥¥]?\s*([\d,]+(?:\.\d+)?)\s*(万?元|万|%|％|天)?',s)
        if numeric:
            try:
                n=Decimal(numeric.group(1).replace(',',''))
                if key in MONEY and ('万' in (numeric.group(2) or '') or ('万' in header and not numeric.group(2))): n*=10000
                return format(n,'f').rstrip('0').rstrip('.') if '.' in format(n,'f') else format(n,'f')
            except InvalidOperation: pass
    return s

def table_rows(rows, fields, source):
    found=[]
    for r, values in enumerate(rows):
        mapping={i:field_for(v,fields) for i,v in enumerate(values)}
        mapping={i:k for i,k in mapping.items() if k}
        if '拍品名称' not in mapping.values() or len(mapping)<2: continue
        for row_number,vals in enumerate(rows[r+1:],r+2):
            if not any(v not in ('',None) for v in vals): continue
            data={k:normalize_value(k,vals[i],str(values[i])) for i,k in mapping.items() if i<len(vals) and vals[i] not in ('',None)}
            name=data.get('拍品名称','')
            if not name or name in ('合计','总计','小计') or field_for(name,fields)=='拍品名称': continue
            found.append((data,source+f' · 第 {row_number} 行'))
        break
    return found

def table_pairs(rows,fields):
    data={}
    for row in rows:
        for i in range(0,len(row)-1,2):
            key=field_for(row[i],fields)
            if key and row[i+1] not in ('',None) and not field_for(row[i+1],fields):
                value=normalize_value(key,row[i+1],str(row[i]))
                data.setdefault(key,[]).append(value)
    return {k:list(dict.fromkeys(v)) for k,v in data.items()}

def labeled_values(text, fields):
    result={}
    labels=[]
    for f in fields:
        for label in [f['key']]+ALIASES.get(f['key'],[]):
            if label=='名称': continue
            labels.append((label,f['key']))
    labels.sort(key=lambda x:len(x[0]), reverse=True)
    pattern=r'(?<![\w])('+ '|'.join(re.escape(x[0]) for x in labels)+r')\s*(?:[（(](万元|元|%|％|天)[)）])?\s*[:：]\s*'
    lookup=dict(labels)
    matches=list(re.finditer(pattern,text))
    for i,m in enumerate(matches):
        raw=text[m.end(): matches[i+1].start() if i+1<len(matches) else len(text)]
        raw=re.split(r'[\n\r;；\t]',raw)[0].strip(' ，,')
        key=lookup[m.group(1)]
        if raw and len(raw)<500:
            result.setdefault(key,[]).append(normalize_value(key,raw,m.group(2) or ''))
    return {k:list(dict.fromkeys(v)) for k,v in result.items()}

def ocr_image(image):
    global _ocr
    import numpy as np
    with _ocr_lock:
        if _ocr is None:
            from rapidocr_onnxruntime import RapidOCR
            _ocr=RapidOCR()
        result,_=_ocr(np.array(image.convert('RGB')))
    return '\n'.join(line[1] for line in result or [])

def read_document(blob, name, use_ocr,table_metadata=None):
    with ExitStack() as stack:return _read_document(blob,name,use_ocr,stack,table_metadata if table_metadata is not None else {})

def _read_document(blob,name,use_ocr,stack,table_metadata):
    ext=Path(name).suffix.lower()
    text=''; tables=[]; notes=[]
    source=lambda: stack.enter_context(BytesIO(blob) if isinstance(blob,bytes) else blob.open('rb'))
    raw=lambda: blob if isinstance(blob,bytes) else blob.read_bytes()
    if ext in {'.xls','.xlsx','.xlsm'}:
        # Some spreadsheet exporters keep .xls even when the contents are XLSX.
        # Choose the reader from the file itself; preserve the original upload.
        signature=source().read(8)
        if signature==bytes.fromhex('D0CF11E0A1B11AE1'): ext='.xls'
        elif signature.startswith(b'PK'):
            with zipfile.ZipFile(source()) as archive:
                if 'xl/workbook.xml' in archive.namelist(): ext='.xlsx'
                else: raise ValueError('文件不是可读取的 Excel 表格，请在 Excel/WPS 中另存为 XLSX 后重新导入。')
    if ext in {'.xlsx','.xlsm'}:
        with zipfile.ZipFile(source()) as z:
            book=ET.fromstring(z.read('xl/workbook.xml'))
            rels={r.get('Id'):r.get('Target') for r in ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))}
            for sheet in book.findall('{'+NS+'}sheets/{'+NS+'}sheet'):
                target=rels[sheet.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')]
                path=target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/'+target)
                spans=[]
                with z.open(path) as stream:
                    for _,element in ET.iterparse(stream,events=('end',)):
                        if element.tag=='{'+NS+'}mergeCell':
                            c1,r1,c2,r2=openpyxl.utils.range_boundaries(element.get('ref'));spans.append([r1,r2,c1,c2])
                        element.clear()
                table_metadata[sheet.get('name')]=spans
        wb=openpyxl.load_workbook(source(),data_only=True,read_only=True)
        for ws in wb:
            rows=list(ws.iter_rows(values_only=True))
            tables.append((rows,ws.title))
            text+=f'\n[工作表：{ws.title}]\n'+'\n'.join(f'第 {i} 行\t'+'\t'.join('' if v is None else str(v) for v in row) for i,row in enumerate(rows,1))
        wb.close()
        formulas=openpyxl.load_workbook(source(),data_only=False,read_only=True)
        for ws in formulas:
            cached=next((rows for rows,title in tables if title==ws.title),[])
            for row in ws:
                for c in row:
                    if c.data_type=='f' and (c.row>len(cached) or c.column>len(cached[c.row-1]) or cached[c.row-1][c.column-1] is None): notes.append(f'{ws.title}!{c.coordinate} 公式缺少已计算结果，请在 Excel 计算并保存后导入')
        formulas.close()
    elif ext=='.xls':
        import xlrd
        wb=xlrd.open_workbook(file_contents=raw(),formatting_info=True)
        for ws in wb.sheets():
            table_metadata[ws.name]=[[a+1,b,c+1,d] for a,b,c,d in ws.merged_cells]
            rows=[[xlrd.xldate_as_datetime(c.value,wb.datemode).isoformat(sep=' ') if c.ctype==xlrd.XL_CELL_DATE else int(c.value) if c.ctype==xlrd.XL_CELL_NUMBER and c.value.is_integer() else c.value for c in ws.row(i)] for i in range(ws.nrows)];tables.append((rows,ws.name))
            text+=f'\n[工作表：{ws.name}]\n'+'\n'.join(f'第 {i} 行\t'+'\t'.join(str(v) for v in row) for i,row in enumerate(rows,1))
        wb.release_resources()
    elif ext=='.csv':
        decoded=decode_text(raw())
        try: dialect=csv.Sniffer().sniff(decoded[:8192],delimiters=',\t;')
        except csv.Error: dialect=csv.excel
        rows=list(csv.reader(decoded.splitlines(),dialect))
        tables=[(rows,'CSV')]; text=decoded
    elif ext=='.docx':
        from docx.table import Table
        from docx.text.paragraph import Paragraph
        doc=Document(source());table_no=0
        for element in doc.element.body.iterchildren():
            if element.tag.endswith('}p'): text+='\n'+Paragraph(element,doc).text
            elif element.tag.endswith('}tbl'):
                table_no+=1;t=Table(element,doc);rows=[[c.text for c in r.cells] for r in t.rows]
                tables.append((rows,f'表 {table_no}'))
                text+=f'\n[表 {table_no}]\n'+'\n'.join(f'第 {i} 行\t'+'\t'.join(row) for i,row in enumerate(rows,1))
    elif ext=='.pdf':
        reader=PdfReader(source())
        if reader.is_encrypted: raise ValueError('PDF 已加密，请先解密')
        parts=[]
        for i,p in enumerate(reader.pages):
            value=p.extract_text() or ''
            if len(value.strip())<20 and use_ocr:
                import pypdfium2 as pdfium
                pdf=pdfium.PdfDocument(blob if isinstance(blob,bytes) else str(blob))
                try: value=ocr_image(pdf[i].render(scale=1.8).to_pil())
                finally: pdf.close()
            if not value.strip(): notes.append(f'第 {i+1} 页没有提取到文字')
            if value.strip():parts.append(f'[第 {i+1} 页]\n'+value)
        text='\n'.join(parts)
        # Preserve multi-lot tabular relationships when the PDF has a text layer.
        import pdfplumber
        with pdfplumber.open(source()) as pdf:
            for i,page in enumerate(pdf.pages):
                for t in page.extract_tables(): tables.append((t,f'第 {i+1} 页'))
    elif ext in {'.txt','.md'}: text=decode_text(raw())
    elif ext in IMAGE_EXT:
        if use_ocr: text=ocr_image(Image.open(source()))
        else: notes.append('照片已导入；未识别图片文字')
    else: notes.append('该格式尚不支持读取，请转成 PDF、DOCX、XLSX、CSV、TXT 或图片')
    for sheet,spans in table_metadata.items():
        if spans:text+='\n[工作表 '+sheet+' 的合并单元格：'+', '.join(f'{openpyxl.utils.get_column_letter(c1)}{r1}:{openpyxl.utils.get_column_letter(c2)}{r2}' for r1,r2,c1,c2 in spans)+']'
    return text,tables,notes

def has_document_text(file):
    text=file.get('text','')
    if Path(file.get('name','')).suffix.lower()=='.pdf':text=re.sub(r'\[第\s*\d+\s*页\]','',text)
    return bool(text.strip())


def decode_text(blob):
    for encoding in ('utf-8-sig','gb18030','utf-16'):
        try: return blob.decode(encoding)
        except UnicodeError: pass
    raise ValueError('无法识别文本编码，请另存为 UTF-8')

def empty_lot(kind, name=''):
    return {'id':uuid.uuid4().hex,'fields':{'拍卖业务线':templates()[kind]['name'],'拍品名称':name},'sources':{'拍卖业务线':'所选模板'},'conflicts':{},'imageIds':[]}

def can_retry_read(p):
    documents=[f for f in p.get('files',[]) if f['kind']=='document']
    return bool(p.get('analyzed') and not p.get('lots') and any(f.get('status')=='读取失败' for f in documents) and not any(has_document_text(f) for f in documents))

def analyze(p, use_ocr=True, progress=None):
    if p['lots'] or (p['analyzed'] and not can_retry_read(p)): raise ValueError('已生成标的，请新建项目重新识别，以免覆盖核对结果')
    fields=templates()[p['template']]['fields']
    lots=[]; planned=[]; shared={}; texts=[];plan_summary={'files':[],'excluded':[],'pending':[]}
    ordered=sorted(p['files'],key=lambda f:(f['kind']=='image',Path(f['name']).suffix.lower() not in {'.xls','.xlsx','.xlsm','.csv'}))
    for index,f in enumerate(ordered,1):
        if progress: progress({'message':f'{"正在整理图片信息" if f["kind"]=="image" else "正在读取"} {index}/{len(p["files"])}：{f["name"]}','phase':'read','completed':index-1,'total':len(p['files'])})
        blob=project_path(p['id'])/'files'/f['id']
        try:
            if f['kind']=='image':
                with Image.open(blob) as picture:
                    info={'width':picture.width,'height':picture.height,'format':picture.format,'bytes':f['size']}
                f.update(text='',status='已读取图片名称和规格',notes=[],imageInfo=info);continue
            metadata={}
            text,tables,notes=read_document(blob,f['name'],use_ocr,metadata)
            f['text']=text; f['status']='已读取' if text else '未提取文字'
            f['notes']=notes
            plan=classification.parse_tables(tables,metadata,f['name'],normalize_value)
            if plan:
                for record in plan['plans']:
                    lot=empty_lot(p['template'])
                    for key,value in record['fields'].items():merge_value(lot,key,value,record['source']+' · 分类表')
                    lot['grouping']=record['grouping'];planned.append(lot)
                plan_summary['files'].append(f['id']);plan_summary['excluded']+=plan['excluded'];plan_summary['pending']+=plan['pending']
                f['status']=f'已读取分类表 · {len(plan["plans"])} 个拍品'
                if plan['pending']:f['notes'].append('以下素材未明确分组：'+', '.join(x['code'] for x in plan['pending']))
                continue
            candidates=[]
            for rows,sheet in tables:
                tabular=table_rows(rows,fields,f['name']+' · '+sheet)
                if tabular: candidates+=tabular
                else:
                    pairs=table_pairs(rows,fields)
                    if len(pairs.get('拍品名称',[]))==1:
                        candidates.append(({k:v[0] for k,v in pairs.items() if len(v)==1},f['name']+' · '+sheet))
                    else:
                        for key,vals in pairs.items():
                            for val in vals: shared.setdefault(key,[]).append((val,f['name']+' · '+sheet))
            from_table=bool(candidates)
            if not candidates:
                # Repeated explicit lot labels are separate records, never merged into shared defaults.
                blocks=re.split(r'(?=(?:拍品名称|标的名称|资产名称)\s*[:：])',text)
                for block in blocks:
                    values=labeled_values(block,fields)
                    if len(values.get('拍品名称',[]))==1:
                        candidates.append(({k:v[0] for k,v in values.items() if len(v)==1},f['name']))
                    elif not candidates:
                        for key,vals in values.items():
                            for val in vals: shared.setdefault(key,[]).append((val,f['name']))
            for data,source in candidates:
                matching=[l for l in lots if l['fields'].get('拍品名称')==data.get('拍品名称')]
                existing=matching[0] if len(matching)==1 and not from_table else None
                lot=existing or empty_lot(p['template'])
                for k,v in data.items(): merge_value(lot,k,v,source)
                if not existing: lots.append(lot)
            if text: texts.append(f['name'])
        except Exception as e:
            f['status']='读取失败'; f['notes']=[chinese_error(e)]
    if plan_summary['files']:
        # Explicit listing plans determine the number of lots. Other documents can enrich
        # matching lots, but may not turn inventory rows or appraisal entries into new lots.
        for lot in lots:
            matches=[l for l in planned if l['fields'].get('拍品名称')==lot['fields'].get('拍品名称')]
            if len(matches)==1:
                for key,value in lot['fields'].items():
                    if key not in {'拍品名称','起拍价','图片文件夹名称','拍卖业务线'}:merge_value(matches[0],key,value,lot['sources'].get(key,''))
        lots=planned;p['classification']=plan_summary
        p['classification']['sourceGroups']=[deepcopy(l['grouping']) for l in planned if l.get('grouping')]
    # Only genuinely shared labeled facts are propagated; conflicting values are left blank.
    for lot in lots:
        for key,values in shared.items():
            if key in {'拍品名称','起拍价','保证金金额','保留价_价格','最小加价幅度_卖家指定','图片文件夹名称','预计处置数量'}: continue
            if key not in lot['fields'] or not lot['fields'][key]:
                distinct={v for v,s in values}
                if len(distinct)==1: merge_value(lot,key,values[0][0],values[0][1]+' · 共用信息')
                elif len(distinct)>1: lot['conflicts'][key]=[{'value':v,'source':s} for v,s in values]
    p['lots']=lots
    titles=[l['fields'].get('拍品名称','') for l in lots]
    for title in set(titles):
        if titles.count(title)>1: p['warnings'].append(f'“{title}”出现多条记录，已分别保留；请核对是否为不同标的或重复清单。')
    for i,lot in enumerate(lots):
        if not lot['fields'].get('图片文件夹名称'):
            lot['fields']['图片文件夹名称']=str(i+1) if planned else f'{i+1:03d}'
            lot['sources']['图片文件夹名称']='自动编号'
    classification.assign_images(p,apply_exclusions=True)
    p['analyzed']=True
    inherit_common(p)
    p['warnings'] += [] if lots else ['没有识别出明确的标的名称。可手动新增标的，或导入带“标的名称、起拍价”等表头的清单。']
    save(p)
    return p

def merge_value(lot,key,value,source):
    if key in lot['conflicts']:
        if value not in [v['value'] for v in lot['conflicts'][key]]: lot['conflicts'][key].append({'value':value,'source':source})
    elif lot['fields'].get(key) and lot['fields'][key]!=value:
        lot['conflicts'][key]=[{'value':lot['fields'][key],'source':lot['sources'].get(key,'')},{'value':value,'source':source}]
        lot['fields'][key]=''
    else: lot['fields'][key]=value; lot['sources'][key]=source

def apply_classification(p,records,summary,source_label='分类表'):
    """Reconcile verified listing rows without discarding manual fields or photo order."""
    work=deepcopy(p);lots=[];matched=set()
    for record in records:
        members=set(record['grouping']['codes'])
        candidates=[l for l in work['lots'] if set(l.get('grouping',{}).get('codes',[]))==members]
        if not candidates:
            candidates=[l for l in work['lots'] if l['fields'].get('拍品名称')==record['fields']['拍品名称']]
        old=candidates[0] if len(candidates)==1 and candidates[0]['id'] not in matched else None
        lot=deepcopy(old) if old else empty_lot(work['template'])
        if old:matched.add(old['id'])
        for key,value in record['fields'].items():
            if lot['sources'].get(key)=='人工核对':continue
            lot['fields'][key]=value;lot['sources'][key]=record['source']+' · '+source_label
            lot['conflicts'].pop(key,None);lot.get('aiEvidence',{}).pop(key,None)
        lot['grouping']=deepcopy(record['grouping']);lots.append(lot)
    for old in work['lots']:
        if old['id'] in matched:continue
        manual={k for k,v in old['sources'].items() if v=='人工核对'}|set(old.get('commonOverrides',[]))
        # Redundant fills equal to saved project defaults are retained by inheritance.
        defaults=common_values(work,old) if work.get('commonMode')=='inherit' else {}
        changed=[k for k in manual if k not in defaults or old['fields'].get(k)!=defaults[k]]
        if changed or old.get('manualImages'):
            raise ValueError('分类表无法唯一对应已单独修改的标的“'+old['fields'].get('拍品名称','')+'”，请先核对该标的的'+('、'.join(changed) if changed else '图片归属')+'；当前结果已保留')
    folders={l['fields'].get('图片文件夹名称') for l in lots if l['sources'].get('图片文件夹名称')=='人工核对'}
    for lot in lots:
        if lot['sources'].get('图片文件夹名称')=='人工核对':continue
        n=1
        while str(n) in folders:n+=1
        lot['fields']['图片文件夹名称']=str(n);lot['sources']['图片文件夹名称']='自动编号';folders.add(str(n))
    work['lots']=lots;work['classification']=deepcopy(summary)
    work['classification']['sourceGroups']=[deepcopy(r['grouping']) for r in records]
    assigned={image_id:l['id'] for l in lots for image_id in l['imageIds']}
    for f in work['files']:
        if f['kind']!='image':continue
        f['lotId']=assigned.get(f['id'],'')
        if f.get('ignoreReason','').startswith('分类表'):
            f['ignored']=False;f.pop('ignoreReason',None)
    classification.assign_images(work,apply_exclusions=True);inherit_common(work)
    if work['lots']!=p['lots'] or work['classification']!=p.get('classification'):
        json_write(project_path(p['id'])/'history'/('before-classification-'+uuid.uuid4().hex+'.json'),p)
        work['structureRevision']=p.get('structureRevision',0)+1
        work['reviewed']=False
    p.clear();p.update(work);save(p)
    return p


def valid_folder(name):
    return bool(name and len(name)<=80 and not re.search(r'[<>:"/\\|?*\x00-\x1f]',name) and name not in ('.','..') and not name.endswith(('.', ' ')) and not re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)',name,re.I))

def validate(p):
    errors=[]; warnings=[]; seen=set(); used=set()
    excluded_codes={x['code'] for x in p.get('classification',{}).get('excluded',[])}
    all_codes=excluded_codes|{c for l in p['lots'] for c in l.get('grouping',{}).get('codes',[])}
    defs=templates()[p['template']]['fields']
    image_ids={f['id'] for f in p['files'] if f['kind']=='image'}
    if not p['lots']: errors.append({'lot':'','field':'标的','message':'至少新增一个标的'})
    for i,lot in enumerate(p['lots']):
        fields=lot['fields']; title=fields.get('拍品名称') or f'标的 {i+1}'
        def error(key,message): errors.append({'lot':lot['id'],'field':key,'message':title+'：'+message})
        for f in defs:
            key=f['key']; v=str(fields.get(key,'')).strip()
            if f['required'] and not v: error(key,'请补充'+key)
            if v and f['options'] and v not in f['options']: error(key,key+'不在模板可选范围内')
            if len(v)>1000: error(key,key+'内容过长')
            if v.startswith(('=','+','@')): error(key,key+'不能以公式符号开头')
        if fields.get('拍卖业务线')!=templates()[p['template']]['name']: error('拍卖业务线','业务线与模板不一致')
        for key in ('拍品名称','委托方名称'):
            if len(fields.get(key,''))>50: error(key,key+'不能超过 50 字')
        for key in MONEY|{'委托方佣金比例','预计处置数量','租期','佣金收取时长'}:
            v=str(fields.get(key,'')).strip()
            if not v: continue
            if not re.fullmatch(r'\d+(\.\d{1,2})?',v): error(key,key+'必须是最多两位小数的数字'); continue
            n=Decimal(v)
            if key in {'委托方佣金比例','委托方佣金封顶值'}:
                if key=='委托方佣金比例' and n>100: error(key,'委托方佣金比例必须为 0–100')
            elif n<=0 and not (key=='保留价_价格' and n==0): error(key,key+'必须大于 0')
            if key in {'保证金金额','保留价_价格','最小加价幅度_卖家指定'} and len(v.split('.')[0])>12: error(key,key+'整数部分不能超过 12 位')
        if not any(fields.get(k,'').strip() for k in ('联系人电话_座机1','联系人电话_座机2','联系人电话_手机1')): error('联系人电话_手机1','至少填写一个联系电话')
        for k in ('联系人电话_座机1','联系人电话_座机2','联系人电话_手机1'):
            if fields.get(k) and not re.fullmatch(r'[\d\s()（）+\-]{5,30}',fields[k]): error(k,'请核对联系电话，只填写号码')
        if fields.get('流转方式')=='出租':
            for k in ('租期','租期单位','佣金收取时长','成交总额计算方式'):
                if not fields.get(k): error(k,'出租时请补充'+k)
            if fields.get('单价竞拍'): error('单价竞拍','出租时请清空单价竞拍')
        else:
            for k in ('租期','租期单位','佣金收取时长','成交总额计算方式'):
                if fields.get(k): error(k,'非出租时请清空'+k)
        if fields.get('单价竞拍')!='是':
            for k in ('单价竞拍计量单位','预计处置数量'):
                if fields.get(k): error(k,'未选单价竞拍时请清空'+k)
        if fields.get('尾款支付')=='通过京东平台':
            if not re.fullmatch(r'[1-9]\d?',str(fields.get('付款时间限制',''))) or int(fields.get('付款时间限制') or 0)>90: error('付款时间限制','京东尾款支付需填写 1–90 天')
        elif fields.get('付款时间限制'): error('付款时间限制','不通过京东平台时请清空付款时间限制')
        if fields.get('拍卖阶段')=='变卖' and fields.get('快速竞价'): error('快速竞价','变卖不能开启快速竞价')
        folder=fields.get('图片文件夹名称','')
        if not valid_folder(folder): error('图片文件夹名称','图片目录名无效或包含路径符号')
        if folder.casefold() in seen: error('图片文件夹名称','图片目录名不能重复')
        seen.add(folder.casefold())
        if not lot.get('imageIds'): error('图片','至少分配一张标的图片')
        for file_id in lot.get('imageIds',[]):
            if file_id not in image_ids: error('图片','图片不存在')
            if file_id in used: error('图片','同一图片被分配给多个标的')
            used.add(file_id)
        members=set(lot.get('grouping',{}).get('codes',[]))
        blocked=members&excluded_codes|classification.title_codes(title,excluded_codes)
        for f in p['files']:
            if f['id'] in lot.get('imageIds',[]):blocked|=classification.image_codes(f['name'],all_codes)&excluded_codes
        if blocked:error('已排除标的','素材 '+','.join(sorted(blocked))+' 已因鉴定不符合或明确不上拍而排除，不能放入上拍包')
        if members:
            covered=set()
            for f in p['files']:
                if f['id'] not in lot.get('imageIds',[]):continue
                codes=classification.image_codes(f['name'],all_codes);covered|=codes
                if codes-members:error('图片','图片编号 '+','.join(sorted(codes-members))+' 不属于该分类组合')
            if members-covered:error('图片','分类组合缺少图片：'+','.join(sorted(members-covered)))
        for key in lot.get('conflicts',{}): error(key,key+'存在来源冲突，请选定或填写正确值')
        if len(lot.get('imageIds',[]))>10: warnings.append(title+'超过 10 张照片；前 10 张设为轮播图，其余保留为详情图片')
    unassigned=[f for f in p['files'] if f['kind']=='image' and f['id'] not in used and not f.get('ignored')]
    if unassigned: errors.append({'lot':'','field':'图片','message':f'还有 {len(unassigned)} 张图片未分配，请分配或标记为资料图片'})
    if not p.get('reviewed'): errors.append({'lot':'','field':'核对','message':'导出前请确认已核对标的字段和图片归属'})
    return {'errors':errors,'warnings':warnings}

def update_review(p, supplied):
    if supplied.get('structureRevision',0)!=p.get('structureRevision',0):
        raise ValueError('项目分组已更新，请刷新页面后继续，避免旧页面覆盖已恢复的起拍价和图片')
    lots=supplied.get('lots')
    if not isinstance(lots,list): raise ValueError('标的列表无效')
    keys={f['key'] for f in templates()[p['template']]['fields']}
    ids=set(); actual={f['id'] for f in p['files'] if f['kind']=='image'}
    previous={l['id']:l for l in p['lots']}
    migrate_common(p)
    common_keys=set(p.get('commonFields',{}))|set(p.get('priceRates',{}))
    clean=[]
    for l in lots:
        if not re.fullmatch(r'[a-f0-9-]{32,36}',str(l.get('id',''))) or l['id'] in ids: raise ValueError('标的编号无效或重复')
        ids.add(l['id'])
        fields={k:str(v).strip() for k,v in l.get('fields',{}).items() if k in keys}
        images=l.get('imageIds',[])
        if not isinstance(images,list) or any(x not in actual for x in images): raise ValueError('图片编号无效')
        old=previous.get(l['id'],{})
        if old and 'editedFields' in l:
            edited=l['editedFields']
            if not isinstance(edited,list):raise ValueError('修改字段列表无效')
            # A queued browser save may still carry the previous calculated amounts.
            # Only explicit input edits can replace values recomputed by an earlier save.
            fields={**old.get('fields',{}),**{k:v for k,v in fields.items() if k in edited}}
        sources=deepcopy(old.get('sources',{})); conflicts=deepcopy(old.get('conflicts',{}))
        evidence=deepcopy(old.get('aiEvidence',{}))
        overrides=set(old.get('commonOverrides',[]))
        for key,val in fields.items():
            if val!=old.get('fields',{}).get(key,''):
                sources[key]='人工核对'; conflicts.pop(key,None);evidence.pop(key,None)
                if key in common_keys:overrides.add(key)
        for key in l.get('inheritFields',[]):
            if key in common_keys:
                overrides.discard(key);sources[key]='项目统一设置'
        clean.append({'id':l['id'],'fields':fields,'sources':sources,'conflicts':conflicts,'imageIds':images,'commonOverrides':sorted(overrides),'manualImages':bool(old.get('manualImages') or images!=old.get('imageIds',[])),**({'aiEvidence':evidence} if evidence else {}),**({'grouping':deepcopy(old['grouping'])} if old.get('grouping') else {})})
    p['lots']=clean; p['name']=str(supplied.get('name',p['name']))[:100]
    p['reviewed']=supplied.get('reviewed') is True
    ignored=set(supplied.get('ignored',[]))
    for f in p['files']: f['ignored']=f['id'] in ignored
    inherit_common(p)
    save(p)
    return p

def common_values(p,lot):
    values=dict(p.get('commonFields',{}))
    for key,rate in p.get('priceRates',{}).items():
        price=lot['fields'].get('起拍价','')
        if not re.fullmatch(r'\d+(\.\d{1,2})?',price):values[key]='';continue
        price=Decimal(price);amount=price*Decimal(rate)/100
        if price<=0:values[key]='';continue
        if key=='保证金金额':amount=min(price,max(amount,Decimal(p.get('depositMinimum') or '0')))
        elif key=='最小加价幅度_卖家指定':
            options=p.get('incrementOptimization',{})
            if options.get('multipleFive'):amount=max(Decimal('5'),(amount/5).quantize(Decimal('1'),rounding=ROUND_HALF_UP)*5)
            elif options.get('roundInteger'):amount=max(Decimal('1'),amount.quantize(Decimal('1'),rounding=ROUND_HALF_UP))
            if p.get('incrementMinimumEnabled'):amount=max(amount,Decimal(p.get('incrementMinimum') or '0'))
            amount=max(Decimal('.01'),amount)
        values[key]=normalize_value(key,amount.quantize(Decimal('.01'),rounding=ROUND_HALF_UP))
    return values


def migrate_common(p):
    # Older shared fills were labelled manual. Only values still equal to their
    # saved rule can safely resume inheritance; different manual values remain exceptions.
    if p.get('commonMode')=='inherit':return
    for lot in p['lots']:
        for key,value in common_values(p,lot).items():
            if lot['sources'].get(key)=='人工核对' and lot['fields'].get(key)==value:
                lot['sources'][key]='项目统一设置'


def inherit_common(p):
    if p.get('commonMode')!='inherit':return
    for lot in p['lots']:
        overrides=set(lot.get('commonOverrides',[]));values=common_values(p,lot)
        for key in list(lot['sources']):
            if lot['sources'][key].startswith('项目统一设置') and key not in values:
                lot['sources'][key]='原统一设置（已停用）'
        for key,value in values.items():
            if key in overrides or lot['sources'].get(key)=='人工核对':
                overrides.add(key);continue
            lot['fields'][key]=value;lot['conflicts'].pop(key,None);lot.get('aiEvidence',{}).pop(key,None)
            rate=p.get('priceRates',{}).get(key)
            source='项目统一设置'+(f' · 起拍价 × {rate}%' if rate else '')
            if key=='保证金金额' and rate and p.get('depositMinimum'):
                source+=f'，最低 {p["depositMinimum"]} 元，不超过起拍价'
            if key=='最小加价幅度_卖家指定' and rate:
                options=p.get('incrementOptimization',{})
                if options.get('multipleFive'):source+='，取最接近的 5 的倍数'
                elif options.get('roundInteger'):source+='，四舍五入到整数元'
                if p.get('incrementMinimumEnabled'):source+=f'，最低 {p["incrementMinimum"]} 元（阈值优先）'
            if rate and not value:source+=' · 待填写起拍价'
            lot['sources'][key]=source
        lot['commonOverrides']=sorted(overrides)


def apply_common(p,supplied):
    keys={f['key'] for f in templates()[p['template']]['fields']}-{'拍卖业务线','拍品名称','起拍价','图片文件夹名称'}
    values={k:str(v).strip() for k,v in supplied.get('fields',{}).items() if k in keys and str(v).strip()}
    rates={}
    for key,value in supplied.get('rates',{}).items():
        if key not in {'保证金金额','最小加价幅度_卖家指定'} or str(value).strip()=='':continue
        value=str(value).strip()
        if not re.fullmatch(r'\d+(\.\d{1,4})?',value) or not 0<Decimal(value)<=100:raise ValueError('保证金、加价幅度比例须大于 0 且不超过 100%')
        rates[key]=value
        values.pop(key,None)
    for key in {'保证金金额','最小加价幅度_卖家指定'} & values.keys():
        if not re.fullmatch(r'\d+(\.\d{1,2})?',values[key]) or Decimal(values[key])<=0:raise ValueError('统一金额须大于 0，最多保留两位小数')
    minimum=str(supplied.get('depositMinimum','')).strip()
    if minimum and (not re.fullmatch(r'\d+(\.\d{1,2})?',minimum) or Decimal(minimum)<0):raise ValueError('保证金阈值须为不小于 0 的金额，最多保留两位小数')
    if minimum and '保证金金额' not in rates:raise ValueError('请填写保证金比例后再设置阈值')
    increment_options=supplied.get('incrementOptimization',p.get('incrementOptimization',{'roundInteger':True,'multipleFive':True}))
    if not isinstance(increment_options,dict):raise ValueError('加价幅度取整设置无效')
    increment_minimum=str(supplied.get('incrementMinimum',p.get('incrementMinimum',''))).strip()
    increment_enabled=supplied.get('incrementMinimumEnabled',p.get('incrementMinimumEnabled',False)) is True
    if increment_minimum and (not re.fullmatch(r'\d+(\.\d{1,2})?',increment_minimum) or Decimal(increment_minimum)<=0):raise ValueError('最低加价幅度须大于 0，最多保留两位小数')
    if increment_enabled and (not increment_minimum or '最小加价幅度_卖家指定' not in rates):raise ValueError('请填写加价幅度比例和最低阈值后启用阈值')
    work=deepcopy(p)
    migrate_common(work)
    work.update(commonFields=values,priceRates=rates,depositMinimum=minimum,commonMode='inherit',reviewed=False)
    work.update(incrementOptimization={k:increment_options.get(k) is True for k in ('roundInteger','multipleFive')},incrementMinimum=increment_minimum,incrementMinimumEnabled=increment_enabled)
    inherit_common(work)
    p.clear();p.update(work);save(p);return p


def filled_workbook(kind,lots):
    """Patch data rows only; keep original headers, styling and template package parts."""
    template=templates()[kind]; src=zipfile.ZipFile(BytesIO(template['blob']))
    doc=ET.fromstring(src.read('xl/worksheets/sheet1.xml'))
    tag=lambda n:'{'+NS+'}'+n
    data=doc.find(tag('sheetData'))
    original=data.find(tag('row')+"[@r='3']")
    styles={re.sub(r'\d','',c.get('r')):c.get('s') for c in original} if original is not None else {}
    for row in list(data):
        if int(row.get('r'))>=3: data.remove(row)
    for i,lot in enumerate(lots,3):
        row=ET.SubElement(data,tag('row'),{'r':str(i)})
        for f in template['fields']:
            value=str(lot['fields'].get(f['key'],'')).strip()
            if not value: continue
            attrs={'r':f['column']+str(i),'t':'inlineStr'}
            if styles.get(f['column']): attrs['s']=styles[f['column']]
            cell=ET.SubElement(row,tag('c'),attrs)
            ET.SubElement(ET.SubElement(cell,tag('is')),tag('t')).text=value
    end=max(3,len(lots)+2)
    dimension=doc.find(tag('dimension'))
    if dimension is not None: dimension.set('ref',f'A1:{template["fields"][-1]["column"]}{end}')
    dvs=doc.find(tag('dataValidations'))
    if dvs is not None:
        # The original later-row category lists are stale; propagate each first-row rule.
        first=[]
        for dv in list(dvs):
            refs=dv.get('sqref','').split()
            col=None
            for ref in refs:
                m=re.fullmatch(r'([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?',ref)
                if m and int(m[2])<=3<=int(m[4] or m[2]): col=m[1]; break
            if col:
                dv.set('sqref',f'{col}3:{col}{end}'); first.append(dv)
        for dv in list(dvs): dvs.remove(dv)
        for dv in first: dvs.append(dv)
        dvs.set('count',str(len(first)))
    output=BytesIO()
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as target:
        for info in src.infolist(): target.writestr(info,ET.tostring(doc,encoding='utf-8',xml_declaration=True) if info.filename=='xl/worksheets/sheet1.xml' else src.read(info))
    return output.getvalue()

def prepare_image(blob):
    image=ImageOps.exif_transpose(Image.open(BytesIO(blob)))
    if image.width*image.height>80_000_000: raise ValueError('图片像素过大，请缩小后导入')
    image.thumbnail((1600,1600),Image.Resampling.LANCZOS)
    if image.mode in ('RGBA','LA') or 'transparency' in image.info:
        rgba=image.convert('RGBA'); bg=Image.new('RGB',rgba.size,'white'); bg.paste(rgba,mask=rgba.getchannel('A')); image=bg
    else: image=image.convert('RGB')
    for quality in (90,82,72,60,45):
        out=BytesIO(); image.save(out,'JPEG',quality=quality,optimize=True)
        if len(out.getvalue())<1_000_000: return out.getvalue()
    raise ValueError('图片压缩后仍超过 1 MB')

def export(p,draft=False,progress=None):
    result=validate(p)
    if any(e['field']=='已排除标的' for e in result['errors']):raise ValueError('包含已排除标的，请先移出鉴定不符合或明确不上拍的素材')
    if result['errors'] and not draft: raise ValueError('还有待补充或待核对内容，不能生成正式上拍包')
    if not p['lots']: raise ValueError('请先新增标的')
    folders=[l['fields'].get('图片文件夹名称','').casefold() for l in p['lots']]
    if len(set(folders))!=len(folders): raise ValueError('图片目录名不能重复，待补资料包也需保持一一对应')
    now=datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:4]
    stem=re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',p['name']).strip(' .') or '上拍项目'
    stem=stem[:60]+('_待补资料' if draft else '_批量上拍')+'_'+now
    output=BytesIO(); mapping=[]
    total=sum(len(lot['imageIds']) for lot in p['lots'])+2;completed=0
    if progress:progress({'message':'正在填写上拍表格','completed':0,'total':total})
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr(stem+'.xlsx',filled_workbook(p['template'],p['lots']))
        completed+=1
        z.writestr('图片/','')
        for idx,lot in enumerate(p['lots'],1):
            folder=lot['fields'].get('图片文件夹名称','')
            # A draft with unsafe or missing folders must never create unsafe archive paths.
            if not valid_folder(folder): raise ValueError('请先填写安全且唯一的图片目录名')
            for n,file_id in enumerate(lot['imageIds'],1):
                f=next(x for x in p['files'] if x['id']==file_id)
                target=f'图片/{folder}/'+(f'zt{n}.jpg' if n<=10 else f'detail{n:03d}.jpg')
                blob=prepare_image((project_path(p['id'])/'files'/file_id).read_bytes())
                z.writestr(target,blob)
                mapping.append({'original':f['name'],'output':target,'originalSha256':f['sha256'],'outputSha256':hashlib.sha256(blob).hexdigest()})
                completed+=1
                if progress:progress({'message':f'正在处理图片：第 {completed-1}/{total-2} 张','completed':completed,'total':total})
    if len(output.getvalue())>=500_000_000: raise ValueError('输出包超过手册要求的 500 MB，请拆分项目')
    folder=OUTPUTS/p['id']; folder.mkdir(parents=True,exist_ok=True)
    if progress:progress({'message':'正在保存压缩包和核对记录','completed':total-1,'total':total})
    (folder/(stem+'.zip')).write_bytes(output.getvalue())
    report={'project':p['name'],'template':templates()[p['template']]['filename'],'templateSha256':templates()[p['template']]['sha256'],'draft':draft,'validation':result,'mapping':mapping,'lots':p['lots'],'note':'手册 2023-02-27，用户模板包 20260202。后台导入、剩余字段配置和提交尚需在京东完成。'}
    (folder/(stem+'_核对记录.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    p['lastExport']=stem+'.zip'; p['lastReport']=stem+'_核对记录.json'; save(p)
    return {'filename':stem+'.zip','report':p['lastReport'],'validation':result}
