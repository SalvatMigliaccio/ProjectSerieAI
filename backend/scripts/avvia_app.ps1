# ---------------------------------------------------------------------------
#  Avvia l'applicazione: Docker, i container del sito, il worker, il browser.
#
#  PERCHE' A MANO E NON ALL'ACCESSO (20 novembre 2026). Docker e il worker
#  servono solo quando si usa il sito: il pannello, i pulsanti della pipeline.
#  Le previsioni pianificate NON ne hanno bisogno: `accoda.cmd` le consegna al
#  worker se c'e', e altrimenti le esegue direttamente come prima. Tenere
#  Docker acceso tutto il giorno costava memoria per niente.
#
#  Idempotente: se qualcosa e' gia' acceso, lo lascia com'e'.
#  Il contrario e' ferma_app.ps1.
# ---------------------------------------------------------------------------
# "Continue" e non "Stop": in PowerShell 5.1, con "Stop", ogni riga che docker
# scrive su stderr (anche il semplice avanzamento di `compose up`) diventa un
# errore che interrompe lo script. Gli esiti si controllano con $LASTEXITCODE.
$ErrorActionPreference = "Continue"
$repo = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$dockerDesktop = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
$porta = if ($env:FRONTEND_PORT) { $env:FRONTEND_PORT } else { "5173" }

function Test-Docker {
    cmd /c "docker info >nul 2>&1"
    return $LASTEXITCODE -eq 0
}

# 1. Docker
if (-not (Test-Docker)) {
    if (-not (Test-Path $dockerDesktop)) { throw "Docker Desktop non trovato in $dockerDesktop" }
    Write-Host "Avvio Docker Desktop..."
    Start-Process $dockerDesktop
    $limite = (Get-Date).AddMinutes(3)
    while (-not (Test-Docker)) {
        if ((Get-Date) -gt $limite) { throw "Docker non risponde dopo 3 minuti" }
        Start-Sleep -Seconds 3
    }
}
Write-Host "Docker: pronto"

# 2. I container del sito (db, mail, api, frontend)
Push-Location $repo
try {
    docker compose up -d --wait 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "docker compose up non e' riuscito" }
} finally { Pop-Location }
Write-Host "Sito: in esecuzione"

# 3. Il worker della pipeline: parte dopo il database, che ora e' sano
#    (--wait aspetta i healthcheck).
Start-ScheduledTask -TaskName "AI_Naples worker"
Write-Host "Worker: avviato"

# 4. Il sito, su 127.0.0.1 e non su localhost: deve essere lo stesso host di
#    AI_NAPLES_PUBLIC_URL, o l'accesso con Google perde il cookie del flusso
#    al ritorno (per il browser sono due siti diversi).
Start-Process "http://127.0.0.1:$porta/#/dashboard"
Write-Host "Fatto. Per spegnere tutto: backend\scripts\ferma_app.cmd"
