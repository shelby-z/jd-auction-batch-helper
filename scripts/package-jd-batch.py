"""Build an allowlisted Windows distribution; never copy project data or credentials."""
from pathlib import Path
from io import BytesIO
from datetime import datetime
import hashlib,importlib.metadata as metadata,json,shutil,sys,zipfile,re,subprocess,os

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'.codex-work/jd-batch/python-deps'),str(ROOT/'src')]
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
import jd_batch as core
import openpyxl
from PIL import Image,ImageDraw

def copy_file(source,destination):
    destination.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,destination)


def build_desktop(package,work):
    icon=Image.new('RGBA',(256,256),(0,0,0,0));draw=ImageDraw.Draw(icon)
    draw.rounded_rectangle((8,8,248,248),radius=48,fill='#263e58')
    draw.rounded_rectangle((65,44,191,211),radius=12,fill='white')
    for y in (76,101,126):draw.rounded_rectangle((88,y,166,y+8),radius=4,fill='#a6b6c6')
    draw.line((87,167,112,190,172,146),fill='#dc4c40',width=16)
    icon_path=work/'assistant.ico';icon.save(icon_path,sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])
    compiler=Path(os.environ.get('WINDIR','C:/Windows'))/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    subprocess.run([str(compiler),'/nologo','/target:winexe','/platform:x64','/optimize+','/reference:System.Windows.Forms.dll','/reference:System.Drawing.dll','/win32icon:'+str(icon_path),'/out:'+str(package/'资料助手.exe'),str(ROOT/'scripts/jd-batch-desktop.cs')],check=True)

def main():
    stamp=datetime.now().strftime('%Y%m%d_%H%M%S')
    work=ROOT/'.codex-work/jd-batch/distribution'/stamp
    package=work/'京东批量上拍资料助手_Windows版';package.mkdir(parents=True)
    for name in ['src/jd_batch.py','src/jd_batch_ai.py','src/jd_batch_classification.py','src/jd_batch_errors.py','src/jd_batch_audit.py','src/jd_batch_jd.py','scripts/jd-batch-server.py','public/jd-batch.html','public/jd-batch.js','public/jd-batch.css']:
        copy_file(ROOT/name,package/name)
    copy_file(ROOT/'scripts/jd-batch-portable-launch.py',package/'scripts/launch.py')
    # Release paths are local to the extracted package, with no source-machine links.
    target=package/'scripts/jd-batch-server.py';text=target.read_text(encoding='utf-8').replace("sys.path.insert(0,str(ROOT/'.codex-work/jd-batch/python-deps'))\n",'').replace('import json, os, re, threading, uuid','import hashlib, json, os, re, threading, uuid').replace("'service':'jd-batch-v1'","'service':'jd-batch-v1','instance':hashlib.sha256(str(ROOT).casefold().encode('utf-8')).hexdigest()");target.write_text(text,encoding='utf-8')
    target=package/'public/jd-batch.html';text=re.sub(r'<!--.*?-->\s*','',target.read_text(encoding='utf-8'),flags=re.S);target.write_text(text,encoding='utf-8')
    build_desktop(package,work)
    (package/'templates').mkdir()
    with zipfile.ZipFile(package/'templates/批量上拍空白模板.zip','w',zipfile.ZIP_DEFLATED) as archive:
        for template in core.templates().values():
            workbook=openpyxl.load_workbook(BytesIO(template['blob']),keep_links=False)
            for sheet in workbook:
                for row in sheet:
                    for cell in row:
                        if isinstance(cell,openpyxl.cell.cell.MergedCell):continue
                        if cell.row>=3:cell.value=None
                        cell.hyperlink=None;cell.comment=None
            workbook.properties.creator='';workbook.properties.lastModifiedBy='';workbook.properties.description='';workbook.properties.title=''
            output=BytesIO();workbook.save(output);archive.writestr(template['filename'],output.getvalue())
    runtime=Path(sys.base_prefix);dest=package/'runtime'
    for source in runtime.iterdir():
        if source.is_file() and (source.suffix.lower() in {'.exe','.dll'} or source.name=='LICENSE.txt'):copy_file(source,dest/source.name)
    ignore=shutil.ignore_patterns('__pycache__','*.pyc','*.pyo','site-packages','test','tests','ensurepip','idlelib','tkinter','turtledemo')
    shutil.copytree(runtime/'Lib',dest/'Lib',ignore=ignore)
    shutil.copytree(runtime/'DLLs',dest/'DLLs',ignore=ignore)
    pending=['openpyxl','python-docx','pypdf','pdfplumber','pypdfium2','Pillow','rapidocr-onnxruntime','xlrd'];selected={}
    while pending:
        name=canonicalize_name(pending.pop())
        if name in selected:continue
        distribution=metadata.distribution(name);selected[name]=distribution
        for dependency in distribution.requires or []:
            requirement=Requirement(dependency)
            if requirement.marker is None or requirement.marker.evaluate({'extra':''}):pending.append(requirement.name)
    versions={}
    for name,distribution in sorted(selected.items()):
        versions[name]=distribution.version
        for file in distribution.files or []:
            if '..' in file.parts or '__pycache__' in file.parts or file.suffix in {'.pyc','.pyo'} or file.name=='direct_url.json':continue
            source=Path(distribution.locate_file(file))
            if source.is_file():copy_file(source,dest/'Lib/site-packages'/file)
    (package/'依赖版本.txt').write_text('\n'.join(name+'=='+version for name,version in versions.items())+'\n',encoding='utf-8-sig')
    copy_file(ROOT/'docs/jd-batch-portable-readme.txt',package/'先看这里.txt')
    entries=[]
    for source in sorted(package.rglob('*')):
        if source.is_file():entries.append({'path':source.relative_to(package).as_posix(),'bytes':source.stat().st_size,'sha256':hashlib.sha256(source.read_bytes()).hexdigest()})
    (package/'文件校验清单.json').write_text(json.dumps(entries,ensure_ascii=False,indent=2),encoding='utf-8')
    output=ROOT/'outputs/jd-batch/工具分享包';output.mkdir(parents=True,exist_ok=True)
    archive=output/f'京东批量上拍资料助手_Windows免安装版_{stamp}.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as bundle:
        for source in sorted(package.rglob('*')):
            if source.is_file():bundle.write(source,source.relative_to(package.parent))
    result={'package':str(package),'archive':str(archive),'files':len(entries)+1,'bytes':archive.stat().st_size,'sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'versions':versions}
    (work/'build-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    (ROOT/'.codex-work/jd-batch/distribution/latest-build.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='versions'},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
