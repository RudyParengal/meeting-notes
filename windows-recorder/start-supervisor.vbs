' Starts the Meeting Recorder (through its supervisor) with no visible window.
'
' Works from wherever this folder lives: nothing here is a fixed path.
' Run it by double-clicking, from the Startup shortcut the installer makes, or:
'     explorer.exe "<this folder>\start-supervisor.vbs"
' Explorer is the most reliable way to launch it from a script, because it runs
' the recorder exactly as Windows does at login, outside any sandbox or
' packaged app you might be working inside.

Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

here = fso.GetParentFolderName(WScript.ScriptFullName)
pyw  = here & "\venv\Scripts\pythonw.exe"
sup  = here & "\supervisor.py"

If Not fso.FileExists(pyw) Then
    MsgBox "Meeting Recorder is not installed in this folder yet." & vbCrLf & vbCrLf & _
           "Run install.ps1 first (see docs\INSTALL.md).", vbExclamation, "Meeting Recorder"
    WScript.Quit 1
End If

shell.CurrentDirectory = here
' 0 = hidden window, False = do not wait for it to finish
shell.Run Chr(34) & pyw & Chr(34) & " " & Chr(34) & sup & Chr(34), 0, False
