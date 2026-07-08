# TigerMemory example post-tool hook
#
# This example only prints a reminder. It does not modify files.

$commandText = [string]$env:CODEX_TOOL_COMMAND

if ($commandText -match "tm\s+ask") {
  Write-Host "TigerMemory: verify the answer cites local evidence before treating it as durable knowledge."
}

if ($commandText -match "tm\s+admin\s+propose") {
  Write-Host "TigerMemory: proposals are drafts under runtime/tigermemory/admin-proposals/. Human approval is required."
}

exit 0
