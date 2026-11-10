"""Launch only this extracted copy, using its own runtime and data directory."""
from pathlib import Path
import argparse,hashlib,json,os,socket,subprocess,sys,time,urllib.request,webbrowser

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from jd_batch_errors import chinese_error
INSTANCE=hashlib.sha256(str(ROOT).casefold().encode('utf-8')).hexdigest()
OPENER=urllib.request.build_opener(urllib.request.ProxyHandler({}))

def say(message):
    print(message,flush=True)

def port_in_use(port):
    with socket.socket() as probe:
        try:probe.bind(('127.0.0.1',port));return False
        except OSError:return True

def health(port):
    # Closed ports can take the full HTTP timeout on Windows. Test locally first.
    if not port_in_use(port):return False
    try:
        with OPENER.open(f'http://127.0.0.1:{port}/api/templates',timeout=2) as response:
            value=json.load(response)
        return value.get('service')=='jd-batch-v1' and value.get('instance')==INSTANCE
    except Exception:return False

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--no-open',action='store_true');args=parser.parse_args()
    say('正在检查本地服务…')
    selected=None
    for port in range(4184,4205):
        if health(port):selected=port;break
    if selected is None:
        for port in range(4184,4205):
            if port_in_use(port):continue
            selected=port;break
        if selected is None:raise RuntimeError('本机可用端口已被占用，请关闭其他工具后重试。')
        logs=ROOT/'logs';logs.mkdir(exist_ok=True)
        env=dict(os.environ)
        for key in list(env):
            if key.startswith(('PYTHON','JD_BATCH_')):env.pop(key)
        env.update(JD_BATCH_PORT=str(selected),JD_BATCH_DATA_DIR=str(ROOT/'data/jd-batch'),JD_BATCH_OUTPUT_DIR=str(ROOT/'outputs/jd-batch'),JD_BATCH_AI_CONFIG=str(ROOT/'data/jd-batch/_ai/settings.json'))
        say('正在启动资料助手，请稍候…')
        with (logs/'server.log').open('ab') as log:
            process=subprocess.Popen([sys.executable,'-I','-B','-X','utf8',str(ROOT/'scripts/jd-batch-server.py')],cwd=ROOT,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            if process.poll() is not None:raise RuntimeError('启动失败，请查看 logs/server.log。')
            if health(selected):break
            time.sleep(0.25)
        else:raise RuntimeError('启动等待超时，请查看 logs/server.log。')
        (logs/'server.json').write_text(json.dumps({'pid':process.pid,'port':selected,'instance':INSTANCE}),encoding='utf-8')
    url=f'http://127.0.0.1:{selected}/'
    say('工具已启动：'+url)
    say('工具已在后台运行，可以关闭此窗口；下次双击启动文件可继续使用。')
    if not args.no_open:
        say('正在打开浏览器；若未自动打开，请将上方网址复制到浏览器地址栏。')
        try:
            if not webbrowser.open(url):say('未能自动打开默认浏览器，服务已启动，可手动打开上方网址。')
        except OSError:say('未能自动打开默认浏览器，服务已启动，可手动打开上方网址。')

if __name__=='__main__':
    try:main()
    except Exception as error:
        message=str(error) if isinstance(error,RuntimeError) else chinese_error(error)
        print('启动未完成：'+message,file=sys.stderr,flush=True);sys.exit(1)
