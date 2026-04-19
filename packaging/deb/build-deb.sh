#!/bin/bash
# Build .deb package for evim
set -e

VERSION="1.0.0"
PKG="evim-editor_${VERSION}_all"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

rm -rf "$PKG"
mkdir -p "$PKG/DEBIAN"
mkdir -p "$PKG/usr/lib/evim"
mkdir -p "$PKG/usr/bin"
mkdir -p "$PKG/usr/share/doc/evim-editor"
mkdir -p "$PKG/usr/share/licenses/evim-editor"

cp "$SCRIPT_DIR/DEBIAN/control" "$PKG/DEBIAN/control"
cp "$REPO_ROOT/evim.py" "$PKG/usr/lib/evim/evim.py"
cp "$REPO_ROOT/README.md" "$PKG/usr/share/doc/evim-editor/"
cp "$REPO_ROOT/LICENSE" "$PKG/usr/share/licenses/evim-editor/"

cat > "$PKG/usr/bin/evim" <<'EOF'
#!/bin/sh
exec python3 /usr/lib/evim/evim.py "$@"
EOF
chmod 755 "$PKG/usr/bin/evim"

dpkg-deb --build "$PKG"
echo "Built: ${PKG}.deb"
