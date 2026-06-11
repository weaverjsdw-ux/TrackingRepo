' Launches run_scheduled.ps1 with NO visible window (window-style 0 = hidden).
' Used as the scheduled-task action instead of calling powershell.exe directly,
' which avoids the brief console flash every interval while preserving exit code.
Dim shell, scriptDir, ps1, args, i, exitCode
Set shell = CreateObject("WScript.Shell")
scriptDir = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
ps1 = scriptDir & "run_scheduled.ps1"
args = ""
For i = 0 To WScript.Arguments.Count - 1
    args = args & " " & WScript.Arguments(i)
Next
exitCode = shell.Run("powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & ps1 & """" & args, 0, True)
WScript.Quit exitCode
