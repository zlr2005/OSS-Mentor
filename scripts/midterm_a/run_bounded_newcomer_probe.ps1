param([string]$PreviousDirectory = 'D:\OSS-Mentor\data\teacher_clickhouse\newcomer_20261021T162841Z', [string]$TaskPlan)
$ErrorActionPreference = 'Stop'
$taskWorkspace = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
Set-Location -LiteralPath $taskWorkspace
$probeArgs = @('-u',(Join-Path $PSScriptRoot 'run_bounded_newcomer_probe.py'),'--previous',$PreviousDirectory,'--workspace',$taskWorkspace)
if ($TaskPlan) { $probeArgs += @('--tasks-path',[IO.Path]::GetFullPath($TaskPlan)) }
& (Get-Command python).Source @probeArgs
