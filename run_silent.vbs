Set fso = CreateObject("Scripting.FileSystemObject")
Set WshShell = CreateObject("WScript.Shell")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
WshShell.Run chr(34) & scriptDir & "\start_bot.bat" & chr(34), 0
Set WshShell = Nothing
Set fso = Nothing

