# capturar-pantalla.ps1 - captura la pantalla (o una region) y la deja como PNG.
#
# Lo usa stream.py para reflejar el escritorio en el panel. System.Drawing viene con Windows.
#
# Uso:
#   powershell -NoProfile -ExecutionPolicy Bypass -File capturar-pantalla.ps1 -Salida C:\ruta.png
#     [-Ancho 1920] [-Alto 462] [-Recortar]     # Recortar = banda central en vez de ajustar

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Salida,
    [int]$Ancho = 1920,
    [int]$Alto = 462,
    [switch]$Recortar
)

Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms

$pantalla = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$origen = New-Object System.Drawing.Bitmap($pantalla.Width, $pantalla.Height)
$g1 = [System.Drawing.Graphics]::FromImage($origen)
$g1.CopyFromScreen($pantalla.X, $pantalla.Y, 0, 0, $origen.Size)
$g1.Dispose()

$destino = New-Object System.Drawing.Bitmap($Ancho, $Alto)
$g2 = [System.Drawing.Graphics]::FromImage($destino)
$g2.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic

$proporcionDestino = $Ancho / $Alto
$proporcionOrigen = $origen.Width / $origen.Height

if ($Recortar) {
    # banda central con la proporcion del panel
    $altoRecorte = [Math]::Round($origen.Width / $proporcionDestino)
    if ($altoRecorte -gt $origen.Height) { $altoRecorte = $origen.Height }
    $y = [Math]::Round(($origen.Height - $altoRecorte) / 2)
    $recorte = New-Object System.Drawing.Rectangle(0, $y, $origen.Width, $altoRecorte)
    $g2.DrawImage($origen, (New-Object System.Drawing.Rectangle(0, 0, $Ancho, $Alto)), $recorte,
                  [System.Drawing.GraphicsUnit]::Pixel)
} else {
    # pantalla completa con bandas negras si hace falta
    $anchoEscalado = $Ancho
    $altoEscalado = [Math]::Round($Ancho / $proporcionOrigen)
    if ($altoEscalado -gt $Alto) {
        $altoEscalado = $Alto
        $anchoEscalado = [Math]::Round($Alto * $proporcionOrigen)
    }
    $x = [Math]::Round(($Ancho - $anchoEscalado) / 2)
    $y = [Math]::Round(($Alto - $altoEscalado) / 2)
    $g2.Clear([System.Drawing.Color]::Black)
    $g2.DrawImage($origen, $x, $y, $anchoEscalado, $altoEscalado)
}
$g2.Dispose()
$origen.Dispose()
$destino.Save($Salida, [System.Drawing.Imaging.ImageFormat]::Png)
$destino.Dispose()
Write-Output "$Salida $((Get-Item $Salida).Length)"
