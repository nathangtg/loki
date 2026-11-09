#!/usr/bin/env bash
# Builds Loki.app (release) into interface/loki-macos/.build/Loki.app.
#
# Usage (from anywhere): interface/loki-macos/scripts/bundle.sh
# Environment:
#   SWIFT_BUILD_FLAGS  extra flags for `swift build` (for example --disable-sandbox in a
#                      restricted environment). Empty by default.
#   CODESIGN_IDENTITY  signing identity; "-" (ad hoc) by default. No Developer ID is needed.
set -euo pipefail

PKG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PKG_DIR"

APP="$PKG_DIR/.build/Loki.app"
ICONSET="$PKG_DIR/.build/AppIcon.iconset"
IDENTITY="${CODESIGN_IDENTITY:--}"

# shellcheck disable=SC2086
swift build -c release ${SWIFT_BUILD_FLAGS:-}
# shellcheck disable=SC2086
BIN_DIR="$(swift build -c release ${SWIFT_BUILD_FLAGS:-} --show-bin-path)"

rm -rf "$APP" "$ICONSET"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cp "$BIN_DIR/LokiOrb" "$APP/Contents/MacOS/LokiOrb"
cp Resources/Info.plist "$APP/Contents/Info.plist"

# The icon is drawn by the app itself (the same renderer as the live bubble), then packed.
"$APP/Contents/MacOS/LokiOrb" --export-icon "$ICONSET"
iconutil --convert icns "$ICONSET" --output "$APP/Contents/Resources/AppIcon.icns"

# Build number: commit count when available, otherwise the value in Info.plist.
if BUILD_NUMBER="$(git -C "$PKG_DIR" rev-list --count HEAD 2>/dev/null)" && [ -n "$BUILD_NUMBER" ]; then
    /usr/libexec/PlistBuddy -c "Set :CFBundleVersion $BUILD_NUMBER" "$APP/Contents/Info.plist"
fi

plutil -lint "$APP/Contents/Info.plist" >/dev/null
codesign --force --deep --sign "$IDENTITY" "$APP"

echo "$APP"
