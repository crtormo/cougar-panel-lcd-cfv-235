# instalar-tarea.ps1 - arranca el dashboard del CFV235 al iniciar sesion, sin ventanas.
#
# Crea una tarea programada que lanza el cliente en modo bucle. No depende del COUGAR LCD
# Editor: el panel se maneja enteramente desde nuestro codigo.
#
# Uso (consola de administrador):
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\instalar-tarea.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\instalar-tarea.ps1 -Periodo 5
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\instalar-tarea.ps1 -Quitar

[CmdletBinding()]
param(
    [int]$Periodo = 5,
    [string]$NombreTarea = 'COUGAR CFV235 Dashboard',
    [string]$NodeExe,
    [string]$NodeHid,
    [switch]$Admin,
    [switch]$Quitar
)

$ErrorActionPreference = 'Stop'
$raiz = Split-Path -Parent $PSScriptRoot
$cliente = Join-Path $raiz 'sondas\cougar_hid_node.js'

# Rutas duraderas por defecto: un Node portable propio y el node-hid copiado al proyecto.
# Asi la tarea no depende de %TEMP% (que se puede limpiar) ni de otro programa instalado.
if (-not $NodeExe) {
    $portable = "$env:LOCALAPPDATA\CFV235\node\node.exe"
    $NodeExe = if (Test-Path $portable) { $portable }
               else { "$env:APPDATA\dsh-desktop\harness\.desktop-bin\node.cmd" }
}
if (-not $NodeHid) {
    $local = Join-Path $raiz 'sondas\nodehid\nodehid.js'
    $NodeHid = if (Test-Path $local) { $local }
               else { "$env:LOCALAPPDATA\Temp\nodehid\nodehid.js" }
}

if ($Quitar) {
    Unregister-ScheduledTask -TaskName $NombreTarea -Confirm:$false -ErrorAction SilentlyContinue
    schtasks /delete /tn "$NombreTarea" /f 2>$null | Out-Null
    $vbs = Join-Path ([Environment]::GetFolderPath('Startup')) 'COUGAR CFV235 Dashboard.vbs'
    if (Test-Path $vbs) { Remove-Item $vbs -Force; Write-Host "Quitado el lanzador de Inicio: $vbs" }
    Write-Host "Tarea '$NombreTarea' eliminada (si existia)."
    return
}

foreach ($ruta in @($cliente, $NodeExe, $NodeHid)) {
    if (-not (Test-Path $ruta)) { throw "No encuentro: $ruta" }
}

# Se lanza un PowerShell oculto que fija COUGAR_NODE_HID y arranca el bucle. Es mas simple y
# robusto que inyectar variables de entorno en el XML de la tarea.
$hid = $NodeHid -replace '\\', '/'
$comando = "`$env:COUGAR_NODE_HID='$hid'; & '$NodeExe' '$cliente' bucle $Periodo"

$accion = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -Command `"$comando`"" `
    -WorkingDirectory (Join-Path $raiz 'sondas')
$disparo = New-ScheduledTaskTrigger -AtLogOn
$ajustes = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
# El cliente no necesita privilegios (solo escribe en hidraw), asi que la tarea se registra
# como usuario normal: no hace falta consola de administrador salvo que pidas -Admin.
$nivel = if ($Admin) { 'Highest' } else { 'Limited' }
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel $nivel

$creada = $false
try {
    Register-ScheduledTask -TaskName $NombreTarea -Action $accion -Trigger $disparo `
        -Settings $ajustes -Principal $principal -Force -ErrorAction Stop | Out-Null
    $creada = $true
} catch {
    Write-Host "No pude crear la tarea programada ($($_.Exception.Message.Trim()))."
    Write-Host "En muchos equipos eso exige administrador; uso la carpeta de Inicio, que no."
}

if (-not $creada) {
    # --- Alternativa sin administrador: carpeta de Inicio del usuario con lanzador oculto ---
    $inicio = [Environment]::GetFolderPath('Startup')
    $lanzador = Join-Path $raiz 'dashboard\panel-bucle.ps1'
    if (-not (Test-Path $lanzador)) { throw "No encuentro el lanzador: $lanzador" }
    $vbs = Join-Path $inicio 'COUGAR CFV235 Dashboard.vbs'
    $contenido = @"
' Lanzador oculto del dashboard del panel COUGAR CFV235 (generado por instalar-tarea.ps1)
' Arranca al iniciar sesion, sin ventana. Para quitarlo: borra este fichero.
Set sh = CreateObject("WScript.Shell")
sh.Run "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File ""$lanzador""", 0, False
"@
    Set-Content -Path $vbs -Value $contenido -Encoding ASCII
    Write-Host "Alternativa instalada en la carpeta de Inicio:"
    Write-Host "   $vbs"
    Write-Host "   -> apunta a $lanzador"
    Write-Host "Se quita borrando ese fichero (o con -Quitar)."
    return
}

Write-Host "Tarea '$NombreTarea' creada (arranca al iniciar sesion, sin ventana)."
Write-Host "   bucle cada $Periodo s"
Write-Host "   node : $NodeExe"
Write-Host "   hid  : $NodeHid"
Write-Host ""
Write-Host "Arrancar ahora:  Start-ScheduledTask  -TaskName '$NombreTarea'"
Write-Host "Parar:           Stop-ScheduledTask   -TaskName '$NombreTarea'"
Write-Host "Estado:          Get-ScheduledTask -TaskName '$NombreTarea' | Get-ScheduledTaskInfo"
Write-Host "Quitar:          .\instalar-tarea.ps1 -Quitar"
Write-Host ""
Write-Host "Recuerda: NO dejes el COUGAR LCD Editor abierto a la vez (solo un programa puede"
Write-Host "mandar en el panel)."
