#!/usr/bin/env bash
# scripts/sync-wiki.sh — mirror docs/ + docs/uk/ into GitHub Wiki.
#
# Runs both locally (manual pushes) and inside .github/workflows/sync-wiki.yml.
# The wiki repo (${REPO}.wiki.git) must be initialised ONCE via the GitHub
# web UI (open /wiki, click "Create the first page", Save). Afterwards
# this script owns it end-to-end.
#
# Auth — needs a token with write access to ${REPO}.wiki.git:
#   * In CI: exported from secrets.WIKI_SYNC_TOKEN. MUST be a PAT with
#     Contents:Write on this repo — the built-in GITHUB_TOKEN cannot
#     push to the wiki repo.
#   * Locally: export GITHUB_TOKEN or WIKI_SYNC_TOKEN before invoking,
#     e.g. ``WIKI_SYNC_TOKEN=$(cat ~/.config/selena/wiki.token) \
#     bash scripts/sync-wiki.sh``. The script NEVER reads tokens from
#     tracked files.
#
# Pages are rewritten per naming convention:
#   docs/foo-bar.md        → en-Foo-Bar.md
#   docs/uk/foo-bar.md     → uk-Foo-Bar.md
# with cross-references patched accordingly.
set -euo pipefail

# Script lives at scripts/, so the repo root is one level up.
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO="${SELENA_WIKI_REPO:-dotradepro/SelenaCore}"

TOKEN="${GITHUB_TOKEN:-${WIKI_SYNC_TOKEN:-}}"
if [ -z "$TOKEN" ]; then
    echo "[!] No token in env — set GITHUB_TOKEN or WIKI_SYNC_TOKEN" >&2
    exit 1
fi

WIKI_URL="https://${TOKEN}@github.com/${REPO}.wiki.git"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "[+] Preparing wiki clone at $TMP/wiki"
if ! git clone --quiet "$WIKI_URL" "$TMP/wiki" 2>/dev/null; then
    cat >&2 <<BOOTSTRAP_MSG
[!] The GitHub wiki repo for ${REPO} does not exist yet.
    GitHub only creates it after the FIRST page is saved via the web UI.
    One-time bootstrap step:
      1. Open https://github.com/${REPO}/wiki
      2. Click 'Create the first page'
      3. Leave title=Home, put any text, click 'Save page'
      4. Re-run this script — it will take over from there and never bug
         you again.
BOOTSTRAP_MSG
    exit 1
fi

cd "$TMP/wiki"

# Drop every managed page before regenerating. Leaves any unmanaged (hand-
# edited) pages alone — drift warning handled in _Footer.
find . -maxdepth 1 \( -name 'en-*.md' -o -name 'uk-*.md' \) -delete
rm -f Home.md _Sidebar.md _Footer.md

# ────────────────────────────────────────────────────────────────────────
# Title-Case converter: "deploy-native" → "Deploy-Native"
title_case() {
    echo "$1" | awk -F'-' '{
        for (i=1; i<=NF; i++) $i = toupper(substr($i,1,1)) substr($i,2);
        print
    }' OFS='-'
}

