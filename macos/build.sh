#!/bin/zsh
# Builds Morph.app (native shell) and installs it to /Applications.
set -e
cd "$(dirname "$0")"
OUT=build/Morph.app
rm -rf build && mkdir -p $OUT/Contents/{MacOS,Resources}
swiftc -O -swift-version 5 MorphApp.swift -o $OUT/Contents/MacOS/Morph -framework Cocoa -framework WebKit
cp Morph.icns $OUT/Contents/Resources/
cat > $OUT/Contents/Info.plist <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleName</key><string>Morph</string>
<key>CFBundleDisplayName</key><string>Morph</string>
<key>CFBundleIdentifier</key><string>local.morph.converter</string>
<key>CFBundleVersion</key><string>2</string>
<key>CFBundleShortVersionString</key><string>1.1</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleExecutable</key><string>Morph</string>
<key>CFBundleIconFile</key><string>Morph</string>
<key>LSMinimumSystemVersion</key><string>12.0</string>
<key>NSHighResolutionCapable</key><true/>
<key>NSPrincipalClass</key><string>NSApplication</string>
<key>MorphDirectory</key><string>$(cd .. && pwd)</string>
<key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
<key>CFBundleDocumentTypes</key><array><dict>
  <key>CFBundleTypeName</key><string>Any File</string>
  <key>CFBundleTypeRole</key><string>Viewer</string>
  <key>LSHandlerRank</key><string>Alternate</string>
  <key>LSItemContentTypes</key><array><string>public.data</string><string>public.content</string></array>
</dict></array>
</dict></plist>
PL
codesign --force --deep -s - $OUT
rm -rf /Applications/Morph.app && cp -R $OUT /Applications/Morph.app
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f /Applications/Morph.app
echo "✓ Installed /Applications/Morph.app"
