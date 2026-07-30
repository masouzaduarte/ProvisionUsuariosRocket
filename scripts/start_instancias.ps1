# Sobe 3 painéis em paralelo (MARCO / EVALDO / DIEGO) compartilhando o mesmo checkpoint.
# Uso: .\scripts\start_instancias.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    Write-Error "Venv não encontrado: $Python"
}

$instancias = @(
    @{ Dev = "MARCO";  Port = 5055 },
    @{ Dev = "EVALDO"; Port = 5056 },
    @{ Dev = "DIEGO";  Port = 5057 }
)

foreach ($i in $instancias) {
    $env:WEB_PORT = "$($i.Port)"
    $env:PROVISION_DEV = $i.Dev
    $env:PYTHONPATH = $Root
    Write-Host "Iniciando $($i.Dev) em http://127.0.0.1:$($i.Port)" -ForegroundColor Cyan
    Start-Process -FilePath $Python -ArgumentList "run_web.py" -WorkingDirectory $Root `
        -WindowStyle Normal
    Start-Sleep -Seconds 1
}

Write-Host ""
Write-Host "Painéis:" -ForegroundColor Green
Write-Host "  MARCO  -> http://127.0.0.1:5055"
Write-Host "  EVALDO -> http://127.0.0.1:5056"
Write-Host "  DIEGO  -> http://127.0.0.1:5057"
Write-Host ""
Write-Host "Cada um filtra os lotes do respectivo desenvolvedor."
Write-Host "Checkpoint compartilhado (SQLite WAL) — não processe o mesmo lote em 2 painéis."
