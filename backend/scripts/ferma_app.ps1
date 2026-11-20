# ---------------------------------------------------------------------------
#  Ferma l'applicazione: worker, container del sito, Docker Desktop.
#
#  Dopo, le previsioni pianificate continuano a girare: `accoda.cmd` vede che
#  il database non risponde e lancia il comando direttamente.
#
#  Un job in corso viene interrotto: il worker, al prossimo avvio, lo segna
#  come fallito. Per questo lo script prima controlla e chiede conferma.
#
#  Opzione:  -LasciaDocker   ferma sito e worker ma lascia Docker acceso
#            (per chi lo usa anche per altri progetti).
# ---------------------------------------------------------------------------
param([switch]$LasciaDocker)
$ErrorActionPreference = "Continue"
$repo = Resolve-Path (Join-Path $PSScriptRoot "..\..")

$worker = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
    Where-Object { $_.CommandLine -like "*backend.worker*" }
# Solo i comandi lanciati DAL worker. Un predict_round partito direttamente
# dallo scheduler (Docker spento, ripiego di accoda.cmd) non va toccato.
$pidWorker = @($worker | ForEach-Object { $_.ProcessId })
$figli = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
    Where-Object { $_.ParentProcessId -in $pidWorker }
if ($figli) {
    $risposta = Read-Host "Il worker sta eseguendo un comando della pipeline. Interromperlo? (s/N)"
    if ($risposta -notin @("s", "S", "si", "SI")) { Write-Host "Niente fermato."; exit 1 }
}

# 1. Il worker (prima del database, o al suo ultimo giro troverebbe il DB giu')
Stop-ScheduledTask -TaskName "AI_Naples worker" -ErrorAction SilentlyContinue
@($worker) + @($figli) | Where-Object { $_ } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Write-Host "Worker: fermato"

# 2. I container. `stop` e non `down`: i dati restano nei volumi, e con
#    `restart: unless-stopped` un container fermato a mano non riparte da solo.
cmd /c "docker info >nul 2>&1"
if ($LASTEXITCODE -eq 0) {
    Push-Location $repo
    try { docker compose stop 2>&1 | Out-Host } finally { Pop-Location }
    Write-Host "Sito: fermato"
}

# 3. Docker Desktop e le sue due distribuzioni WSL, e solo quelle: un
#    `wsl --shutdown` spegnerebbe anche Ubuntu e qualunque altra distribuzione.
if (-not $LasciaDocker) {
    Get-Process -Name "Docker Desktop", "com.docker.*" -ErrorAction SilentlyContinue |
        Stop-Process -Force -ErrorAction SilentlyContinue
    wsl --terminate docker-desktop *> $null
    wsl --terminate docker-desktop-data *> $null
    Write-Host "Docker: spento"
}
Write-Host "Fatto. Le previsioni pianificate continuano a girare senza Docker."
