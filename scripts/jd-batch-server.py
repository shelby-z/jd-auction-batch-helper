"""Dedicated loopback server. Does not load board data, accounts or reminders."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'.codex-work/jd-batch/python-deps'))
sys.path.insert(0,str(ROOT/'src'))
import json, os, re, threading, uuid
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs, quote
import jd_batch as core
import jd_batch_ai as ai
import jd_batch_audit as audit
from jd_batch_errors import diagnostic

def error_message(error,stage):return diagnostic(error,ROOT/'logs',stage)

locks={}; locks_guard=threading.Lock()
jobs={}; jobs_guard=threading.Lock()
def lock_for(key):
    with locks_guard: return locks.setdefault(key,threading.RLock())

def start_job(project_id,body,kind='analyze'):
    with jobs_guard:
        running=next((j for j in jobs.values() if j['projectId']==project_id and j['state']=='running'),None)
        if running:raise ValueError('本项目还有任务正在进行，请等待完成后重试')
        job={'id':uuid.uuid4().hex,'projectId':project_id,'kind':kind,'state':'running','message':'正在准备处理','percent':0,'remainingSeconds':None,'startedAt':time.time()};jobs[job['id']]=job
    phase_clock={'phase':None,'time':time.monotonic()}
    def progress(event):
        with jobs_guard:
            if isinstance(event,str):job['message']=event;return
            phase=event.get('phase','work');completed=event.get('completed',0);total=event.get('total',0)
            if phase_clock['phase']!=phase:phase_clock.update(phase=phase,time=time.monotonic(),initial=completed)
            fraction=completed/total if total else 0
            if kind=='analyze':
                lo,hi=(0,25) if body.get('ai') and phase=='read' else (40,99) if phase=='ai' else (0,99)
            elif kind=='audit-unlock':lo,hi=(35,99) if phase=='reference' else (0,35)
            elif kind=='audit-run':lo,hi=(0,15) if phase=='reference' else (40,99) if phase=='images' else (15,40 if body.get('images') else 99)
            else:lo,hi=0,99
            advanced=completed-phase_clock.get('initial',0);elapsed=time.monotonic()-phase_clock['time']
            estimate=round(elapsed/advanced*(total-completed)) if advanced>0 and elapsed>=1 else None
            job.update(message=event.get('message','正在处理'),percent=round(min(99,max(0,lo+(hi-lo)*fraction)),1),remainingSeconds=estimate,completed=completed,total=total,progressAt=time.time())
    def run():
        try:
            with lock_for(project_id):
                p=core.load(project_id)
                result=None
                if kind=='export':result=core.export(p,body.get('draft') is True,progress)
                elif kind=='audit-unlock':result=audit.preview(p,body['auditId'],body.get('password',''),progress)
                elif kind=='audit-run':result=audit.run(p,body,progress)
                elif kind=='files':
                    try:core.ingest_source(p,body['name'],body['path'],progress)
                    finally:body['path'].unlink(missing_ok=True)
                elif not p['analyzed']:
                    core.analyze(p,body.get('ocr',True),progress)
                elif kind=='analyze':
                    for file in p['files']:
                        retry_scan=body.get('ocr',False) and any('页没有提取到文字' in note for note in file.get('notes',[]))
                        if file['kind']=='image' or (core.has_document_text(file) and file.get('status')!='读取失败' and not retry_scan):continue
                        progress('重新读取未完成文件：'+file['name'])
                        try:
                            text,_,notes=core.read_document(core.project_path(p['id'])/'files'/file['id'],file['name'],body.get('ocr',False))
                            file.update(text=text,notes=notes,status='已读取' if text else '未提取文字')
                        except Exception as error:file.update(status='读取失败',notes=[ai.chinese_error(error)])
                    core.save(p)
                if kind=='analyze' and body.get('ai'):
                    progress({'message':'AI 正在识别表格分类结构','phase':'schema','completed':0,'total':0})
                    with jobs_guard:job['percent']=25
                    ai.recognize(p,progress)
                with jobs_guard:job.update(state='completed',message='处理完成',percent=100,remainingSeconds=0,result=result,finishedAt=time.time())
        except Exception as e:
            with jobs_guard:job.update(state='failed',message=error_message(e,{'export':'生成上拍包','audit-unlock':'读取专场导出包','audit-run':'专场复核','files':'资料归档'}.get(kind,'读取资料与 AI 识别')),finishedAt=time.time())
        finally:body.pop('password',None)
    threading.Thread(target=run,daemon=True).start()
    return dict(job)

class Handler(BaseHTTPRequestHandler):
    def log_message(self,fmt,*args): pass

    def send_bytes(self,status,data,content_type='application/json; charset=utf-8',filename=None):
        self.send_response(status)
        self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
        if filename: self.send_header('Content-Disposition',"attachment; filename*=UTF-8''"+quote(filename))
        try:self.end_headers(); self.wfile.write(data)
        except (ConnectionError,OSError):pass # Browser navigation must not trigger a second response.

    def respond(self,status,value): self.send_bytes(status,json.dumps(value,ensure_ascii=False).encode('utf8'))

    def body(self,limit=64*1024*1024):
        length=int(self.headers.get('Content-Length','0'))
        if length<0 or length>limit: raise ValueError('文件或请求过大')
        value=self.rfile.read(length)
        if len(value)!=length: raise ValueError('文件上传不完整，请重新导入')
        return value

    def receive_file(self,project_id):
        length=int(self.headers.get('Content-Length','-1'))
        if length<0:raise ValueError('上传缺少文件长度，请通过网页重新选择文件')
        folder=core.project_path(project_id)/'incoming';folder.mkdir(parents=True,exist_ok=True)
        path=folder/uuid.uuid4().hex
        try:
            with path.open('wb') as out:
                remaining=length
                while remaining:
                    chunk=self.rfile.read(min(1024*1024,remaining))
                    if not chunk:raise ValueError('上传中断，请重新导入')
                    out.write(chunk);remaining-=len(chunk)
            return path
        except Exception:path.unlink(missing_ok=True);raise

    def same_origin(self):
        host=self.headers.get('Host','')
        if host not in {f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}: return False
        origin=self.headers.get('Origin')
        return (not origin or origin=='http://'+host) and self.headers.get('Sec-Fetch-Site')!='cross-site'

    def do_GET(self):
        try:
            if not self.same_origin(): return self.respond(403,{'error':'仅允许本机同源访问'})
            path=urlsplit(self.path).path
            assets={'/':'jd-batch.html','/jd-batch.html':'jd-batch.html','/jd-batch.css':'jd-batch.css','/jd-batch.js':'jd-batch.js'}
            if path in assets:
                name=assets[path]; return self.send_bytes(200,(ROOT/'public'/name).read_bytes(),{'.html':'text/html; charset=utf-8','.css':'text/css; charset=utf-8','.js':'text/javascript; charset=utf-8'}[Path(name).suffix])
            if path=='/api/templates': return self.respond(200,{'templates':core.metadata(),'service':'jd-batch-v1'})
            if path=='/api/ai/status':return self.respond(200,ai.public_config())
            job_match=re.fullmatch(r'/api/jobs/([a-f0-9]{32})',path)
            if job_match:
                with jobs_guard:job=jobs.get(job_match[1]);result=dict(job) if job else None
                return self.respond(200 if result else 404,result or {'error':'任务已中断，请重新发起识别'})
            if path=='/api/projects':
                projects=[]
                for file in core.DATA.glob('*/project.json'):
                    p=json.loads(file.read_text(encoding='utf8'))
                    projects.append({'id':p['id'],'name':p['name'],'template':p['template'],'count':len(p['lots']),'modified':file.stat().st_mtime})
                return self.respond(200,sorted(projects,key=lambda p:p['modified'],reverse=True))
            audit_match=re.fullmatch(r'/api/projects/([a-f0-9]{32})/audits/([a-f0-9]{32})(/report)?',path)
            if audit_match:
                project_id,identifier,report=audit_match.groups();p={'id':project_id};root=audit.folder(p,identifier)
                if report:return self.send_bytes(200,(root/'report.html').read_bytes(),'text/html; charset=utf-8','专场复核记录.html')
                return self.respond(200,json.loads((root/'result.json').read_text(encoding='utf8')))
            match=re.fullmatch(r'/api/projects/([a-f0-9]{32})(?:/(preview|download|report)(?:/([a-f0-9]{32}))?)?',path)
            if not match: return self.respond(404,{'error':'页面不存在'})
            project_id,action,file_id=match.groups()
            with lock_for(project_id):
                p=core.load(project_id)
                if not action: return self.respond(200,{'project':p,'validation':core.validate(p)})
                if action=='preview':
                    f=next((f for f in p['files'] if f['id']==file_id and f['kind']=='image'),None)
                    if not f: return self.respond(404,{'error':'图片不存在'})
                    blob=core.prepare_image((core.project_path(project_id)/'files'/file_id).read_bytes())
                    return self.send_bytes(200,blob,'image/jpeg')
                name=p.get('lastReport' if action=='report' else 'lastExport')
                if not name: return self.respond(404,{'error':'请先生成文件'})
                file=core.OUTPUTS/project_id/name
                return self.send_bytes(200,file.read_bytes(),'application/zip' if action=='download' else 'application/json; charset=utf-8',name)
        except FileNotFoundError: self.respond(404,{'error':'项目不存在'})
        except Exception as e: self.respond(400,{'error':error_message(e,'读取本机数据')})

    def do_POST(self):
        try:
            if not self.same_origin(): return self.respond(403,{'error':'仅允许本机同源访问'})
            path=urlsplit(self.path).path
            if path=='/api/ai/config':return self.respond(200,ai.save_config(json.loads(self.body(20000))))
            if path=='/api/ai/test':
                self.body(20000)
                return self.respond(200,ai.test_connection())
            if path=='/api/projects':
                data=json.loads(self.body()); p=core.new_project(data.get('name'),data.get('template'))
                return self.respond(201,{'project':p,'validation':core.validate(p)})
            match=re.fullmatch(r'/api/projects/([a-f0-9]{32})/(files|analyze|review|common|export|audit-upload|audit-unlock|audit-run)',path)
            if not match: return self.respond(404,{'error':'接口不存在'})
            project_id,action=match.groups()
            if action in {'export','audit-unlock','audit-run'}:
                body=json.loads(self.body())
                if body.get('async'):return self.respond(202,{'job':start_job(project_id,body,action)})
            if action=='analyze':
                body=json.loads(self.body(20000))
                if body.get('ai') and not ai.public_config()['configured']:raise ValueError('请先保存 API 设置，再使用 AI 识别')
                if body.get('async'):return self.respond(202,{'job':start_job(project_id,body)})
            with jobs_guard:
                if any(j['projectId']==project_id and j['state']=='running' for j in jobs.values()):raise ValueError('本项目还有任务正在处理，请等待完成后操作')
            with lock_for(project_id):
                p=core.load(project_id)
                if action in {'files','audit-upload'}:
                    if action=='files' and p['analyzed']: raise ValueError('已经识别完成，请新建项目导入另一批资料')
                    name=parse_qs(urlsplit(self.path).query).get('name',[''])[0]
                    if not name: raise ValueError('缺少文件名')
                    uploaded=self.receive_file(project_id)
                    if action=='files' and parse_qs(urlsplit(self.path).query).get('async')==['1']:
                        try:return self.respond(202,{'job':start_job(project_id,{'name':name,'path':uploaded},'files')})
                        except Exception:uploaded.unlink(missing_ok=True);raise
                    try:
                        if action=='audit-upload':return self.respond(200,audit.upload(p,uploaded,name))
                        core.ingest_source(p,name,uploaded)
                    finally:uploaded.unlink(missing_ok=True)
                else:
                    if action not in {'analyze','export','audit-unlock','audit-run'}:body=json.loads(self.body())
                    if action=='analyze': core.analyze(p,body.get('ocr',True))
                    elif action=='review': core.update_review(p,body)
                    elif action=='common': core.apply_common(p,body)
                    elif action=='export': return self.respond(200,core.export(p,body.get('draft') is True))
                    elif action=='audit-unlock':return self.respond(200,audit.preview(p,body['auditId'],body.get('password','')))
                    elif action=='audit-run':return self.respond(200,audit.run(p,body))
                self.respond(200,{'project':p,'validation':core.validate(p)})
        except Exception as e: self.respond(400,{'error':error_message(e,'处理本机请求')})

if __name__=='__main__':
    port=int(os.environ.get('JD_BATCH_PORT','4182'))
    core.templates()
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    print(f'京东批量上拍资料助手：http://127.0.0.1:{port}/',flush=True)
    server.serve_forever()
