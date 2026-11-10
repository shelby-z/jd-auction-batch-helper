using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;
using System.Windows.Forms;

// Small Windows entry point. All project work remains in the local web service.
class AssistantWindow : Form {
    readonly string root = AppDomain.CurrentDomain.BaseDirectory;
    readonly Label status = new Label();
    readonly TextBox address = new TextBox();
    readonly Button open = new Button();
    readonly Button retry = new Button();
    string url = "";
    Process launch;
    bool starting;
    readonly bool noOpen;
    AssistantWindow(bool quiet) {
        noOpen = quiet;
        Text = "京东批量上拍 · 资料助手";
        Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        ClientSize = new Size(540, 270); MinimumSize = Size; MaximumSize = Size;
        StartPosition = FormStartPosition.CenterScreen;
        Font = new Font("Microsoft YaHei UI", 10F); BackColor = Color.White;
        Label heading = new Label { Text = "批量上拍资料助手", Left = 28, Top = 25, Width = 470, Height = 36, Font = new Font(Font.FontFamily, 17F, FontStyle.Bold) };
        status.SetBounds(30, 76, 480, 64); status.Text = "正在准备打开，请稍候…";
        address.SetBounds(30, 148, 480, 28); address.ReadOnly = true; address.Visible = false;
        open.SetBounds(30, 197, 160, 40); open.Text = "打开操作网页"; open.Enabled = false;
        retry.SetBounds(202, 197, 130, 40); retry.Text = "重新启动"; retry.Enabled = false;
        Button logs = new Button { Text = "查看运行记录", Left = 344, Top = 197, Width = 166, Height = 40 };
        open.Click += (s, e) => OpenPage(); retry.Click += (s, e) => Start();
        logs.Click += (s, e) => {
            try { string path = Path.Combine(root, "logs"); Directory.CreateDirectory(path); Process.Start(new ProcessStartInfo(path) { UseShellExecute = true }); }
            catch { MessageBox.Show("无法打开运行记录文件夹，请将整个工具移到有写入权限的文件夹后重试。", Text); }
        };
        Controls.AddRange(new Control[] { heading, status, address, open, retry, logs });
        Shown += (s, e) => Start();
        FormClosing += (s, e) => { if (starting) { e.Cancel = true; status.Text = "正在启动，请等待结果后关闭窗口。"; } };
    }
    void Display(Action action) { if (!IsDisposed && IsHandleCreated) BeginInvoke(action); }
    void Start() {
        if (starting) return;
        string python = Path.Combine(root, "runtime", "python.exe"), script = Path.Combine(root, "scripts", "launch.py");
        if (!File.Exists(python) || !File.Exists(script)) { status.Text = "工具文件不完整。请先解压整个压缩包，再双击资料助手；不要单独复制启动图标。"; retry.Enabled = true; return; }
        starting = true; retry.Enabled = false; open.Enabled = false; address.Visible = false; url = "";
        status.Text = "正在启动资料助手，请稍候…";
        try {
            launch = new Process();
            launch.StartInfo = new ProcessStartInfo(python, "-I -B -X utf8 \"" + script + "\" --no-open") {
                WorkingDirectory = root, UseShellExecute = false, CreateNoWindow = true,
                RedirectStandardOutput = true, RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8
            };
            string failure = "";
            launch.OutputDataReceived += (s, e) => {
                if (String.IsNullOrEmpty(e.Data)) return;
                Match match = Regex.Match(e.Data, @"http://127\.0\.0\.1:\d+/");
                if (match.Success) url = match.Value;
                if (url == "") Display(() => status.Text = e.Data);
            };
            launch.ErrorDataReceived += (s, e) => { if (!String.IsNullOrEmpty(e.Data)) failure = e.Data; };
            launch.EnableRaisingEvents = true;
            launch.Exited += (s, e) => {
                launch.WaitForExit(); // Flush asynchronous output before reading the address.
                int code = launch.ExitCode;
                Display(() => {
                    starting = false; retry.Enabled = true;
                    if (code != 0 || url == "") { status.Text = failure != "" && Regex.IsMatch(failure, @"[\u4e00-\u9fff]") ? failure : "启动未完成，请点击“查看运行记录”，或将整个工具移到可写文件夹后重试。"; return; }
                    address.Text = url; address.Visible = true; open.Enabled = true;
                    status.Text = "工具已就绪。可以关闭此窗口，继续在网页中操作。";
                    if (!noOpen) OpenPage();
                });
            };
            launch.Start(); launch.BeginOutputReadLine(); launch.BeginErrorReadLine();
        } catch (Exception error) {
            starting = false; retry.Enabled = true;
            status.Text = "无法启动本地程序。请确认已完整解压，文件夹可写，且启动文件未被安全软件隔离。";
            try { Directory.CreateDirectory(Path.Combine(root, "logs")); File.AppendAllText(Path.Combine(root, "logs", "launcher.log"), DateTime.Now.ToString("s") + " 启动失败；异常类型：" + error.GetType().Name + Environment.NewLine); } catch { }
        }
    }
    void OpenPage() {
        try { Process.Start(new ProcessStartInfo(url) { UseShellExecute = true }); }
        catch { status.Text = "网页服务已就绪，但未能打开默认浏览器。请复制下方网址到浏览器打开。"; address.SelectAll(); address.Focus(); }
    }
    [STAThread] static void Main(string[] args) {
        Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false);
        Application.Run(new AssistantWindow(Array.IndexOf(args, "--no-open") >= 0));
    }
}
