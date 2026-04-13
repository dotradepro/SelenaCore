"""
core/api/routes/shared_assets.py — Shared CSS/JS assets for system module widgets.

Widgets (widget.html, settings.html) run inside iframes and need theme
variables and common boilerplate.  Instead of embedding them in every
HTML file, they load shared assets via <link>/<script> from this route.
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response

from core.utils.theme_utils import generate_override_css

router = APIRouter(prefix="/shared", tags=["shared"])

# ── Theme CSS ────────────────────────────────────────────────────────────

THEME_CSS = """\
/* Shared SelenaCore theme tokens v4 — loaded by all widget/settings iframes.
   Single source of truth: keep in sync with src/index.css :root block.     */

*{margin:0;padding:0;box-sizing:border-box}
html,body{width:100%;height:100%;overflow:hidden;font-family:'DM Sans',system-ui,sans-serif;font-size:13px;line-height:1.4}
body{background:var(--sf);color:var(--tx)}

:root{
  --bg:#0B0C10;--sf:#14151E;--sf2:#1B1C27;--sf3:#242532;
  --b:rgba(255,255,255,.12);--b2:rgba(255,255,255,.20);
  --tx:#EDEEF5;--tx2:#A0A5BE;--tx3:#6B7194;
  --ac:#5A96FF;--gr:#34D693;--am:#F5A93A;--rd:#E05454;
  --shadow:0 1px 3px rgba(0,0,0,.4),0 0 0 1px rgba(255,255,255,.06);
  --shadow-lg:0 4px 16px rgba(0,0,0,.5),0 0 0 1px rgba(255,255,255,.06)
}
:root.light{
  --bg:#EFF0F5;--sf:#FFFFFF;--sf2:#F4F5F9;--sf3:#E8E9F0;
  --b:rgba(0,0,0,.13);--b2:rgba(0,0,0,.22);
  --tx:#1A1C24;--tx2:#4A4F68;--tx3:#7C8198;
  --ac:#3B7AE8;--gr:#1FAF75;--am:#DB8F1C;--rd:#C94040;
  --shadow:0 1px 3px rgba(0,0,0,.08),0 0 0 1px rgba(0,0,0,.06);
  --shadow-lg:0 4px 16px rgba(0,0,0,.1),0 0 0 1px rgba(0,0,0,.06)
}
:root{--ws-bg:var(--sf);--ws-blur:none}
html.has-wallpaper{
  --ws-bg:rgba(20,21,30,.55);--ws-blur:blur(16px);
  --bg:transparent;--sf:transparent;--sf2:rgba(255,255,255,.04);--sf3:rgba(255,255,255,.07);
  --card:transparent;--shadow:none;--shadow-lg:none
}
html.has-wallpaper.light{
  --ws-bg:rgba(255,255,255,.45);--ws-blur:blur(16px);
  --sf2:rgba(0,0,0,.03);--sf3:rgba(0,0,0,.06);--card:transparent
}
html.has-wallpaper body{background:transparent!important;background-image:none!important}
"""


@router.get("/theme.css")
async def theme_css() -> Response:
    css = THEME_CSS
    # Append active custom theme overrides (if any)
    try:
        from core.api.routes.ui import _load_themes
        data = _load_themes()
        active_id = data.get("active", "default")
        if active_id != "default":
            theme = next((t for t in data.get("themes", []) if t["id"] == active_id), None)
            if theme:
                css += generate_override_css(theme)
    except Exception:
        pass  # graceful fallback to base theme
    return Response(content=css, media_type="text/css")


# ── Widget common JS ─────────────────────────────────────────────────────

WIDGET_COMMON_JS = """\
/* Shared widget boilerplate — loaded before per-module scripts.
   Expects the module to define:  var L = { en: {...}, uk: {...} };        */

var LANG = (function () {
    try { return localStorage.getItem('selena-lang') || 'en'; }
    catch (e) { return 'en'; }
})();

function t(k) { return (L[LANG] || L.en)[k] || k; }

function applyLang() {
    document.querySelectorAll('[data-i18n]').forEach(function (el) {
        el.textContent = t(el.getAttribute('data-i18n'));
    });
}

/* Theme sync — apply light/dark class + has-wallpaper from parent app */
(function () {
    try {
        var theme = localStorage.getItem('selena-theme') || 'dark';
        if (theme === 'light') document.documentElement.classList.add('light');
    } catch (e) {}
    try {
        var p = window.parent.document.documentElement;
        if (p.classList.contains('has-wallpaper'))
            document.documentElement.classList.add('has-wallpaper');
    } catch (e) {}
})();

window.addEventListener('message', function (e) {
    if (!e.data) return;
    if (e.data.type === 'theme_changed') {
        var theme = e.data.theme || 'dark';
        document.documentElement.classList.toggle('light', theme === 'light');
    }
    if (e.data.type === 'theme_vars_changed') {
        /* Reload theme.css to pick up new custom theme / wallpaper vars */
        var link = document.querySelector('link[href*="theme.css"]');
        if (link) link.href = '/api/shared/theme.css?t=' + Date.now();
        /* Sync has-wallpaper class from parent */
        try {
            var p = window.parent.document.documentElement;
            document.documentElement.classList.toggle('has-wallpaper', p.classList.contains('has-wallpaper'));
        } catch (_) {}
    }
    if (e.data.type === 'lang_changed') {
        try { LANG = localStorage.getItem('selena-lang') || 'en'; } catch (ex) {}
        applyLang();
        /* Trigger module-specific reload if defined */
        if (typeof refresh === 'function') refresh();
        else if (typeof loadStatus === 'function') loadStatus();
        else if (typeof load === 'function') load();
    }
});
"""


@router.get("/widget-common.js")
async def widget_common_js() -> Response:
    return Response(content=WIDGET_COMMON_JS, media_type="application/javascript")
