<#
.SYNOPSIS
  Configura o Forex Watch V2.3 na Cloudflare (somente PAPER e somente leitura).
.DESCRIPTION
  Execute após descompactar o ZIP da branch deploy-ready, em PowerShell.
  Sem -Activate: executa testes Node, autentica Cloudflare, cria KV caso necessário
  e publica apenas no modo OBSERVE (sem Telegram).
  Com -Activate: exige confirmação explícita, solicita segredos via prompt seguro
  do Wrangler e então habilita ALERT_MODE=active.
  NUNCA digite tokens no ChatGPT ou salve segredos no Git.
#>
[CmdletBinding()]
param([switch]$Activate)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Config = Join-Path $PSScriptRoot "wrangler_v23.toml"
$Worker = Join-Path $PSScriptRoot "forex_watch_v23_worker.mjs"
$Tests = Join-Path $Root "tests/test_forex_watch_v23_worker.mjs"

function Assert-Command($Name) {
  if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
    throw "Programa nao encontrado: $Name. Instale Node.js LTS e tente novamente."
  }
}

function Invoke-Checked([string[]]$Arguments) {
  & npx @Arguments
  if ($LASTEXITCODE -ne 0) { throw ("Falhou: npx "+($Arguments -join " ")) }
}

Assert-Command "node"
Assert-Command "npx"
if (-not (Test-Path $Worker) -or -not (Test-Path $Tests) -or -not (Test-Path $Config)) {
  throw "Arquivos do Forex Watch V2.3 nao encontrados no pacote."
}
Push-Location $Root
try {
  Write-Host "Forex Watch V2.3 | validando testes JavaScript (offline)..."
  & node --test $Tests
  if ($LASTEXITCODE -ne 0) { throw "Testes offline falharam. Implantacao bloqueada." }

  Write-Host "Autorizacao Cloudflare: use o navegador para entrar na SUA conta."
  Invoke-Checked @("wrangler","login")

  $toml = Get-Content -Raw -Encoding UTF8 $Config
  if ($toml -notmatch '(?m)^\[\[kv_namespaces\]\]') {
    Write-Host "Criando namespace KV e adicionando binding INCIDENT_KV..."
    Invoke-Checked @("wrangler","kv","namespace","create","INCIDENT_KV","--binding","INCIDENT_KV","--config",$Config,"--update-config")
  }

  if (-not $Activate) {
    Write-Host "Publicando SOMENTE modo observe. Nenhum alerta Telegram ativo."
    $current = Get-Content -Raw -Encoding UTF8 $Config
    if ($current -notmatch 'ALERT_MODE\s*=\s*"observe"') {
      $current = $current -replace 'ALERT_MODE\s*=\s*"active"','ALERT_MODE = "observe"'
      [System.IO.File]::WriteAllText($Config,$current,(New-Object System.Text.UTF8Encoding($false)))
    }
    Invoke-Checked @("wrangler","deploy","--config",$Config)
    Write-Host "Publicado em OBSERVE. Proxima etapa: crie bot Telegram e Healthchecks.io."
    Write-Host "Depois execute este script com -Activate, sem compartilhar secrets."
    exit 0
  }

  Write-Host ""
  Write-Host "ATIVACAO: requer Telegram Bot Token, Telegram Chat ID e Healthchecks Ping URL."
  Write-Host "Esses segredos serao digitados no prompt do Wrangler, nunca aqui no chat."
  $confirm = Read-Host "Digite ATIVAR para publicar alertas reais de monitoramento"
  if ($confirm -cne "ATIVAR") { throw "Ativacao cancelada. Nenhum alerta habilitado." }

  $current = Get-Content -Raw -Encoding UTF8 $Config
  if ($current -notmatch '(?m)^\[\[kv_namespaces\]\]') {
    throw "Binding KV nao configurado."
  }
  if ($current -notmatch 'ALERT_MODE\s*=\s*"observe"' -and
      $current -notmatch 'ALERT_MODE\s*=\s*"active"') {
    throw "ALERT_MODE nao encontrado no arquivo Wrangler."
  }
  Invoke-Checked @("wrangler","secret","put","TELEGRAM_BOT_TOKEN","--config",$Config)
  Invoke-Checked @("wrangler","secret","put","TELEGRAM_CHAT_ID","--config",$Config)
  Invoke-Checked @("wrangler","secret","put","MONITOR_HEARTBEAT_URL","--config",$Config)

  $current = Get-Content -Raw -Encoding UTF8 $Config
  $current = $current -replace 'ALERT_MODE\s*=\s*"observe"','ALERT_MODE = "active"'
  [System.IO.File]::WriteAllText($Config,$current,(New-Object System.Text.UTF8Encoding($false)))
  Invoke-Checked @("wrangler","deploy","--config",$Config)
  Write-Host "Cloudflare: alertas HABILITADOS na configuracao."
  Write-Host "IMPORTANTE: valide teste real de Telegram e Healthchecks antes de considerar monitor operacional."
}
finally { Pop-Location }
