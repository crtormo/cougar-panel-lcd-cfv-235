# panel-bucle.ps1 - lanzador silencioso del bucle del dashboard.
#
# Lo llama la tarea programada (o el acceso directo de Inicio). Fija la ruta del modulo
# node-hid del proyecto y arranca el cliente en modo bucle, sin ventana.
#
# Uso manual:  powershell -NoProfile -ExecutionPolicy Bypass -File panel-bucle.ps1 [-Periodo 5]

[CmdletBinding()]
param([int]$Periodo = 5)

$raiz = Split-Path -Parent $PSScriptRoot
$cliente = Join-Path $raiz 'sondas\cougar_hid_node.js'

# node-hid: primero el del proyecto (duradero), luego el de %TEMP%
$hidLocal = Join-Path $raiz 'sondas\nodehid\nodehid.js'
$hid = if (Test-Path $hidLocal) { $hidLocal } else { "$env:LOCALAPPDATA\Temp\nodehid\nodehid.js" }
$env:COUGAR_NODE_HID = ($hid -replace '\\', '/')

# node: primero el portable propio, luego el del harness de DSH
$nodePortable = "$env:LOCALAPPDATA\CFV235\node\node.exe"
$node = if (Test-Path $nodePortable) { $nodePortable }
        else { "$env:APPDATA\dsh-desktop\harness\.desktop-bin\node.cmd" }

if (-not (Test-Path $node) -or -not (Test-Path $cliente) -or -not (Test-Path $hid)) {
    "$(Get-Date -Format s)  faltan ficheros: node=$node cliente=$cliente hid=$hid" |
        Out-File -Append -Encoding utf8 (Join-Path $env:TEMP 'cougar-dashboard-inicio.log')
    exit 1
}

& $node $cliente bucle $Periodo *>> (Join-Path $env:TEMP 'cougar-dashboard-inicio.log')
