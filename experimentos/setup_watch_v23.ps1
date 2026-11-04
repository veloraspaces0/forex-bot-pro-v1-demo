<#
.SYNOPSIS
  Forex Watch V2.3: safe Windows installer; PAPER ONLY / read-only bot.
.DESCRIPTION
  Default: offline tests, Wrangler dry-run, Cloudflare login, KV setup,
  deploy OBSERVE only (no Telegram alerts, no Healthchecks heartbeat).
  -Activate: confirms with operator, deploys OBSERVE first, then configures
  three secrets locally via Wrangler prompts, deploys ACTIVE only at the end.
  -PreflightOnly: offline tests and Wrangler compile; no login/remote writes.
  NEVER paste credentials into ChatGPT, commits or source files.
#>
[CmdletBinding()]
param(
  [switch]$Activate,
  [switch]$PreflightOnly
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
if ($Activate -and $PreflightOnly) {
  throw "Escolha apenas -PreflightOnly OU -Activate."
}
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Config = Join-Path $PSScriptRoot "wrangler_v23.toml"
$Worker = Join-Path $PSScriptRoot "forex_watch_v23_worker.mjs"
$Tests = Join-Path $Root "tests/test_forex_watch_v23_worker.mjs"
$PublishedActive = $false

function Invoke-Wrangler {
  param([string[]]$CommandArgs)
  & npx.cmd --yes wrangler@4.157.0 @CommandArgs
  if ($LASTEXITCODE -ne 0) {
    throw "Falha no Wrangler ao executar: $($CommandArgs[0]) $($CommandArgs[1])"
  }
}
function Set-AlertMode {
  param([ValidateSet("observe","active")][string]$Mode)
  $txt = Get-Content -LiteralPath $Config -Encoding UTF8 -Raw
  if ($txt -notmatch '(?m)^ALERT_MODE[ \t]*=[ \t]*"(observe|active)"[ \t]*$') {
    throw "ALERT_MODE ausente/invalido no arquivo TOML. Nao publicar."
  }
  $txt = [regex]::Replace($txt, '(?m)^ALERT_MODE[ \t]*=[ \t]*"(observe|active)"[ \t]*$',
    ('ALERT_MODE = "'+$Mode+'"'))
  [System.IO.File]::WriteAllText($Config,$txt,(New-Object System.Text.UTF8Encoding($false)))
}
function Has-KvBinding {
  $txt = Get-Content -LiteralPath $Config -Encoding UTF8 -Raw
  return ($txt -match '(?m)^\s*\[\[kv_namespaces\]\]\s*$')
}

foreach ($name in @("node.exe","npx.cmd")) {
  if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
    throw "$name nao encontrado. Instale Node.js LTS (20+) e abra o PowerShell novamente."
  }
}
if (-not ((Test-Path $Worker) -and (Test-Path $Tests) -and (Test-Path $Config))) {
  throw "O pacote esta incompleto. Baixe novamente o ZIP da branch de revisao final."
}
$versionText = (& node.exe --version).Trim() -replace '^v',''
if ([int]$versionText.Split('.')[0] -lt 20) {
  throw "Node.js 20+ e necessario. Versao atual: $versionText"
}
Push-Location $Root
try {
  Write-Host "[1/5] Executando testes offline..."
  & node.exe --test $Tests
  if ($LASTEXITCODE -ne 0) { throw "Testes falharam. Implantacao bloqueada." }

  Write-Host "[2/5] Compilando Worker (dry-run, sem publicar)..."
  Invoke-Wrangler -CommandArgs @("deploy","--dry-run","--config",$Config)
  if ($PreflightOnly) {
    Write-Host "Preflight aprovado. NENHUMA alteracao feita na Cloudflare."
    return
  }

  if ($Activate) {
    Write-Host "ATENCAO: o processo publica primeiro OBSERVE (alertas off)."
    Write-Host "Secrets serao informados na sua maquina pelo CLI oficial."
    $confirmation = Read-Host "Digite ATIVAR para configurar os alertas externos"
    if ($confirmation -cne "ATIVAR") { throw "Ativacao cancelada." }
  }

  Write-Host "[3/5] Login Cloudflare, pelo navegador oficial..."
  Invoke-Wrangler -CommandArgs @("login")

  if (-not (Has-KvBinding)) {
    Write-Host "Criando/bindando KV INCIDENT_KV na sua conta Cloudflare..."
    Invoke-Wrangler -CommandArgs @("kv","namespace","create","INCIDENT_KV",
      "--binding","INCIDENT_KV","--config",$Config,"--update-config")
    if (-not (Has-KvBinding)) { throw "Binding KV nao foi gravado no TOML." }
  }
  Set-AlertMode "observe"
  Write-Host "[4/5] Publicando modo OBSERVE: sem Telegram e sem heartbeat..."
  Invoke-Wrangler -CommandArgs @("deploy","--config",$Config)

  if (-not $Activate) {
    Write-Host "OBSERVE publicado. O monitor externo NAO notifica neste modo."
    Write-Host "Configure Telegram e Healthchecks.io antes de executar com -Activate."
    return
  }

  Write-Host "Confirme que criou Healthchecks.io e habilitou alertas no canal externo."
  Write-Host "Iniciando configuracao dos tres secrets (nao serao mostrados aqui)..."
  # Wrangler secret put immediately deploys a new Worker version.
  # Keep local and remote ALERT_MODE=observe throughout ALL THREE operations.
  Invoke-Wrangler -CommandArgs @("secret","put","TELEGRAM_BOT_TOKEN","--config",$Config)
  Invoke-Wrangler -CommandArgs @("secret","put","TELEGRAM_CHAT_ID","--config",$Config)
  Invoke-Wrangler -CommandArgs @("secret","put","MONITOR_HEARTBEAT_URL","--config",$Config)

  Write-Host "[5/5] Validando nova configuracao antes da publicacao ACTIVE..."
  Set-AlertMode "active"
  Invoke-Wrangler -CommandArgs @("deploy","--dry-run","--config",$Config)
  Invoke-Wrangler -CommandArgs @("deploy","--config",$Config)
  $PublishedActive = $true
  Write-Host "ACTIVE publicado. Falta COMPROVAR entregas reais Telegram/Healthchecks."
}
finally {
  if (-not $PublishedActive -and (Test-Path $Config)) {
    # Fail closed locally. Secret uploads were done while remote was observe.
    try { Set-AlertMode "observe" }
    catch { Write-Warning "Confira manualmente ALERT_MODE no TOML antes de reutilizar." }
  }
  Pop-Location
}
