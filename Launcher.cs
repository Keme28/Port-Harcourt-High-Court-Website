using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

namespace HighCourtPortal
{
    static class Program
    {
        [STAThread]
        static void Main()
        {
            try
            {
                string baseDir = AppDomain.CurrentDomain.BaseDirectory;
                string scriptPath = Path.Combine(baseDir, "Start-HCT6.ps1");

                if (!File.Exists(scriptPath))
                {
                    scriptPath = Path.Combine(baseDir, "hct6-file-portal", "Start-HCT6.ps1");
                }

                if (!File.Exists(scriptPath))
                {
                    MessageBox.Show(
                        "Could not find Start-HCT6.ps1.\nExpected in: " + baseDir,
                        "High Court Portal",
                        MessageBoxButtons.OK,
                        MessageBoxIcon.Error
                    );
                    return;
                }

                ProcessStartInfo psi = new ProcessStartInfo
                {
                    FileName = "powershell.exe",
                    Arguments = "-NoProfile -ExecutionPolicy Bypass -File \"" + scriptPath + "\"",
                    WorkingDirectory = Path.GetDirectoryName(scriptPath),
                    UseShellExecute = true,
                    WindowStyle = ProcessWindowStyle.Normal
                };

                Process.Start(psi);
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "Error launching High Court Portal:\n" + ex.Message,
                    "High Court Portal",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error
                );
            }
        }
    }
}
