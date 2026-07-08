# TigerMemory example pre-tool hook
#
# This example is passive guidance for advanced users. It does not approve Wiki
# writes and does not contain secrets.

$commandText = [string]$env:CODEX_TOOL_COMMAND

if ($commandText -match "tm\s+admin\s+approve") {
  Write-Error "TigerMemory: tm admin approve is human-only. Review the proposal and approve manually."
  exit 1
}

if ($commandText -match "tm\s+admin\s+propose") {
  Write-Host "TigerMemory: proposal mode is allowed. Check route, source refs, sensitivity, stability, and evidence quality before approval."
}

exit 0
