# panel-dashboard.ps1 - dibuja un PNG de 1920x462 con metricas del PC, para el panel CFV235.
#
# Sin dependencias: usa System.Drawing, que viene con Windows. El PNG resultante se sube al
# panel con el cliente HID (cougar_hid_node.js subir), que aplica el protocolo y la
# sincronizacion correctos.
#
# Uso:  .\panel-dashboard.ps1 [-Salida C:\ruta\panel.png] [-Temperaturas]
#   -Temperaturas  intenta leer la temperatura de CPU del sysinfo.json del editor (si existe)

[CmdletBinding()]
param(
    [string]$Salida = "$env:TEMP\panel_cfv235.png",
    [switch]$Temperaturas
)

$ErrorActionPreference = 'Continue'
Add-Type -AssemblyName System.Drawing

# ---------------------------------------------------------------- metricas
# Se usan clases CIM (WMI) y NO Get-Counter: los nombres de los contadores de rendimiento
# estan traducidos al idioma del sistema, asi que '\Processor(_Total)\% Processor Time' no
# existe en un Windows en espanol. Los nombres de clase CIM si son independientes del idioma.
function Get-Metricas {
    $m = [ordered]@{ cpu = $null; ram = $null; disco = $null; red = $null; cpuTemp = $null; gpu = $null }

    try {
        $p = Get-CimInstance Win32_PerfFormattedData_PerfOS_Processor -ErrorAction Stop |
             Where-Object { $_.Name -eq '_Total' }
        if ($p) { $m.cpu = [Math]::Round([double]$p.PercentProcessorTime, 0) }
    } catch { }

    try {
        $os = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
        $usada = $os.TotalVisibleMemorySize - $os.FreePhysicalMemory
        $m.ram = [Math]::Round(100 * $usada / $os.TotalVisibleMemorySize, 0)
        $m.ramGB = [Math]::Round($usada / 1MB, 1)
        $m.ramTotalGB = [Math]::Round($os.TotalVisibleMemorySize / 1MB, 0)
    } catch { }

    try {
        $d = Get-CimInstance Win32_PerfFormattedData_PerfDisk_PhysicalDisk -ErrorAction Stop |
             Where-Object { $_.Name -eq '_Total' }
        if ($d) { $m.disco = [Math]::Min(100, [Math]::Round([double]$d.PercentDiskTime, 0)) }
    } catch { }

    try {
        $n = Get-CimInstance Win32_PerfFormattedData_Tcpip_NetworkInterface -ErrorAction Stop
        $suma = ($n | Measure-Object -Property BytesTotalPersec -Sum).Sum
        $m.red = [Math]::Round($suma / 1MB, 2)
    } catch { }

    try {
        $g = Get-CimInstance Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine -ErrorAction Stop |
             Where-Object { $_.Name -like '*engtype_3D*' }
        if ($g) {
            $suma = ($g | Measure-Object -Property UtilizationPercentage -Sum).Sum
            $m.gpu = [Math]::Min(100, [Math]::Round($suma, 0))
        }
    } catch { }

    if ($Temperaturas) {
        # El editor guarda un volcado de sensores (requiere haberlo abierto alguna vez).
        $ruta = "$env:APPDATA\cougar_lcd_editor\sysinfo.json"
        if (Test-Path $ruta) {
            try {
                $texto = Get-Content $ruta -Raw
                if ($texto -match '"CPU Package"\s*:\s*([\d\.]+)') {
                    $m.cpuTemp = [Math]::Round([double]$Matches[1], 0)
                }
            } catch { }
        }
    }
    return $m
}