# Link rewriter for EN pages: patch docs-relative links to wiki page names.
rewrite_en() {
    # docs/foo.md inside an EN page currently looks like "(foo.md)" or
    # "(./foo.md)" or "(../docs/foo.md)". Turn them all into (en-Foo).
    # docs/uk/foo.md → (uk-Foo).
    # ../core/** or ../scripts/** → absolute blob URL.
    sed -E "
        s|\]\(\.\./core/([^\)]+)\)|](https://github.com/${REPO}/tree/main/core/\1)|g
        s|\]\(\.\./scripts/([^\)]+)\)|](https://github.com/${REPO}/tree/main/scripts/\1)|g
        s|\]\(\.\./system_modules/([^\)]+)\)|](https://github.com/${REPO}/tree/main/system_modules/\1)|g
        s|\]\(\.\./agent/([^\)]+)\)|](https://github.com/${REPO}/tree/main/agent/\1)|g
        s|\]\(\.\./\.\./core/([^\)]+)\)|](https://github.com/${REPO}/tree/main/core/\1)|g
    " | python3 -c '
import re, sys
text = sys.stdin.read()
def case_slug(s):
    s = s.replace("_","-")
    return "-".join(p[:1].upper()+p[1:] for p in s.split("-"))
def repl_uk(m):
    anchor = m.group(2) or ""
    return "](uk-" + case_slug(m.group(1)) + anchor + ")"
def repl_en(m):
    anchor = m.group(2) or ""
    return "](en-" + case_slug(m.group(1)) + anchor + ")"
text = re.sub(r"\]\((?:\./|\.\./)?uk/([a-zA-Z0-9_-]+)\.md(#[^\)]+)?\)", repl_uk, text)
text = re.sub(r"\]\((?:\./|\.\./)?([a-zA-Z0-9_-]+)\.md(#[^\)]+)?\)", repl_en, text)
sys.stdout.write(text)
'
}

# Link rewriter for UK pages.
rewrite_uk() {
    sed -E "
        s|\]\(\.\./\.\./core/([^\)]+)\)|](https://github.com/${REPO}/tree/main/core/\1)|g
        s|\]\(\.\./\.\./scripts/([^\)]+)\)|](https://github.com/${REPO}/tree/main/scripts/\1)|g
        s|\]\(\.\./\.\./system_modules/([^\)]+)\)|](https://github.com/${REPO}/tree/main/system_modules/\1)|g
        s|\]\(\.\./\.\./agent/([^\)]+)\)|](https://github.com/${REPO}/tree/main/agent/\1)|g
    " | python3 -c '
import re, sys
text = sys.stdin.read()
def case_slug(s):
    s = s.replace("_","-")
    return "-".join(p[:1].upper()+p[1:] for p in s.split("-"))
def repl_same(m):
    anchor = m.group(2) or ""
    return "](uk-" + case_slug(m.group(1)) + anchor + ")"
def repl_up(m):
    anchor = m.group(2) or ""
    return "](en-" + case_slug(m.group(1)) + anchor + ")"
text = re.sub(r"\]\(\.\./([a-zA-Z0-9_-]+)\.md(#[^\)]+)?\)", repl_up, text)
text = re.sub(r"\]\((?:\./)?([a-zA-Z0-9_-]+)\.md(#[^\)]+)?\)", repl_same, text)
sys.stdout.write(text)
'
}

