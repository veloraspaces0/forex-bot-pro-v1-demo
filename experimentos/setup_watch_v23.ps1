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
  # Wrangler sometimes writes a TOML array, rather than [[kv_namespaces]].
  return (($txt -match '(?m)^\s*\[\[kv_namespaces\]\]\s*
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

  Write-Host "Verificando e associando KV INCIDENT_KV sem duplicar recursos..."
  Ensure-KvBinding
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
) -or
          ($txt -match '(?m)^\s*kv_namespaces\s*='))
}
function Get-KvNamespaces {
  # Read remote first. A previous run may have CREATED a KV namespace but
  # failed to update the local TOML. Never create a second one blindly.
  $output = & npx.cmd --yes wrangler@4.157.0 kv namespace list --config $Config
  if ($LASTEXITCODE -ne 0) { throw "Nao foi possivel consultar KV na Cloudflare." }
  try {
    $items = ($output -join "`n") | ConvertFrom-Json
    return @($items)
  }
  catch {
    throw "Resposta KV nao reconhecida; nao criar recurso duplicado."
  }
}
function Ensure-KvBinding {
  if (Has-KvBinding) { return }
  $expected = "forex-watch-v23-paper-monitor-INCIDENT_KV"
  $namespaces = @(Get-KvNamespaces)
  $existing = @($namespaces | Where-Object {
    $_.title -eq $expected -or $_.title -eq "INCIDENT_KV"
  })
  if ($existing.Count -gt 1) {
    throw "Multiplos KV compativeis encontrados. Nao escolher automaticamente."
  }
  if ($existing.Count -eq 0) {
    Write-Host "Criando KV INCIDENT_KV (nenhum existente encontrado)..."
    Invoke-Wrangler -CommandArgs @("kv","namespace","create","INCIDENT_KV",
      "--binding","INCIDENT_KV","--config",$Config,"--update-config")
    if (Has-KvBinding) { return }
    # Some Wrangler releases create remotely but fail to update TOML.
    $namespaces = @(Get-KvNamespaces)
    $existing = @($namespaces | Where-Object {
      $_.title -eq $expected -or $_.title -eq "INCIDENT_KV"
    })
  }
  if ($existing.Count -ne 1) {
    throw "KV remoto nao localizado de forma unica. Nao criar outro automaticamente."
  }
  $id = [string]$existing[0].id
  if ($id -notmatch '^[0-9a-fA-F]{32}
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
) {
    throw "ID do KV invalido; nao escrever no TOML."
  }
  $txt = Get-Content -LiteralPath $Config -Encoding UTF8 -Raw
  if ($txt -match '(?m)^\s*(\[\[kv_namespaces\]\]|kv_namespaces\s*=)') {
    throw "KV TOML tem formato inesperado; revise antes de alterar."
  }
  $txt = $txt.TrimEnd() + "`r`n`r`n[[kv_namespaces]]`r`nbinding = `"INCIDENT_KV`"`r`nid = `"$id`"`r`n"
  [System.IO.File]::WriteAllText($Config,$txt,(New-Object System.Text.UTF8Encoding($false)))
  if (-not (Has-KvBinding)) { throw "Falha ao salvar o binding KV no TOML." }
  Write-Host "KV existente associado ao TOML; nenhum KV adicional criado."
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