# ---------------------------------------------------------------- dibujo
function New-PanelPng {
    param([string]$Ruta, $M, [double]$HoraPct = -1)

    $ancho = 1920; $alto = 462
    $bmp = New-Object System.Drawing.Bitmap($ancho, $alto)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = 'AntiAlias'
    $g.TextRenderingHint = 'ClearTypeGridFit'

    $fondo = [System.Drawing.Color]::FromArgb(255, 14, 17, 23)
    $g.Clear($fondo)

    $acento = [System.Drawing.Color]::FromArgb(255, 0, 208, 255)
    $acento2 = [System.Drawing.Color]::FromArgb(255, 255, 0, 200)
    $gris = [System.Drawing.Color]::FromArgb(255, 120, 130, 145)
    $blanco = [System.Drawing.Color]::FromArgb(255, 235, 240, 248)

    $fuenteEtiqueta = New-Object System.Drawing.Font('Segoe UI', 26, [System.Drawing.FontStyle]::Regular)
    $fuenteValor = New-Object System.Drawing.Font('Segoe UI', 74, [System.Drawing.FontStyle]::Bold)
    $fuenteUnidad = New-Object System.Drawing.Font('Segoe UI', 28, [System.Drawing.FontStyle]::Regular)
    $fuenteReloj = New-Object System.Drawing.Font('Segoe UI', 132, [System.Drawing.FontStyle]::Bold)
    $fuenteFecha = New-Object System.Drawing.Font('Segoe UI', 34, [System.Drawing.FontStyle]::Regular)

    $pincelEtiqueta = New-Object System.Drawing.SolidBrush($gris)
    $pincelValor = New-Object System.Drawing.SolidBrush($blanco)
    $pincelAcento = New-Object System.Drawing.SolidBrush($acento)
    $pincelAcento2 = New-Object System.Drawing.SolidBrush($acento2)
    $pincelUnidad = New-Object System.Drawing.SolidBrush($gris)

    # ---- columna izquierda: reloj y fecha
    $ahora = Get-Date
    $g.DrawString($ahora.ToString('HH:mm'), $fuenteReloj, $pincelValor, 60, 90)
    $g.DrawString($ahora.ToString('dddd d MMMM yyyy'), $fuenteFecha, $pincelEtiqueta, 66, 270)

    $cuadro = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 24, 30, 40))
    $g.FillRectangle($cuadro, 60, 350, 560, 62)
    $g.DrawString("COUGAR CFV235  -  panel controlado desde el PC (sin el editor)",
                  (New-Object System.Drawing.Font('Segoe UI', 20)), $pincelAcento, 76, 364)

    # ---- tarjetas de metricas
    function New-Tarjeta {
        param($x, $etiqueta, $valor, $unidad, $pct, $color)

        $g.FillRectangle((New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 22, 27, 36))),
                         $x, 60, 340, 342)
        $g.DrawRectangle((New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(255, 44, 52, 66), 2)),
                         $x, 60, 340, 342)

        $g.DrawString($etiqueta, $fuenteEtiqueta, $pincelEtiqueta, $x + 28, 84)
        $g.DrawString($valor, $fuenteValor, $pincelValor, $x + 24, 140)
        $tam = $g.MeasureString($valor, $fuenteValor)
        $g.DrawString($unidad, $fuenteUnidad, $pincelUnidad, $x + 28 + $tam.Width, 190)

        if ($pct -ge 0) {
            $bx = $x + 28; $by = 300; $bw = 284; $bh = 26
            $g.FillRectangle((New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 38, 45, 58))),
                             $bx, $by, $bw, $bh)
            $relleno = [Math]::Max(0, [Math]::Min(1, $pct / 100.0)) * $bw
            $g.FillRectangle((New-Object System.Drawing.SolidBrush($color)), $bx, $by, $relleno, $bh)
            $g.DrawString(("{0:N0} %" -f $pct), (New-Object System.Drawing.Font('Segoe UI', 20)),
                          $pincelUnidad, $bx, $by + 34)
        } else {
            $g.DrawString('sin dato', (New-Object System.Drawing.Font('Segoe UI', 20)),
                          $pincelUnidad, $x + 28, 302)
        }
    }

    $c1 = 680; $c2 = 1050; $c3 = 1420

    if ($M.cpu -ne $null) { New-Tarjeta $c1 'CPU' ([string]$M.cpu) '%' $M.cpu $acento }
    else { New-Tarjeta $c1 'CPU' '--' '%' -1 $acento }

    if ($M.ram -ne $null) { New-Tarjeta $c2 'MEMORIA' ([string]$M.ram) '%' $M.ram $acento2 }
    else { New-Tarjeta $c2 'MEMORIA' '--' '%' -1 $acento2 }

    if ($M.gpu -ne $null) { New-Tarjeta $c3 'GPU' ([string]$M.gpu) '%' $M.gpu $acento }
    else { New-Tarjeta $c3 'GPU' '--' '%' -1 $acento }

    # ---- linea inferior
    $pie = ""
    if ($M.ramGB) { $pie += "RAM $($M.ramGB) / $($M.ramTotalGB) GB    " }
    if ($M.disco -ne $null) { $pie += "Disco $($M.disco)%    " }
    if ($M.red -ne $null) { $pie += "Red $($M.red) MB/s    " }
    if ($M.cpuTemp) { $pie += "CPU $($M.cpuTemp) C    " }
    $g.DrawString($pie.Trim(), (New-Object System.Drawing.Font('Segoe UI', 22)), $pincelEtiqueta, 680, 418)

    $g.Dispose()
    $bmp.Save($Ruta, [System.Drawing.Imaging.ImageFormat]::Png)
    $bmp.Dispose()
}

# ---------------------------------------------------------------- principal
$m = Get-Metricas
New-PanelPng -Ruta $Salida -M $m
$info = Get-Item $Salida
"PNG: $($info.FullName)  ($($info.Length) B)"
"Cpu=$($m.cpu)%  Ram=$($m.ram)%  Gpu=$($m.gpu)%  Disco=$($m.disco)%  Red=$($m.red) MB/s  Temp=$($m.cpuTemp)"
