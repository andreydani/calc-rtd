<#
.SYNOPSIS
  Compiles and registers the sample RTD server (ProgID CalcRtd.Sample).

.DESCRIPTION
  Uses the C# compiler and regasm from the .NET Framework that ships with
  Windows. Registration writes to HKEY_CLASSES_ROOT, so run this from an
  elevated PowerShell. Pick the architecture of the program that will load
  the server (the bridge's Python or executable, or Excel).

.EXAMPLE
  .\build.ps1 -Arch x64
  .\build.ps1 -Arch x64 -Unregister
#>
param(
    [ValidateSet("x64", "x86")][string]$Arch = "x64",
    [switch]$Unregister
)
$ErrorActionPreference = "Stop"

$framework = if ($Arch -eq "x64") { "Framework64" } else { "Framework" }
$tools = Join-Path $env:WINDIR "Microsoft.NET\$framework\v4.0.30319"
$dll = Join-Path $PSScriptRoot "bin\CalcRtdSample.dll"

if ($Unregister) {
    & "$tools\regasm.exe" $dll /unregister /nologo
    exit $LASTEXITCODE
}

New-Item -ItemType Directory -Force (Split-Path $dll) | Out-Null
& "$tools\csc.exe" /nologo /target:library "/out:$dll" /r:System.Windows.Forms.dll `
    (Join-Path $PSScriptRoot "SampleRtdServer.cs")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& "$tools\regasm.exe" $dll /codebase /nologo
exit $LASTEXITCODE
