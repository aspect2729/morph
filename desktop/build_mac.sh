#!/usr/bin/env bash
# Build the Morph desktop app for macOS -> dist/Morph-mac-<apple-silicon|intel>.dmg
#
# Needs: Homebrew with tesseract ffmpeg dylibbundler, Python 3.12 with requirements.lock + desktop deps.
# Pandoc and LibreOffice: official builds (LibreOffice from /Applications if present, unless LO_DOWNLOAD=1).
#   desktop/build_mac.sh            (on Apple Silicon -> arm64, on Intel -> x86_64)
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD
ARCH=$(uname -m)                                  # arm64 | x86_64
NAME=$([ "$ARCH" = arm64 ] && echo apple-silicon || echo intel)
PY=${PYTHON:-python3}
BUILD=$ROOT/build/desktop
ENG=$BUILD/engines
rm -rf "$BUILD" dist/Morph.app; mkdir -p "$ENG/bin" "$ENG/libs" "$ENG/tessdata" dist

echo "==> Engines: Tesseract, FFmpeg (Homebrew, made relocatable)"
BREW=$(brew --prefix)
for t in tesseract ffmpeg ffprobe; do cp "$(command -v $t)" "$ENG/bin/"; chmod u+w "$ENG/bin/$t"; done
# copy every Homebrew dylib they need into engines/libs and point the binaries at it
dylibbundler -od -b -cd -ns -x "$ENG/bin/tesseract" -x "$ENG/bin/ffmpeg" -x "$ENG/bin/ffprobe" \
  -d "$ENG/libs" -p @executable_path/../libs/ -s "$BREW/lib" >/dev/null
TD=$(cd "$BREW/share/tessdata" && pwd -P)
cp "$TD/eng.traineddata" "$TD/osd.traineddata" "$ENG/tessdata/"
cp -R "$TD/configs" "$ENG/tessdata/" 2>/dev/null || true

echo "==> Pandoc (official build: smaller than Homebrew's and needs only system libraries)"
PDARCH=$([ "$ARCH" = arm64 ] && echo arm64 || echo x86_64)
PDURL=$(curl -fsSL https://api.github.com/repos/jgm/pandoc/releases/latest | grep -oE "https://[^\"]+-${PDARCH}-macOS\.zip" | head -1)
curl -fsSL --retry 3 -o "$BUILD/pandoc.zip" "$PDURL"
unzip -qo "$BUILD/pandoc.zip" -d "$BUILD/pandoc"
cp "$(find "$BUILD/pandoc" -name pandoc -type f | head -1)" "$ENG/bin/pandoc"
strip -x "$ENG/bin/pandoc" 2>/dev/null || true

echo "==> LibreOffice"
if [ -d /Applications/LibreOffice.app ] && [ -z "${LO_DOWNLOAD:-}" ]; then
  LO_APP=/Applications/LibreOffice.app
else
  LOARCH=$([ "$ARCH" = arm64 ] && echo aarch64 || echo x86-64)
  LODIR=$([ "$ARCH" = arm64 ] && echo aarch64 || echo x86_64)
  VER=$(curl -fsSL https://download.documentfoundation.org/libreoffice/stable/ | grep -oE 'href="[0-9]+\.[0-9]+\.[0-9]+/' | grep -oE '[0-9.]+[0-9]' | sort -V | tail -1)
  URL="https://download.documentfoundation.org/libreoffice/stable/$VER/mac/$LODIR/LibreOffice_${VER}_MacOS_${LOARCH}.dmg"
  echo "    downloading $URL"
  curl -fL --retry 3 -o "$BUILD/lo.dmg" "$URL"
  MNT=$(hdiutil attach -nobrowse -readonly "$BUILD/lo.dmg" | grep -o '/Volumes/.*' | head -1)
  LO_APP="$MNT/LibreOffice.app"
fi
ditto "$LO_APP" "$ENG/LibreOffice.app"
[ -n "${MNT:-}" ] && hdiutil detach "$MNT" -quiet || true
./desktop/trim_libreoffice.sh "$ENG/LibreOffice.app"
# trimming breaks LibreOffice's own seal: re-sign it (ad hoc) so macOS will run it
codesign --force --deep --sign - --timestamp=none "$ENG/LibreOffice.app"

echo "==> Python app (PyInstaller)"
"$PY" -m PyInstaller --noconfirm --clean --windowed --name Morph \
  --icon "$ROOT/macos/Morph.icns" --osx-bundle-identifier app.morph.desktop \
  --distpath dist --workpath "$BUILD/pyi" --specpath "$BUILD" \
  --add-data "$ROOT/static:static" --add-data "$ROOT/app:app" \
  --hidden-import app.server --collect-submodules app \
  --collect-all pymupdf --collect-all pymupdf4llm --collect-all pdf2docx --collect-all pillow_heif \
  --collect-submodules uvicorn --collect-all webview \
  --exclude-module tkinter --exclude-module matplotlib --exclude-module IPython \
  --exclude-module pyarrow --exclude-module onnxruntime \
  "$ROOT/desktop/morph_desktop.py"

echo "==> Engines into the app bundle"
ditto "$ENG" dist/Morph.app/Contents/Resources/engines
/usr/libexec/PlistBuddy -c "Add :NSAppTransportSecurity:NSAllowsLocalNetworking bool true" dist/Morph.app/Contents/Info.plist 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :LSMinimumSystemVersion string 12.0" dist/Morph.app/Contents/Info.plist 2>/dev/null || true

echo "==> Ad-hoc signing (unsigned app; required for Apple Silicon to run modified binaries)"
find dist/Morph.app/Contents/Resources/engines/bin dist/Morph.app/Contents/Resources/engines/libs -type f \
  -exec codesign --force --sign - --timestamp=none {} \; 2>/dev/null
codesign --force --sign - --timestamp=none dist/Morph.app      # outer bundle; nested code is already signed

echo "==> Disk image (LZMA-compressed, with a drag-to-Applications shortcut)"
STAGE=$BUILD/dmg; rm -rf "$STAGE"; mkdir -p "$STAGE"
ditto dist/Morph.app "$STAGE/Morph.app"
ln -s /Applications "$STAGE/Applications"
OUT="dist/Morph-mac-$NAME.dmg"
rm -f "$OUT"
hdiutil create -volname "Morph" -srcfolder "$STAGE" -fs HFS+ -format ULMO -ov "$OUT" >/dev/null
du -sh dist/Morph.app "$OUT"
echo "Built $OUT"