# ────────────────────────────────────────────────────────────────────────
echo "[+] Rendering EN pages"
count_en=0
for f in "$REPO_ROOT"/docs/*.md; do
    [ -f "$f" ] || continue
    base=$(basename "$f" .md)
    title=$(title_case "$base")
    dest="en-${title}.md"
    rewrite_en < "$f" > "$dest"
    count_en=$((count_en + 1))
done
echo "    $count_en EN pages"

echo "[+] Rendering UK pages"
count_uk=0
for f in "$REPO_ROOT"/docs/uk/*.md; do
    [ -f "$f" ] || continue
    base=$(basename "$f" .md)
    # _ (underscore) → - so URL is consistent
    base="${base//_/-}"
    title=$(title_case "$base")
    dest="uk-${title}.md"
    rewrite_uk < "$f" > "$dest"
    count_uk=$((count_uk + 1))
done
echo "    $count_uk UK pages"

# ────────────────────────────────────────────────────────────────────────
# Sidebar + Home + Footer. Source of truth is _private/wiki-index.md.
if [ -f "$REPO_ROOT/_private/wiki-index.md" ]; then
    cp "$REPO_ROOT/_private/wiki-index.md" _Sidebar.md
else
    cat > _Sidebar.md <<'EOF'
**SelenaCore**

[🏠 Home](Home)

**🇬🇧 English**
- [Deploy Native](en-Deploy-Native)
- [Configuration](en-Configuration)
- [Architecture](en-Architecture)
- [Voice Settings](en-Voice-Settings)
- [Translation](en-Translation)
- [Helsinki Translator](en-Helsinki-Translator)
- [Intent Routing](en-Intent-Routing)
- [Kiosk Setup](en-Kiosk-Setup)
- [Module Development](en-Module-Development)
- [API Reference](en-Api-Reference)

**🇺🇦 Українська**
- [Нативне розгортання](uk-Deploy-Native)
- [Конфігурація](uk-Configuration)
- [Архітектура](uk-Architecture)
- [Voice Settings](uk-Voice-Settings)
- [Переклад](uk-Translation)
- [Helsinki](uk-Helsinki-Translator)
- [Intent routing](uk-Intent-Routing)
- [Kiosk](uk-Kiosk-Setup)
- [Розробка модулів](uk-Module-Development)
- [API Reference](uk-Api-Reference)

---
[Main repo](https://github.com/dotradepro/SelenaCore) · [Helsinki models](https://github.com/dotradepro/selena-helsinki-models)
EOF
fi

cat > Home.md <<EOF
# SelenaCore · Wiki

Offline-first smart-home hub. Pick your language:

- 🇬🇧 [**English documentation**](en-Deploy-Native) — start here to install and configure
- 🇺🇦 [**Українська документація**](uk-Deploy-Native) — почніть звідси для встановлення та налаштування

<p align="center">
  <img src="https://raw.githubusercontent.com/${REPO}/main/docs/img/dashboard-dark.png" alt="SelenaCore dashboard — dark theme" width="46%"/>
  &nbsp;
  <img src="https://raw.githubusercontent.com/${REPO}/main/docs/img/dashboard-light.png" alt="SelenaCore dashboard — light theme" width="46%"/>
</p>
<p align="center"><sub>Dashboard on a 7" kiosk — dark evening / light day themes, media, weather, climate, voice control.</sub></p>

---

## Quick links · Швидкі посилання

| 🇬🇧 English | 🇺🇦 Українська |
|---|---|
| [Deploy native (any Linux)](en-Deploy-Native) | [Нативне розгортання](uk-Deploy-Native) |
| [Configuration](en-Configuration) | [Конфігурація](uk-Configuration) |
| [Architecture](en-Architecture) | [Архітектура](uk-Architecture) |
| [Voice & Intent](en-Voice-Settings) | [Voice & Intent](uk-Voice-Settings) |
| [Translation](en-Translation) | [Переклад](uk-Translation) |
| [Module development](en-Module-Development) | [Розробка модулів](uk-Module-Development) |
| [API reference](en-Api-Reference) | [API reference](uk-Api-Reference) |

---

Related repos:
- [**dotradepro/SelenaCore**](https://github.com/dotradepro/SelenaCore) — main repo, source of truth
- [**dotradepro/selena-helsinki-models**](https://github.com/dotradepro/selena-helsinki-models) — pre-converted translation models
EOF

cat > _Footer.md <<'EOF'
🤖 **This wiki is auto-synced** from [`docs/` in the main repo](https://github.com/dotradepro/SelenaCore/tree/main/docs). Hand-edits on the wiki UI get overwritten on the next push. Open a PR against the main repo instead.

[MIT License](https://github.com/dotradepro/SelenaCore/blob/main/LICENSE) · [Sponsor](https://github.com/sponsors/dotradepro) · [Ko-fi](https://ko-fi.com/dotradepro)
EOF

# ────────────────────────────────────────────────────────────────────────
git add -A
if git diff --cached --quiet; then
    echo "[=] Wiki already up to date."
    exit 0
fi

git -c user.email="wiki-sync@selena" -c user.name="wiki-sync" \
    commit --quiet -m "Sync from docs/ (${GITHUB_SHA:-manual})"
git push --quiet
echo "[✓] Wiki updated: https://github.com/${REPO}/wiki"
