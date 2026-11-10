# 京东批量上拍资料助手

把一个项目的原始表格、文档和图片整理成京东批量上拍模板，并复核从京东导出的专场资料。

## 直接使用 Windows 版

在本仓库的 **Releases** 下载 Windows 免安装 ZIP，完整解压后双击 **资料助手.exe**，浏览器会自动打开。无需安装 Python；不要在压缩包内运行，也不要只移动 EXE。

初始包只有程序、运行环境和空白模板，不含已有项目、照片、API Key 或本机资料链接。详细操作见 [使用说明](docs/jd-batch-portable-readme.txt)。

## 功能

- 读取 Excel（XLSX / XLS / CSV）、DOCX、PDF、文字及图片目录，按原表分类合并或拆分拍品；明确不上拍的素材单独提示。XLS / XLSX 后缀与实际格式不一致时自动选择读取方式，无需手动改名。
- AI 识别并复核表格的列和分组，随后从原单元格逐行读取金额和名称，避免重复逐段抄写整表；支持用原单元格样例核验无表头清单。其他文档仍按文件和小片段提取与复核。
- 火山方舟豆包 Seed 模型的文字提取关闭深度思考；普通整理只读取照片文件名、目录和尺寸，不逐张发送图片。
- 起拍价保留各标的原表金额。保证金、加价幅度及共用字段设置一次，各标的分别计算；支持单独修改例外。
- 保证金支持比例与最低阈值，并且不超过起拍价。加价幅度支持最低阈值、取最接近的 5 的倍数和四舍五入，取整开关默认开启；取 5 时最低 5 元。
- 按拍品查看照片、指定主图，导出自动重命名并填写对应图片文件夹。
- 长任务显示当前事项、进度、已用时间及本阶段预计剩余时间，并显示 AI 请求次数和单次等待时间。
- 上传带密码的京东专场导出 ZIP，比较原表与导出表的拍品、专场、起拍价、保证金及其他可核字段；图片复核可单独开启。

本工具只整理资料和读取公开拍品信息。生成 ZIP 后仍需在京东/京麦后台导入、补充配置并提交。

## 从源码运行

推荐 Windows 10/11 64 位、Python 3.12。普通读取、计算和打包可离线使用，AI 需要自行填写兼容 API。

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe scripts/jd-batch-server.py
```

浏览器打开 `http://127.0.0.1:4182/`。数据在 `data/jd-batch/`，导出在 `outputs/jd-batch/`；这些目录均被 Git 忽略。API 设置保存在本机 `data/jd-batch/_ai/settings.json`，请勿提交或分享使用后的整个目录。

`templates/批量上拍空白模板.zip` 内含去掉示例数据和外部链接的政府/海关模板。正式导入前请核对账号后台当前模板要求。

## 验证与打包

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -B -m unittest discover -s tests -p "test_jd_batch*.py" -q
node --test tests/jd-batch-ui.test.mjs
.venv\Scripts\python.exe -B scripts/package-jd-batch.py
```

测试使用合成资料；部分图片文字识别测试依赖 Windows 微软雅黑字体。前端测试需要 Node.js。Windows 打包使用系统 .NET Framework C# 编译器，输出到 `outputs/jd-batch/工具分享包/`。`requirements-lock.txt` 记录已验证的 Windows 包运行依赖版本；打包脚本只复制程序、依赖与空白模板。

## 复核边界

缺少原始依据、字段无法对应、接口拒绝访问或模型无法判断时，结果会标为未核实。图片复核需要支持图片的模型，属于辅助判断；实际模型的完整识图效果尚未验证。传统 ZIP 加密可读取，不支持的加密方式会提示。当前 Windows 环境已验证，未在每台同事电脑上验证。
