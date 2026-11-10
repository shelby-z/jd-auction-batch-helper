"""Chinese user errors and metadata-only diagnostics; never log request bodies or keys."""
from datetime import datetime
from pathlib import Path
import errno, json, re, uuid, zipfile


class AIError(ValueError):
    def __init__(self, message, code='AI_RESPONSE', split=False):
        super().__init__(message)
        self.code=code
        self.split=split


def chinese_error(error):
    if isinstance(error,PermissionError):return '没有文件夹的读写权限，请将整个工具移到桌面或其他可写文件夹后重试。'
    if isinstance(error,FileNotFoundError):return '所需文件不存在，请确认完整解压工具，或重新导入缺失的资料。'
    if isinstance(error,zipfile.BadZipFile):return '压缩包或表格文件损坏，请重新下载或另存后导入。'
    if type(error).__module__.startswith('xlrd.'):
        if 'encrypt' in str(error).lower():return 'Excel 表格已加密，请先在 Excel/WPS 中解除密码保护，另存后重新导入。'
        return 'Excel 表格无法解析，可能是文件损坏或实际内容并非 Excel 格式。请在 Excel/WPS 中打开并另存为 XLSX 后重新导入。'
    if isinstance(error,UnicodeError):return '文件文字编码无法识别，请将文字文件另存为 UTF-8 后导入。'
    if isinstance(error,MemoryError):return '本机可用内存不足，请关闭占用内存的程序，或分批导入资料。'
    if isinstance(error,TimeoutError):return '连接等待超时，请检查网络后重试。'
    if isinstance(error,ConnectionError):return '连接已中断，请重新打开资料助手后重试；已保存的资料仍在本机。'
    if isinstance(error,OSError) and error.errno==errno.ENOSPC:return '磁盘空间不足，请释放工具所在磁盘的空间后重试。'
    if isinstance(error,ValueError) and re.search(r'[\u4e00-\u9fff]',str(error)) and not re.search(r'Traceback|Error:|Exception:|https?://|Bearer |sk-',str(error)):
        return str(error)[:500]
    return '处理未完成，请确认文件能正常打开后重试；如仍失败，请复制错误信息反馈。'


def diagnostic(error, directory, stage):
    message=chinese_error(error); reference=uuid.uuid4().hex[:10]
    record={'time':datetime.now().isoformat(timespec='seconds'),'reference':reference,'stage':stage,'message':message,'code':getattr(error,'code',type(error).__name__)}
    try:
        path=Path(directory);path.mkdir(parents=True,exist_ok=True)
        with (path/'errors.jsonl').open('a',encoding='utf8') as output:output.write(json.dumps(record,ensure_ascii=False)+'\n')
    except OSError:pass
    return message+'（错误编号：'+reference+'）'
