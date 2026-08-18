using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using PpcCtrls;

namespace B650Video
{
    static class Program
    {
        [STAThread]
        static void Main(string[] args)
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            bool createdNew;
            using (Mutex mutex = new Mutex(true, "Global\\B650VideoMonitor", out createdNew))
            {
                if (!createdNew)
                {
                    // paperChart の new/append 等で二重起動された場合、既存インスタンスを生かす
                    return;
                }
                Application.Run(new MainForm(args));
            }
        }
    }

    public class MainForm : Form
    {
        private PpcCtrl ppc;
        private Process py;
        private System.Windows.Forms.Timer timer;
        private string configPath;
        private string workingDir;
        private string pythonExe;
        private string moduleName;
        private int deviceIndex;
        private double intervalSec = 1.0;
        private bool suppressZero;
        private readonly List<ItemMap> items = new List<ItemMap>();
        private readonly List<DateTime> lastSend = new List<DateTime>();
        private readonly JavaScriptSerializer jss = new JavaScriptSerializer();
        private string lastJson = "";
        private bool sending;
        private bool debug;
        private TextBox debugBox;

        private struct ItemMap
        {
            public string Source;
            public string SubKey;
            public string Target;
            public string Unit;
            public string Type;
            public double IntervalSec;
        }

        public MainForm(string[] args)
        {
            ShowInTaskbar = false;
            WindowState = FormWindowState.Minimized;
            Opacity = 0;
            FormBorderStyle = FormBorderStyle.None;
            Size = new Size(1, 1);
            Load += MainForm_Load;
            FormClosing += MainForm_FormClosing;
        }

        private void MainForm_Load(object sender, EventArgs e)
        {
            Hide();
            try { Init(); }
            catch (Exception ex)
            {
                MessageBox.Show("B650Video init error: " + ex, "B650Video",
                    MessageBoxButtons.OK, MessageBoxIcon.Error);
                Application.Exit();
            }
        }

        private void Init()
        {
            string exeDir = Path.GetDirectoryName(Application.ExecutablePath);

            string cfg = Path.Combine(exeDir, "B650Video.txt");
            if (!File.Exists(cfg))
                cfg = Path.GetFullPath(Path.Combine(exeDir, @"..\..\CONF\monitors\B650Video.txt"));
            if (!File.Exists(cfg))
                throw new FileNotFoundException("B650Video.txt not found");

            ParseConfig(cfg);

            if (debug)
            {
                ShowInTaskbar = true;
                Opacity = 1;
                WindowState = FormWindowState.Normal;
                Size = new Size(600, 400);
                FormBorderStyle = FormBorderStyle.Sizable;
                debugBox = new TextBox
                {
                    Multiline = true,
                    Dock = DockStyle.Fill,
                    ScrollBars = ScrollBars.Vertical,
                    Font = new Font("Consolas", 9)
                };
                Controls.Add(debugBox);
                Show();
            }

            string cfgName = Path.GetFileName(cfg);
            ppc = new PpcCtrl(this, cfgName);
            if (!ppc.CheckEnvFile(cfgName))
                throw new InvalidOperationException("CheckEnvFile failed");

            ppc.CheckStartupEnvironment("B650Video", false);
            ppc.Init();
            if (!ppc.AttachRunningNv())
                Log("paperChart not found; will retry on each send");

            StartPython();

            timer = new System.Windows.Forms.Timer
            {
                Interval = Math.Max(100, (int)(intervalSec * 1000))
            };
            timer.Tick += (s, ev) => { if (!sending) SendVitals(); };
            timer.Start();
        }

        private void ParseConfig(string path)
        {
            Encoding enc;
            try { enc = Encoding.GetEncoding("Shift_JIS"); }
            catch { enc = Encoding.UTF8; }

            foreach (string raw in File.ReadAllLines(path, enc))
            {
                string line = raw.Trim();
                if (string.IsNullOrEmpty(line)) continue;

                int cmt = line.IndexOf("//");
                if (cmt == 0) continue;
                if (cmt > 0) line = line.Substring(0, cmt).Trim();
                if (line.StartsWith("#")) continue;

                int eq = line.IndexOf('=');
                if (eq < 0) continue;

                string key = line.Substring(0, eq).Trim();
                string val = line.Substring(eq + 1).Trim();

                if (key.StartsWith("@"))
                {
                    string k = key.Substring(1).ToLowerInvariant();
                    switch (k)
                    {
                        case "python": pythonExe = val; break;
                        case "module": moduleName = val; break;
                        case "config": configPath = val; break;
                        case "device": int.TryParse(val, out deviceIndex); break;
                        case "interval": double.TryParse(val, out intervalSec); break;
                        case "workingdir": workingDir = val; break;
                        case "debug": bool.TryParse(val, out debug); break;
                    }
                    continue;
                }

                string low = key.ToLowerInvariant();
                if (low == "startup" || low == "suppresszero" || low == "resp"
                    || low == "resp" || low == "itemseparator" || low == "port"
                    || low == "baud" || low == "passivemode")
                {
                    if (low == "suppresszero")
                        bool.TryParse(val, out suppressZero);
                    continue;
                }

                string src = key;
                string type = "single";
                if (src.Contains("|"))
                {
                    int bar = src.IndexOf('|');
                    string suffix = src.Substring(bar + 1).ToLowerInvariant();
                    if (suffix == "sd") type = "sd";
                    else if (suffix == "sdm") type = "sdm";
                    src = src.Substring(0, bar);
                }

                string subKey = null;
                if (src.Contains("."))
                {
                    int dot = src.IndexOf('.');
                    subKey = src.Substring(dot + 1);
                    src = src.Substring(0, dot);
                }

                string[] parts = val.Split(new char[] { ',' }, 3);
                string target = parts[0].Trim();
                string unit = parts.Length >= 2 ? parts[1].Trim() : "";
                double itemInterval = 0.0;
                if (parts.Length >= 3)
                    double.TryParse(parts[2].Trim(), System.Globalization.NumberStyles.Any,
                        System.Globalization.CultureInfo.InvariantCulture, out itemInterval);

                items.Add(new ItemMap
                {
                    Source = src.ToLowerInvariant(),
                    SubKey = subKey,
                    Target = target,
                    Unit = unit,
                    Type = type,
                    IntervalSec = itemInterval
                });
                lastSend.Add(DateTime.MinValue);
            }

            if (string.IsNullOrEmpty(pythonExe)) pythonExe = "python";
            if (string.IsNullOrEmpty(moduleName)) moduleName = "anesthesia_record.monitor_video";
            if (string.IsNullOrEmpty(configPath)) configPath = @"paperchart\b650_video.yaml";
            if (string.IsNullOrEmpty(workingDir))
                workingDir = @"..\..\anesthesia-record";

            string exeDir = Path.GetDirectoryName(Application.ExecutablePath);
            workingDir = Path.GetFullPath(Path.Combine(exeDir, workingDir));
            pythonExe = ResolvePythonExe(exeDir, workingDir, pythonExe);
        }

        private string ResolvePythonExe(string exeDir, string workingDir, string candidate)
        {
            if (string.IsNullOrEmpty(candidate)) candidate = "python";
            string[] tryPaths = new string[]
            {
                candidate,
                Path.GetFullPath(Path.Combine(exeDir, candidate)),
                Path.GetFullPath(Path.Combine(workingDir, ".venv", "Scripts", "python.exe")),
                Path.GetFullPath(Path.Combine(workingDir, "..", ".venv", "Scripts", "python.exe")),
                @"python",
                @"py",
                @"python3"
            };
            foreach (string p in tryPaths)
            {
                string path = p;
                try { path = Path.GetFullPath(path); } catch { }
                if (File.Exists(path))
                    return path;
            }
            return candidate;
        }

        private void StartPython()
        {
            if (!Directory.Exists(workingDir))
                throw new DirectoryNotFoundException(
                    "Working directory not found: " + workingDir +
                    ". Please copy the anesthesia-record folder next to B650Video.exe or set @WorkingDir in B650Video.txt.");

            string args;
            string mod = moduleName.Trim();
            if (mod.EndsWith(".py", StringComparison.OrdinalIgnoreCase)
                || mod.Contains("\\") || mod.Contains("/"))
            {
                args = string.Format("\"{0}\" --config \"{1}\" --device {2} --interval {3} --json",
                    mod, configPath, deviceIndex, intervalSec.ToString(System.Globalization.CultureInfo.InvariantCulture));
            }
            else
            {
                args = string.Format("-m {0} --config \"{1}\" --device {2} --interval {3} --json",
                    mod, configPath, deviceIndex, intervalSec.ToString(System.Globalization.CultureInfo.InvariantCulture));
            }

            var psi = new ProcessStartInfo(pythonExe, args)
            {
                UseShellExecute = false,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                CreateNoWindow = true,
                WorkingDirectory = workingDir,
            };
            psi.EnvironmentVariables["CONDA_DLL_SEARCH_MODIFICATION_ENABLE"] = "1";
            psi.EnvironmentVariables["KMP_DUPLICATE_LIB_OK"] = "TRUE";

            try
            {
                py = Process.Start(psi);
                py.OutputDataReceived += (s, e) =>
                {
                    if (!string.IsNullOrEmpty(e.Data)) lastJson = e.Data;
                };
                py.ErrorDataReceived += (s, e) =>
                {
                    if (!string.IsNullOrEmpty(e.Data)) Log(e.Data);
                };
                py.BeginOutputReadLine();
                py.BeginErrorReadLine();
            }
            catch (Exception ex)
            {
                Log("StartPython failed: " + ex);
                throw;
            }
        }

        private void SendVitals()
        {
            sending = true;
            try
            {
                if (py != null && py.HasExited)
                {
                    Log("Python process exited; will restart in 2 seconds");
                    lastJson = "";
                    try
                    {
                        if (py != null)
                        {
                            py.WaitForExit(2000);
                            py.Dispose();
                            py = null;
                        }
                    }
                    catch { }
                    System.Threading.Thread.Sleep(2000);
                    StartPython();
                }

                string json = lastJson;
                if (string.IsNullOrEmpty(json)) return;

                Dictionary<string, object> root;
                try
                {
                    root = jss.Deserialize<Dictionary<string, object>>(json);
                }
                catch (Exception ex)
                {
                    Log("JSON parse error: " + ex.Message + " | " + json);
                    return;
                }
                if (root == null) return;
                Log("JSON parsed: " + json);

                DateTime now = DateTime.Now;
                for (int i = 0; i < items.Count; i++)
                {
                    ItemMap it = items[i];
                    if (!root.ContainsKey(it.Source)) continue;
                    object val = root[it.Source];
                    if (val == null) continue;

                    double effectiveInterval = it.IntervalSec > 0 ? it.IntervalSec : intervalSec;
                    if ((now - lastSend[i]).TotalSeconds < effectiveInterval) continue;

                    if (it.Source == "nibp" || it.Source == "nibp")
                    {
                        var d = val as Dictionary<string, object>;
                        if (d == null) continue;
                        bool measuring = false;
                        if (d.ContainsKey("measuring")) measuring = ToBool(d["measuring"]);
                        if (measuring) continue;

                        double? sys = GetNum(d, "sys");
                        double? dia = GetNum(d, "dia");
                        double? map = GetNum(d, "map");
                        if (!sys.HasValue || !dia.HasValue) continue;

                        // 大きい順に sys, map, dia となるよう再配置
                        if (sys.HasValue && dia.HasValue && map.HasValue)
                        {
                            double[] nums = new double[] { sys.Value, dia.Value, map.Value };
                            Array.Sort(nums);
                            sys = nums[2];
                            map = nums[1];
                            dia = nums[0];
                        }
                        else if (sys.Value < dia.Value)
                        {
                            double tmp = sys.Value;
                            sys = dia.Value;
                            dia = tmp;
                        }

                        if (!map.HasValue && it.Type == "sdm")
                            map = (sys.Value + 2.0 * dia.Value) / 3.0;

                        double?[] arr;
                        if (it.Type == "sdm") arr = new double?[] { sys, dia, map };
                        else arr = new double?[] { sys, dia };
                        Send(it.Target, it.Unit, arr);
                        lastSend[i] = now;
                    }
                    else if (!string.IsNullOrEmpty(it.SubKey) || it.Source == "spo2_waveform" || it.Source == "pleth")
                    {
                        var d = val as Dictionary<string, object>;
                        if (d == null) continue;
                        string sub = it.SubKey ?? it.Source;
                        double? v = GetNum(d, sub);
                        if (v.HasValue && (!suppressZero || v.Value != 0))
                        {
                            Send(it.Target, it.Unit, v.Value);
                            lastSend[i] = now;
                        }
                    }
                    else
                    {
                        double? v = GetNumScalar(val);
                        if (v.HasValue && (!suppressZero || v.Value != 0))
                        {
                            Send(it.Target, it.Unit, v.Value);
                            lastSend[i] = now;
                        }
                    }
                }
            }
            catch (Exception ex)
            {
                Log("SendVitals: " + ex);
            }
            finally
            {
                sending = false;
            }
        }

        private double? GetNum(Dictionary<string, object> d, string key)
        {
            if (d == null || !d.ContainsKey(key)) return null;
            return GetNumScalar(d[key]);
        }

        private double? GetNumScalar(object o)
        {
            if (o == null) return null;
            if (o is double d) return d;
            if (o is float f) return f;
            if (o is int i) return i;
            if (o is long l) return l;
            if (o is decimal m) return (double)m;

            string s = Convert.ToString(o, System.Globalization.CultureInfo.InvariantCulture);
            double dd;
            if (double.TryParse(s,
                System.Globalization.NumberStyles.Any,
                System.Globalization.CultureInfo.InvariantCulture,
                out dd)) return dd;
            return null;
        }

        private bool ToBool(object o)
        {
            if (o == null) return false;
            if (o is bool b) return b;
            string s = Convert.ToString(o).ToLowerInvariant();
            return s == "true" || s == "1" || s == "yes";
        }

        private void Send(string item, string unit, double value)
        {
            uint ucode = Const.UnitCode(unit);
            Log(string.Format("SEND item={0} unit={1} ucode=0x{2:X} value={3}", item, unit, ucode, value));
            PpcNumData pnd = new PpcNumData(item, ucode, value, 0);
            ppc.SendNumData(pnd, DateTime.Now);
        }

        private void Send(string item, string unit, double?[] values)
        {
            uint ucode = Const.UnitCode(unit);
            Log(string.Format("SEND item={0} unit={1} ucode=0x{2:X} values=[{3}]", item, unit, ucode, string.Join(",", values)));
            PpcNumData pnd = new PpcNumData(item, ucode, values);
            ppc.SendNumData(pnd, DateTime.Now);
        }

        private void Log(string msg)
        {
            string line = DateTime.Now.ToString("s") + " " + msg;
            try
            {
                string path = Path.Combine(Path.GetDirectoryName(Application.ExecutablePath), "B650Video.log");
                File.AppendAllText(path, line + Environment.NewLine);
            }
            catch { }
            try
            {
                if (debugBox != null)
                {
                    if (debugBox.InvokeRequired)
                        debugBox.BeginInvoke(new Action(() => debugBox.AppendText(line + Environment.NewLine)));
                    else
                        debugBox.AppendText(line + Environment.NewLine);
                }
            }
            catch { }
        }

        private void MainForm_FormClosing(object sender, FormClosingEventArgs e)
        {
            timer?.Stop();
            try
            {
                if (py != null && !py.HasExited)
                {
                    py.Kill();
                    py.WaitForExit(2000);
                }
            }
            catch { }
            try { py?.Dispose(); } catch { }
            try { ppc?.Close(); } catch { }
        }
    }
}
