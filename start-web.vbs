Option Explicit

Dim shell, fileSystem, repoRoot, scriptPath, command
Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")

repoRoot = fileSystem.GetParentFolderName(WScript.ScriptFullName)
scriptPath = fileSystem.BuildPath(repoRoot, "start-web.ps1")

command = "powershell.exe -NoProfile -WindowStyle Hidden -STA " & _
          "-ExecutionPolicy Bypass -File """ & scriptPath & """"

shell.Run command, 0, False
