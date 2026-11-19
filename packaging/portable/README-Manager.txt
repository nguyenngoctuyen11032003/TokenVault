TokenVault Manager - no-exe package
===================================

For PCs where Windows (Application Control / Smart App Control) refuses new
unsigned programs. It runs on the official Python runtime, whose python.exe and
pythonw.exe are signed by the Python Software Foundation.

Try it:   extract the whole ZIP, double-click Start-Manager.cmd.
Install:  right-click Install-Manager.ps1 -> Run with PowerShell
          (Start menu + Desktop shortcut, and ONE administrator prompt that adds the
          Windows Firewall rules so adapters on the LAN can connect).
Remove:   Uninstall-Manager.ps1

The Manager window needs the Microsoft Edge WebView2 runtime (included in
Windows 11). If it is missing: https://developer.microsoft.com/microsoft-edge/webview2/

Distribution: use a network share / USB / chat file transfer. A ZIP downloaded in a
browser is marked "from the internet"; Start-Manager.cmd and Install-Manager.ps1 clear
the mark (Unblock-File). Check the runtime:  runtime\python.exe -m tokenvault.manager --selftest
