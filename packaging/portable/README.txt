TokenVault Adapter - no-exe package
===================================

1. Extract this whole ZIP to a normal folder (for example Downloads\TokenVault).
2. Double-click Install.cmd. No administrator rights are needed.
3. Read the consent screen (it lists exactly what is sent) and press Accept.

Nothing in this package is an unsigned program: it runs the official Python
runtime (python.exe / pythonw.exe are signed by the Python Software Foundation)
and TokenVault's own Python source code, which you can read in runtime\app.

Tips
  * Hand the ZIP over via a network share, USB stick or chat file transfer. A file
    downloaded in a browser carries a "Mark of the Web"; Install.cmd removes it
    (Unblock-File), or run:  Get-ChildItem -Recurse | Unblock-File
  * This ZIP contains an enrollment credential for one team. Share it only with
    that team. It works only against your Manager and can be revoked there.
  * Remove: Uninstall.cmd (add  -RemoveData  to delete the collected data too).
