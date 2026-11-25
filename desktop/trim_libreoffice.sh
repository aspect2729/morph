#!/usr/bin/env bash
# Remove the parts of a LibreOffice copy that headless conversion never uses (~300 MB):
# help, spelling dictionaries/extensions, extra icon themes, gallery, templates, wizards, Java helpers,
# PDF import. (LibreOffice's own Python stays: Calc's import filters need it.)
#   desktop/trim_libreoffice.sh path/to/LibreOffice.app        (macOS)
#   desktop/trim_libreoffice.sh path/to/LibreOffice            (Windows layout: program/, share/)
set -euo pipefail
LO=$1
if [ -d "$LO/Contents/Resources" ]; then            # macOS .app
  R=$LO/Contents/Resources
  rm -rf "$R/help" "$R/gallery" "$R/template" "$R/wizards" "$R/java" "$R/xpdfimport" "$R/CREDITS.fodt"
  rm -rf "$R"/extensions/*
  find "$R/config" -name 'images_*.zip' ! -name 'images_colibre.zip' ! -name 'images_sukapura.zip' -delete
else                                                 # Windows / Linux layout
  S=$LO/share; P=$LO/program
  rm -rf "$LO/help" "$S/gallery" "$S/template" "$S/wizards" "$S/xpdfimport" "$P/classes" "$LO/readmes" \
         "$P/wizards" "$LO/CREDITS.fodt"
  rm -rf "$S"/extensions/*
  find "$S/config" -name 'images_*.zip' ! -name 'images_colibre.zip' -delete
fi
du -sh "$LO"
