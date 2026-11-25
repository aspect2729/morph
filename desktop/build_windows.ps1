# Build the Morph desktop app for Windows -> dist\Morph-windows-setup.exe (installer)
# Runs on GitHub's windows-latest runner (Python 3.12 + pip deps installed by the workflow).
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
Set-Location (Join-Path $PSScriptRoot "..")
$ROOT = (Get-Location).Path
$BUILD = Join-Path $ROOT "build\desktop"
$ENG = Join-Path $BUILD "engines"
Remove-Item -Recurse -Force $BUILD, "dist\Morph" -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force "$ENG\bin", "$ENG\tessdata", "dist" | Out-Null

function Get-File($url, $out) {
  Write-Host "    $url"
  for ($i = 1; $i -le 3; $i++) {
    try { Invoke-WebRequest -Uri $url -OutFile $out -UseBasicParsing -Headers @{"User-Agent"="morph-build"}; return }
    catch { if ($i -eq 3) { throw }; Start-Sleep 5 }
  }
}

Write-Host "==> FFmpeg"
Get-File "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" "$BUILD\ffmpeg.zip"
Expand-Archive "$BUILD\ffmpeg.zip" "$BUILD\ffmpeg"
Copy-Item (Get-ChildItem "$BUILD\ffmpeg" -Recurse -Filter ffmpeg.exe | Select-Object -First 1).FullName "$ENG\bin\"
Copy-Item (Get-ChildItem "$BUILD\ffmpeg" -Recurse -Filter ffprobe.exe | Select-Object -First 1).FullName "$ENG\bin\"

Write-Host "==> Pandoc"
$rel = Invoke-RestMethod "https://api.github.com/repos/jgm/pandoc/releases/latest" -Headers @{"User-Agent"="morph-build"}
$asset = $rel.assets | Where-Object { $_.name -like "*windows-x86_64.zip" } | Select-Object -First 1
Get-File $asset.browser_download_url "$BUILD\pandoc.zip"
Expand-Archive "$BUILD\pandoc.zip" "$BUILD\pandoc"
Copy-Item (Get-ChildItem "$BUILD\pandoc" -Recurse -Filter pandoc.exe | Select-Object -First 1).FullName "$ENG\bin\"

Write-Host "==> Tesseract (UB Mannheim build, installed silently, then copied)"
$TS = "C:\Program Files\Tesseract-OCR"
for ($i = 1; $i -le 2 -and -not (Test-Path "$TS\tesseract.exe"); $i++) { choco install tesseract -y --no-progress | Out-Null }
if (-not (Test-Path "$TS\tesseract.exe")) {
  # Chocolatey's mirror is flaky: fall back to the official UB Mannheim installer on GitHub
  $rel = Invoke-RestMethod "https://api.github.com/repos/UB-Mannheim/tesseract/releases/latest" -Headers @{"User-Agent"="morph-build"}
  $asset = $rel.assets | Where-Object { $_.name -like "*w64-setup*.exe" } | Select-Object -First 1
  Get-File $asset.browser_download_url "$BUILD\tess-setup.exe"
  $TS = "$BUILD\tesseract"
  Start-Process "$BUILD\tess-setup.exe" -ArgumentList "/S", "/D=$TS" -Wait
}
if (-not (Test-Path "$TS\tesseract.exe")) { throw "Tesseract install failed" }
Get-ChildItem $TS -File | Where-Object { $_.Extension -in ".exe", ".dll" } | Copy-Item -Destination "$ENG\bin\"
Copy-Item "$TS\tessdata\eng.traineddata", "$TS\tessdata\osd.traineddata" "$ENG\tessdata\"
if (Test-Path "$TS\tessdata\configs") { Copy-Item -Recurse "$TS\tessdata\configs" "$ENG\tessdata\" }

Write-Host "==> LibreOffice (official MSI, unpacked without installing)"
$idx = (Invoke-WebRequest "https://download.documentfoundation.org/libreoffice/stable/" -UseBasicParsing).Content
$VER = ([regex]::Matches($idx, 'href="(\d+\.\d+\.\d+)/"') | ForEach-Object { $_.Groups[1].Value } |
        Sort-Object { [version]$_ } | Select-Object -Last 1)
Get-File "https://download.documentfoundation.org/libreoffice/stable/$VER/win/x86_64/LibreOffice_${VER}_Win_x86-64.msi" "$BUILD\lo.msi"
$p = Start-Process msiexec.exe -ArgumentList "/a `"$BUILD\lo.msi`" /qn TARGETDIR=`"$BUILD\lo`"" -Wait -PassThru
if ($p.ExitCode -ne 0) { throw "LibreOffice MSI extract failed ($($p.ExitCode))" }
$prog = (Get-ChildItem "$BUILD\lo" -Recurse -Filter soffice.exe | Select-Object -First 1).Directory.Parent.FullName
Copy-Item -Recurse $prog "$ENG\LibreOffice"
Write-Host "    LibreOffice $VER -> $ENG\LibreOffice"
# drop what headless conversion never uses (help, dictionaries, icon themes, gallery, templates...)
& "$env:ProgramFiles\Git\bin\bash.exe" ./desktop/trim_libreoffice.sh (($ENG -replace '\\','/') + "/LibreOffice")
if ($LASTEXITCODE -ne 0) { throw "LibreOffice trim failed" }

Write-Host "==> Python app (PyInstaller)"
python -m PyInstaller --noconfirm --clean --windowed --name Morph `
  --icon "$ROOT\desktop\morph.ico" `
  --distpath dist --workpath "$BUILD\pyi" --specpath "$BUILD" `
  --add-data "$ROOT\static;static" --add-data "$ROOT\app;app" `
  --hidden-import app.server --collect-submodules app `
  --collect-all pymupdf --collect-all pymupdf4llm --collect-all pdf2docx --collect-all pillow_heif `
  --collect-submodules uvicorn --collect-all webview --collect-all clr_loader --collect-all pythonnet `
  --exclude-module tkinter --exclude-module matplotlib --exclude-module IPython `
  --exclude-module pyarrow --exclude-module onnxruntime `
  "$ROOT\desktop\morph_desktop.py"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

Write-Host "==> Engines into the app folder"
Copy-Item -Recurse $ENG "dist\Morph\engines"

Write-Host "==> Installer (Inno Setup, LZMA2 max, solid)"
$ISCC = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $ISCC)) { choco install innosetup -y --no-progress | Out-Null }
$ver = if ($env:GITHUB_REF_NAME -match '^v(\d+\.\d+\.\d+)') { $Matches[1] } else { "0.0.0" }
& $ISCC "/DAppVersion=$ver" "desktop\morph.iss" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
$OUT = "dist\Morph-windows-setup.exe"
"{0:N0} MB  $OUT" -f ((Get-Item $OUT).Length / 1MB)
