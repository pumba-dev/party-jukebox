#Requires -Version 5.1
<#
    start.ps1 — um comando inicia tudo (RNF-25).

    Builda o frontend se preciso, sobe o uvicorn na :80 e imprime, grande, o IP e a URL que
    vão no QR code: é a primeira coisa que você precisa na festa e a mais chata de descobrir
    na hora (.docs/03-arquitetura.md §8).

    Nenhum passo pede elevação de privilégio (RNF-28). No Windows, ao contrário do Unix,
    portas abaixo de 1024 não exigem administrador.

    -Tv abre o monitor sozinho, no Chrome, com a política de autoplay relaxada — sem isso o
    karaokê não faz som. A linha de comando é impressa sempre, com ou sem o switch.
#>
param(
    # Abre a /tv em quiosque quando o servidor subir. Opt-in: a festa que não usa karaokê não
    # ganha um navegador aberto por cima do que já estava na tela.
    [switch]$Tv
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$api = Join-Path $root 'api'
$web = Join-Path $root 'web'
$py = Join-Path $api '.venv\Scripts\python.exe'

function Fail($msg, $fix) {
    Write-Host ''
    Write-Host "  $msg" -ForegroundColor Red
    Write-Host "  $fix" -ForegroundColor Yellow
    Write-Host ''
    exit 1
}

# --- pré-requisitos -------------------------------------------------------------------------

if (-not (Test-Path $py)) {
    Fail 'a venv da api não existe.' 'cd api; python -m venv .venv; .\.venv\Scripts\pip install -e .'
}
if (-not (Test-Path (Join-Path $api '.env'))) {
    Fail 'api\.env não existe.' 'copy api\.env.example api\.env   e preencha as chaves do Spotify'
}
if (-not (Test-Path (Join-Path $api '.tokens.json'))) {
    Write-Host ''
    Write-Host '  ⚠  api\.tokens.json não existe: o Spotify não está autorizado.' -ForegroundColor Yellow
    Write-Host '     A API sobe, mas nada vai tocar até rodar:' -ForegroundColor Yellow
    Write-Host '     cd api; .\.venv\Scripts\python scripts\authorize.py' -ForegroundColor Yellow
}

# --- build do frontend, se preciso ----------------------------------------------------------

$dist = Join-Path $web 'dist\index.html'
$precisaBuild = $true
if (Test-Path $dist) {
    $construido = (Get-Item $dist).LastWriteTime
    $fontes = Get-ChildItem -Path (Join-Path $web 'src'), (Join-Path $web 'index.html'), (Join-Path $web 'package.json') -Recurse -File -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($fontes -and $fontes.LastWriteTime -le $construido) { $precisaBuild = $false }
}
if ($precisaBuild) {
    Write-Host ''
    Write-Host '  gerando o contrato do OpenAPI…' -ForegroundColor Cyan
    # Sem servidor de pé: o FastAPI monta a spec offline. É o que evita o ovo e a galinha de
    # buildar o frontend antes de subir a API (ADR-006).
    Push-Location $api
    try {
        & $py scripts\dump_openapi.py
        if ($LASTEXITCODE -ne 0) { Fail 'não consegui gerar o openapi.json.' 'veja o erro acima' }
    }
    finally { Pop-Location }

    Write-Host '  buildando o frontend…' -ForegroundColor Cyan
    Push-Location $web
    try {
        if (-not (Test-Path 'node_modules')) { & npm install --no-fund --no-audit }
        & npm run build
        if ($LASTEXITCODE -ne 0) { Fail 'o build do frontend falhou.' 'veja o erro acima; `cd web; npm run build` reproduz' }
    }
    finally { Pop-Location }
}
else {
    Write-Host '  frontend já buildado (nada mudou desde o último build).' -ForegroundColor DarkGray
}

# --- porta ----------------------------------------------------------------------------------

$porta = 80
$envPorta = Select-String -Path (Join-Path $api '.env') -Pattern '^\s*BIND_PORT\s*=\s*(\d+)' -ErrorAction SilentlyContinue
if ($envPorta) { $porta = [int]$envPorta.Matches[0].Groups[1].Value }

$ocupada = Get-NetTCPConnection -LocalPort $porta -State Listen -ErrorAction SilentlyContinue
if ($ocupada) {
    $quem = (Get-Process -Id $ocupada[0].OwningProcess -ErrorAction SilentlyContinue).ProcessName
    Fail "a porta $porta já está em uso por '$quem'." "feche esse processo, ou mude BIND_PORT no api\.env"
}

# --- IP da LAN ------------------------------------------------------------------------------
# 🔴 A heurística de rota (abrir um socket UDP e ver que interface o SO escolhe) ERRA com VPN
# ligada: a saída passa a ser o túnel, e o IP devolvido é o do túnel. O QR do /tv ficaria com
# um endereço que nenhum celular da festa alcança, sem nada parecer errado no servidor.
#
# Aqui dá para olhar os adaptadores de verdade. O discriminador que funciona: endereço de LAN
# doméstica vem por DHCP; endereço de túnel de VPN vem como `Manual`.

$cand = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
    ForEach-Object {
        $cfg = Get-NetIPConfiguration -InterfaceIndex $_.InterfaceIndex -ErrorAction SilentlyContinue
        [pscustomobject]@{
            IP      = $_.IPAddress
            Alias   = $_.InterfaceAlias
            Dhcp    = ($_.PrefixOrigin -eq 'Dhcp')
            Gateway = [bool]$cfg.IPv4DefaultGateway
        }
    }

$escolhido = $cand | Where-Object { $_.Dhcp -and $_.Gateway } | Select-Object -First 1
if (-not $escolhido) { $escolhido = $cand | Where-Object { $_.Gateway } | Select-Object -First 1 }
if (-not $escolhido) { $escolhido = $cand | Select-Object -First 1 }

if (-not $escolhido) {
    Fail 'não achei nenhum IPv4 de rede local.' 'conecte o notebook ao Wi-Fi da festa e rode de novo'
}
$ip = $escolhido.IP
$env:LAN_IP = $ip   # o servidor usa isto no joinUrl e no QR, em vez de adivinhar pela rota

if (($cand | Measure-Object).Count -gt 1) {
    Write-Host ''
    Write-Host '  mais de uma rede ativa nesta máquina:' -ForegroundColor Yellow
    foreach ($c in $cand) {
        $marca = if ($c.IP -eq $ip) { '  <- usando este' } else { '' }
        Write-Host ("    {0,-16} {1,-34}{2}" -f $c.IP, $c.Alias, $marca) -ForegroundColor Yellow
    }
    Write-Host '  Se o escolhido não for o do Wi-Fi da festa, desligue a VPN.' -ForegroundColor Yellow
}

# --- SSID do QR de Wi-Fi --------------------------------------------------------------------
# O QR de Wi-Fi do /tv sai do WIFI_SSID do .env. Se ele não for a rede em que este notebook
# está, o QR manda os convidados para outra rede — e eles não alcançam este servidor. Escanear
# funciona, conectar funciona, e só a festa não funciona.
#
# O cenário provável não é erro de digitação: é o notebook ter caído na banda 2G enquanto o
# .env diz 5G. Aviso, não erro fatal — pode ser de propósito (roteador dual-band com bandas de
# nome diferente, mas mesma LAN).

$ssidEnv = Select-String -Path (Join-Path $api '.env') -Pattern '^\s*WIFI_SSID\s*=\s*(.+?)\s*$' -ErrorAction SilentlyContinue
if ($ssidEnv) {
    $ssidCfg = $ssidEnv.Matches[0].Groups[1].Value
    # 🔴 `netsh wlan show interfaces`, e NÃO Get-NetConnectionProfile: este último devolve o
    # nome do PERFIL de rede, que o Windows sufixa (`Rede_5G 2`) quando já viu duas redes
    # distintas com o mesmo nome. Comparar com o valor sufixado daria falso positivo sempre.
    $iface = netsh wlan show interfaces 2>$null | Out-String
    $m = [regex]::Match($iface, '(?im)^\s*SSID\s*:\s*(.+?)\s*$')
    if ($m.Success -and $m.Groups[1].Value -cne $ssidCfg) {
        Write-Host ''
        Write-Host "  ⚠  o QR de Wi-Fi apontará para '$ssidCfg', mas este notebook está em '$($m.Groups[1].Value)'." -ForegroundColor Yellow
        Write-Host '     Se as duas não forem a mesma LAN, o convidado conecta e não alcança o servidor.' -ForegroundColor Yellow
        Write-Host '     Ajuste WIFI_SSID no api\.env.' -ForegroundColor Yellow
    }
}

if ($porta -eq 80) { $url = "http://$ip" } else { $url = "http://${ip}:$porta" }

$linha = '=' * 60
Write-Host ''
Write-Host "  $linha" -ForegroundColor DarkGray
Write-Host ''
Write-Host '     convidados  ->  ' -NoNewline -ForegroundColor Gray
Write-Host $url -ForegroundColor Green
Write-Host '     monitor     ->  ' -NoNewline -ForegroundColor Gray
Write-Host "$url/tv" -ForegroundColor Cyan
Write-Host '     você        ->  ' -NoNewline -ForegroundColor Gray
Write-Host "$url/host" -ForegroundColor Magenta
Write-Host ''
Write-Host "  $linha" -ForegroundColor DarkGray
Write-Host '  Ctrl+C encerra. Log completo em api\party.log' -ForegroundColor DarkGray
Write-Host ''

# --- o monitor, para o karaokê ----------------------------------------------------------------
#
# O karaokê toca um vídeo do YouTube num iframe da /tv, e sem estas duas coisas ele não faz som:
#
#   1. --autoplay-policy=no-user-gesture-required   o Chrome barra áudio sem gesto do usuário
#   2. --user-data-dir=<perfil dedicado>            🔴 a parte que ninguém acredita ser necessária
#
# 🔴 Sobre (2): se o Chrome JÁ ESTIVER RODANDO no perfil padrão, `Start-Process chrome` entrega a
# URL ao processo existente e **descarta todos os flags** — inclusive o (1). O sintoma é "o flag
# não funciona", sem erro nenhum, sem nada no log. Um perfil próprio força um processo novo. E é
# nele que a conta com YouTube Premium vive, que é a única coisa que de fato elimina o anúncio de
# pré-roll no pior instante possível.

$perfilTv = Join-Path $root '.chrome-tv'
$flagsTv = @(
    "--user-data-dir=`"$perfilTv`""
    '--autoplay-policy=no-user-gesture-required'
    '--no-first-run'
    '--no-default-browser-check'
    '--kiosk'
    "$url/tv"
)

$chrome = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe"
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe"
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

# Impressa SEMPRE, com ou sem -Tv: sem o switch, o caminho manual precisa ser um paste, e não uma
# linha que alguém remonta de cabeça às 21h com gente chegando.
Write-Host '  para o karaokê, a /tv precisa subir assim:' -ForegroundColor DarkGray
Write-Host "    chrome $($flagsTv -join ' ')" -ForegroundColor DarkGray
Write-Host ''

if ($Tv) {
    if (-not $chrome) {
        Write-Host '  ⚠  não achei o chrome.exe — abra a /tv à mão com a linha acima.' -ForegroundColor Yellow
        Write-Host ''
    }
    else {
        # Primeira vez: SEM quiosque, porque é preciso poder fazer login na conta com YouTube
        # Premium — e em quiosque não há barra de endereço nem menu para chegar lá.
        $primeira = -not (Test-Path $perfilTv)
        if ($primeira) {
            $flagsTv = $flagsTv | Where-Object { $_ -ne '--kiosk' }
            Write-Host '  primeira vez neste perfil do Chrome:' -ForegroundColor Cyan
            Write-Host '    entre na conta com YouTube Premium, feche o Chrome, e rode de novo.' -ForegroundColor Cyan
            Write-Host '    (sem Premium o anúncio toca na frente de quem ia cantar)' -ForegroundColor DarkGray
            Write-Host ''
        }
        # Em job porque o uvicorn abaixo BLOQUEIA: o Chrome tem de esperar o servidor atender,
        # senão abre numa página de erro e fica nela. O job morre sozinho quando o Chrome sobe.
        Start-Job -ScriptBlock {
            param($alvo, $exe, $flags)
            foreach ($i in 1..60) {
                try {
                    Invoke-WebRequest -Uri "$alvo/health" -UseBasicParsing -TimeoutSec 1 | Out-Null
                    break
                }
                catch { Start-Sleep -Milliseconds 500 }
            }
            Start-Process -FilePath $exe -ArgumentList $flags
        } -ArgumentList $url, $chrome, $flagsTv | Out-Null
    }
}

# --- sobe -----------------------------------------------------------------------------------
# Um worker, sempre: o estado deste app é singleton e --workers 2 faria dois maestros
# despacharem faixas um por cima do outro (03 §5).

Push-Location $api
try { & $py -m bq }
finally { Pop-Location }
