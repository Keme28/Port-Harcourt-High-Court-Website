import os
import struct
import subprocess
import sys

def convert_png_to_ico(png_path, ico_path):
    # Use PowerShell System.Drawing or pure struct
    ps_script = f"""
Add-Type -AssemblyName System.Drawing
$pngPath = '{os.path.abspath(png_path).replace("'", "''")}'
$icoPath = '{os.path.abspath(ico_path).replace("'", "''")}'
$img = [System.Drawing.Image]::FromFile($pngPath)
$sizes = @(256, 128, 64, 48, 32, 16)
$pngStreams = @()

foreach ($sz in $sizes) {{
    $bmp = New-Object System.Drawing.Bitmap $sz, $sz
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
    $g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $g.DrawImage($img, 0, 0, $sz, $sz)
    $g.Dispose()
    $ms = New-Object System.IO.MemoryStream
    $bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
    $bmp.Dispose()
    $pngStreams += $ms
}}
$img.Dispose()

$fs = [System.IO.File]::Create($icoPath)
$bw = New-Object System.IO.BinaryWriter $fs
$bw.Write([uint16]0)
$bw.Write([uint16]1)
$bw.Write([uint16]$sizes.Count)

$offset = 6 + ($sizes.Count * 16)
for ($i = 0; $i -lt $sizes.Count; $i++) {{
    $sz = $sizes[$i]
    $w = if ($sz -ge 256) {{ [byte]0 }} else {{ [byte]$sz }}
    $h = if ($sz -ge 256) {{ [byte]0 }} else {{ [byte]$sz }}
    $bytes = $pngStreams[$i].ToArray()
    $bw.Write($w)
    $bw.Write($h)
    $bw.Write([byte]0)
    $bw.Write([byte]0)
    $bw.Write([uint16]1)
    $bw.Write([uint16]32)
    $bw.Write([uint32]$bytes.Length)
    $bw.Write([uint32]$offset)
    $offset += $bytes.Length
}}

for ($i = 0; $i -lt $sizes.Count; $i++) {{
    $bytes = $pngStreams[$i].ToArray()
    $bw.Write($bytes)
    $pngStreams[$i].Dispose()
}}

$bw.Close()
$fs.Close()
Write-Host 'ICON_CREATED'
"""
    cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if "ICON_CREATED" in res.stdout:
        print(f"Successfully generated: {ico_path}")
        return True
    else:
        print("Error generating ico:", res.stderr, res.stdout)
        return False

if __name__ == "__main__":
    base = os.path.dirname(os.path.abspath(__file__))
    png = os.path.join(base, "icon.png")
    ico = os.path.join(base, "icon.ico")
    convert_png_to_ico(png, ico)
