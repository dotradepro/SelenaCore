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
   Single source of truth: keep in sync with src/index.css :root block.

   CONTRAST CONTRACT (WCAG 2.1 AA — minimum required for new widgets):
     - body text on surface:       >= 4.5:1   (use --tx or --tx2)
     - muted/hint text:             >= 4.5:1   (use --tx3 — values tuned for AA)
     - large text (>=18px bold):    >= 3:1
     - UI borders, icons, chips:    >= 3:1
   Text ON TOP OF saturated fills (--ac/--gr/--am/--rd) MUST use the paired
   --on-accent / --on-success / --on-warning / --on-danger tokens — white on
   green/amber FAILS AA and is not allowed.                                   */

*{margin:0;padding:0;box-sizing:border-box}
html,body{width:100%;height:100%;overflow:hidden;font-family:'DM Sans',system-ui,sans-serif;font-size:13px;line-height:1.4}
html.in-modal,html.in-modal body{overflow:auto!important;height:auto!important;min-height:100%}
/* Parent dashboard modal provides its own close button — hide any
   per-widget ones so users see a single, consistently-styled control. */
html.in-modal .fs-close,html.in-modal #fsClose{display:none!important}
body{background:var(--sf);color:var(--tx)}

:root{
  --bg:#0B0C10;--sf:#14151E;--sf2:#1B1C27;--sf3:#242532;
  --b:rgba(255,255,255,.12);--b2:rgba(255,255,255,.20);
  --tx:#EDEEF5;--tx2:#A0A5BE;--tx3:#7B80A3;
  --ac:#5A96FF;--gr:#34D693;--am:#F5A93A;--rd:#E05454;
  --on-accent:#FFFFFF;--on-success:#0A1F15;--on-warning:#2B1F07;--on-danger:#FFFFFF;
  --shadow:0 1px 3px rgba(0,0,0,.4),0 0 0 1px rgba(255,255,255,.06);
  --shadow-lg:0 4px 16px rgba(0,0,0,.5),0 0 0 1px rgba(255,255,255,.06)
}
:root.light{
  --bg:#EFF0F5;--sf:#FFFFFF;--sf2:#F4F5F9;--sf3:#E8E9F0;
  --b:rgba(0,0,0,.13);--b2:rgba(0,0,0,.22);
  --tx:#1A1C24;--tx2:#4A4F68;--tx3:#636880;
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

/* ── Shared Component Library ─────────────────────────────────────────── */
/* Unified styles for all settings.html / widget.html iframes.
   Module-specific styles should go in <style> blocks per file.          */

/* ── Typography ───────────────────────────────────────────────────────── */
h2{font-size:16px;font-weight:600;margin-bottom:4px}
.subtitle{font-size:12px;color:var(--tx2);margin-bottom:16px}
.section-title{font-size:15px;font-weight:600;color:var(--tx);margin-bottom:2px}
.section-sub{font-size:12px;color:var(--tx2);margin-top:2px;margin-bottom:12px}
.label-sm{font-size:12px;font-weight:500;color:var(--tx2);margin-bottom:6px}
.label-xs{font-size:11px;color:var(--tx3);margin-bottom:4px}
.mono{font-family:monospace}
.truncate{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}

/* ── Cards / Sections ─────────────────────────────────────────────────── */
.card{background:var(--sf);border:1px solid var(--b);border-radius:12px;padding:16px;margin-bottom:12px}
.card-inner{background:var(--sf2);border:1px solid var(--b2);border-radius:10px;padding:14px;margin-bottom:12px}

/* ── Buttons ──────────────────────────────────────────────────────────── */
.btn{display:inline-flex;align-items:center;gap:4px;padding:7px 14px;border-radius:8px;border:none;cursor:pointer;font-size:12px;font-weight:600;transition:.15s;white-space:nowrap;font-family:inherit;position:relative}
.btn:disabled{opacity:.4;cursor:not-allowed}
.btn-primary,.btn-blue{background:var(--ac);color:var(--on-accent)}
.btn-primary:hover:not(:disabled),.btn-blue:hover:not(:disabled){opacity:.85}
.btn-green{background:var(--gr);color:var(--on-success)}
.btn-green:hover:not(:disabled){opacity:.85}
.btn-danger,.btn-red{background:rgba(224,84,84,.15);color:var(--rd)}
.btn-danger:hover:not(:disabled),.btn-red:hover:not(:disabled){background:rgba(224,84,84,.25)}
.btn-danger-solid{background:var(--rd);color:var(--on-danger)}
.btn-danger-solid:hover:not(:disabled){opacity:.85}
.btn-amber{background:rgba(245,169,58,.15);color:var(--am)}
.btn-amber:hover:not(:disabled){background:rgba(245,169,58,.25)}
.btn-amber-solid{background:var(--am);color:var(--on-warning)}
.btn-amber-solid:hover:not(:disabled){opacity:.85}
.btn-ghost{background:transparent;color:var(--tx3)}
.btn-ghost:hover:not(:disabled){color:var(--tx)}
.btn-secondary,.btn-sec{background:var(--sf3);color:var(--tx);border:1px solid var(--b2)}
.btn-secondary:hover:not(:disabled),.btn-sec:hover:not(:disabled){background:var(--sf2)}
.btn-outline{background:none;border:1px solid var(--b2);color:var(--tx)}
.btn-outline:hover:not(:disabled){background:var(--sf2)}
.btn-link{background:none;border:none;color:var(--ac);cursor:pointer;font-size:12px;padding:0}
.btn-link:hover{opacity:.8}
.btn-sm{padding:5px 10px;font-size:11px}
.btn-xs{padding:3px 8px;font-size:10px}
/* Loading state — spinner overlay */
.btn-loading{pointer-events:none;opacity:.7}
.btn-loading::after{content:'';position:absolute;width:14px;height:14px;border:2px solid rgba(255,255,255,.3);border-top-color:currentColor;border-radius:50%;animation:spin .6s linear infinite;margin-left:4px}

/* ── Form Elements ────────────────────────────────────────────────────── */
input[type="text"],input[type="number"],input[type="password"],textarea,select{background:var(--sf2);color:var(--tx);border:1px solid var(--b2);border-radius:8px;padding:7px 10px;font-size:12px;outline:none;font-family:inherit}
input:focus,textarea:focus,select:focus{border-color:var(--ac)}
textarea{resize:vertical;line-height:1.5}
input[type="range"]{width:100%;height:6px;cursor:pointer;accent-color:var(--ac)}

/* ── Badges ───────────────────────────────────────────────────────────── */
.badge{display:inline-flex;align-items:center;gap:4px;padding:3px 8px;border-radius:6px;font-size:11px;font-weight:500;white-space:nowrap}
.badge-ok,.badge-on{background:rgba(46,201,138,.12);color:var(--gr)}
.badge-err,.badge-off{background:rgba(224,84,84,.12);color:var(--rd)}
.badge-warn{background:rgba(245,169,58,.12);color:var(--am)}
.badge-info,.badge-ac{background:rgba(79,140,247,.12);color:var(--ac)}
.badge-pr{background:rgba(168,85,247,.12);color:var(--pr,#a855f7)}

/* ── Tables ───────────────────────────────────────────────────────────── */
table{width:100%;border-collapse:collapse;font-size:12px}
th{color:var(--tx3);font-weight:500;padding:6px 8px;text-align:left;border-bottom:1px solid var(--b2);font-size:11px}
td{padding:6px 8px;border-bottom:1px solid var(--b);vertical-align:top}
tr:hover td{background:var(--sf2)}

/* ── Tabs ─────────────────────────────────────────────────────────────── */
.settings-tabs{display:flex;gap:2px;margin-bottom:16px;background:var(--sf);border:1px solid var(--b);border-radius:10px;padding:3px;overflow-x:auto}
.settings-tab{flex:1;padding:8px 12px;border-radius:8px;border:none;cursor:pointer;font-size:12px;font-weight:500;white-space:nowrap;background:transparent;color:var(--tx3);transition:.15s;text-align:center;font-family:inherit}
.settings-tab:hover{color:var(--tx)}
.settings-tab.active{background:var(--ac);color:var(--on-accent)}
.tab-panel{display:none}
.tab-panel.active{display:block}

/* ── Progress Bar ─────────────────────────────────────────────────────── */
.progress-bar{height:4px;background:var(--sf3);border-radius:2px;overflow:hidden;margin-top:4px}
.progress-bar .fill{height:100%;background:var(--ac);border-radius:2px;transition:width .3s}

/* ── Toast ─────────────────────────────────────────────────────────────── */
.toast{position:fixed;top:12px;right:12px;padding:8px 16px;border-radius:8px;font-size:13px;font-weight:500;opacity:0;transform:translateY(-8px);transition:opacity .3s,transform .3s;z-index:9999;pointer-events:none}
.toast.show{opacity:1;transform:translateY(0)}
.toast-success{background:var(--gr);color:var(--on-success)}
.toast-error{background:var(--rd);color:var(--on-danger)}
.toast-info{background:var(--ac);color:var(--on-accent)}

/* ── Modal ─────────────────────────────────────────────────────────────── */
.modal-overlay{position:fixed;inset:0;background:rgba(0,0,0,.65);display:flex;justify-content:center;align-items:center;z-index:1000;backdrop-filter:blur(3px)}
.modal{background:var(--sf);border:1px solid var(--b);border-radius:16px;padding:24px;max-width:400px;width:90%;box-shadow:var(--shadow-lg)}
.modal h3{font-size:15px;font-weight:600;margin-bottom:12px}

/* ── Stat Grid ────────────────────────────────────────────────────────── */
.stat-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-bottom:12px}
.stat-card{background:var(--sf2);border-radius:10px;padding:14px;text-align:center;border:1px solid var(--b)}
.stat-card .num{font-size:20px;font-weight:700;color:var(--ac)}
.stat-card .desc{font-size:11px;color:var(--tx3);margin-top:2px}

/* ── Row / Layout ─────────────────────────────────────────────────────── */
.row{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:8px}
.flex{display:flex;align-items:center}
.flex-col{display:flex;flex-direction:column}
.flex1{flex:1}
.wrap{flex-wrap:wrap}

/* ── Spacing Utilities ────────────────────────────────────────────────── */
.gap4{gap:4px}.gap6{gap:6px}.gap8{gap:8px}.gap10{gap:10px}.gap12{gap:12px}.gap16{gap:16px}
.mb4{margin-bottom:4px}.mb8{margin-bottom:8px}.mb12{margin-bottom:12px}.mb16{margin-bottom:16px}
.mt4{margin-top:4px}.mt8{margin-top:8px}.mt12{margin-top:12px}.mt14{margin-top:14px}.mt16{margin-top:16px}

/* ── Animations ───────────────────────────────────────────────────────── */
@keyframes spin{to{transform:rotate(360deg)}}
.spinner{display:inline-block;width:12px;height:12px;border:2px solid var(--b2);border-top-color:var(--ac);border-radius:50%;animation:spin .6s linear infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.5}}
.pulse{animation:pulse 1.5s infinite}
@keyframes skeleton-pulse{0%{background-position:-200px 0}100%{background-position:200px 0}}
.skeleton{background:linear-gradient(90deg,var(--sf2) 25%,var(--sf3) 50%,var(--sf2) 75%);background-size:400px 100%;animation:skeleton-pulse 1.5s ease-in-out infinite;border-radius:6px;min-height:16px}

/* ── Visibility ───────────────────────────────────────────────────────── */
.hidden{display:none!important}

/* ── Scrollbar ────────────────────────────────────────────────────────── */
::-webkit-scrollbar{width:6px;height:6px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:var(--sf3);border-radius:3px}
::-webkit-scrollbar-thumb:hover{background:var(--tx3)}

/* ── Model Item (shared for model catalogs) ──────────────────────────── */
.model-item{padding:10px 12px;border-radius:8px;border:1px solid var(--b2);background:var(--sf2);display:flex;align-items:center;justify-content:space-between;margin-bottom:6px;transition:.15s}
.model-item.active{border-color:var(--gr);background:rgba(46,201,138,.06)}
.model-item:hover{border-color:var(--ac)}

/* ── Slider ───────────────────────────────────────────────────────────── */
.slider-row{margin-bottom:12px}
.slider-header{display:flex;justify-content:space-between;margin-bottom:4px}
.slider-label{font-size:11px;color:var(--tx2)}
.slider-value{font-size:11px;color:var(--tx);font-family:monospace}
.slider-hints{display:flex;justify-content:space-between;font-size:10px;color:var(--tx3)}

/* ── Toggle (Swift-style sliding switch) ──────────────────────────────── */
/* Shared implementation — widgets should NOT redefine .toggle.
   Off-state thumb uses var(--tx) so it contrasts with the --sf3 track in
   both themes. On-state flips the track to var(--gr) and uses a white thumb,
   which reads cleanly on saturated green. Add .amber modifier for variants
   that should turn amber on activation (e.g. lights).                      */
.toggle{position:relative;display:inline-block;width:52px;height:30px;flex-shrink:0;cursor:pointer}
.toggle input{opacity:0;width:0;height:0}
.toggle .slider{position:absolute;inset:0;background:var(--sf3);border:1px solid var(--b2);border-radius:30px;transition:background .22s ease,border-color .22s ease}
.toggle .slider::before{content:"";position:absolute;top:2px;left:2px;width:24px;height:24px;border-radius:50%;background:var(--tx);box-shadow:0 2px 6px rgba(0,0,0,.25);transition:transform .22s ease,background .22s ease}
.toggle input:checked + .slider{background:var(--gr);border-color:var(--gr)}
.toggle.amber input:checked + .slider,.toggle.light input:checked + .slider{background:var(--am);border-color:var(--am)}
.toggle input:checked + .slider::before{background:#fff;transform:translateX(22px)}
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
    return Response(
        content=css,
        media_type="text/css",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


# ── Widget common JS ─────────────────────────────────────────────────────

WIDGET_COMMON_JS = """\
/* Shared widget boilerplate v2 — loaded before per-module scripts.
   Expects the module to define:  var L = { en: {...}, uk: {...} };
   Provides: i18n, theme sync, DOM helpers, fetch wrappers, toast,
   tab switching, loading states.                                          */

/* ── Modal mode detection ─────────────────────────────────────────────── */
(function () {
    try {
        if (new URLSearchParams(location.search).get('modal') === '1') {
            document.documentElement.classList.add('in-modal');
            if (document.body) document.body.classList.add('in-modal');
            else document.addEventListener('DOMContentLoaded', function () {
                document.body.classList.add('in-modal');
            });
        }
    } catch (e) {}
})();

/* ── i18n ─────────────────────────────────────────────────────────────── */
var LANG = (function () {
    try { return localStorage.getItem('selena-lang') || 'en'; }
    catch (e) { return 'en'; }
})();

function t(k) { return (L[LANG] || L.en)[k] || k; }

function applyLang() {
    document.querySelectorAll('[data-i18n]').forEach(function (el) {
        el.textContent = t(el.getAttribute('data-i18n'));
    });
    document.querySelectorAll('[data-placeholder-i18n]').forEach(function (el) {
        el.placeholder = t(el.getAttribute('data-placeholder-i18n'));
    });
}

/* ── DOM Helpers ──────────────────────────────────────────────────────── */
function $(id) { return document.getElementById(id); }

function show(id) { var el = typeof id === 'string' ? $(id) : id; if (el) el.classList.remove('hidden'); }
function hide(id) { var el = typeof id === 'string' ? $(id) : id; if (el) el.classList.add('hidden'); }

function esc(s) {
    return (s == null ? '' : String(s))
        .replace(/[&<>"']/g, function (c) {
            return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":"&#39;"}[c];
        });
}

/* ── BASE Path (iframe-safe) ─────────────────────────────────────────── */
var BASE = window.location.pathname.replace(/\\/(widget|settings)(\\.html)?$/, '');

/* ── Fetch Wrappers ──────────────────────────────────────────────────── */
function _apiHeaders() {
    var h = { 'Content-Type': 'application/json' };
    try { var dt = localStorage.getItem('selena_device'); if (dt) h['X-Device-Token'] = dt; } catch (e) {}
    try { var et = sessionStorage.getItem('selena_elevated'); if (et) h['X-Elevated-Token'] = et; } catch (e) {}
    return h;
}

function apiGet(path) {
    return fetch(BASE + path, { headers: _apiHeaders() })
        .then(function (r) {
            if (!r.ok) return r.text().then(function (t) {
                var msg; try { msg = JSON.parse(t).detail; } catch (e) { msg = r.statusText; }
                return Promise.reject(new Error(msg || r.statusText));
            });
            return r.json();
        });
}

function apiPost(path, body) {
    return fetch(BASE + path, {
        method: 'POST',
        headers: _apiHeaders(),
        body: body != null ? JSON.stringify(body) : undefined
    }).then(function (r) {
        if (!r.ok) return r.text().then(function (t) {
            var msg; try { msg = JSON.parse(t).detail; } catch (e) { msg = r.statusText; }
            return Promise.reject(new Error(msg || r.statusText));
        });
        return r.json();
    });
}

function apiDelete(path) {
    return fetch(BASE + path, {
        method: 'DELETE',
        headers: _apiHeaders()
    }).then(function (r) {
        if (!r.ok) return r.text().then(function (t) {
            var msg; try { msg = JSON.parse(t).detail; } catch (e) { msg = r.statusText; }
            return Promise.reject(new Error(msg || r.statusText));
        });
        if (r.status === 204) return null;
        return r.json();
    });
}

function apiPatch(path, body) {
    return fetch(BASE + path, {
        method: 'PATCH',
        headers: _apiHeaders(),
        body: body != null ? JSON.stringify(body) : undefined
    }).then(function (r) {
        if (!r.ok) return r.text().then(function (t) {
            var msg; try { msg = JSON.parse(t).detail; } catch (e) { msg = r.statusText; }
            return Promise.reject(new Error(msg || r.statusText));
        });
        return r.json();
    });
}

/* ── Toast ────────────────────────────────────────────────────────────── */
var _toastTimer = null;
function showToast(msg, type) {
    type = type || 'success';
    var el = document.getElementById('_toast');
    if (!el) {
        el = document.createElement('div');
        el.id = '_toast';
        document.body.appendChild(el);
    }
    el.textContent = msg;
    el.className = 'toast toast-' + type + ' show';
    clearTimeout(_toastTimer);
    _toastTimer = setTimeout(function () { el.classList.remove('show'); }, 2500);
    /* Bridge to parent React app */
    try { window.parent.postMessage({ type: 'selena-toast', message: msg, level: type }, '*'); } catch (e) {}
}

/* ── Button Loading Wrapper ──────────────────────────────────────────── */
function withLoading(btn, asyncFn) {
    if (!btn || btn.disabled) return Promise.resolve();
    var orig = btn.textContent;
    btn.classList.add('btn-loading');
    btn.disabled = true;
    return Promise.resolve().then(asyncFn).then(function (r) {
        btn.classList.remove('btn-loading');
        btn.disabled = false;
        return r;
    }).catch(function (e) {
        btn.classList.remove('btn-loading');
        btn.disabled = false;
        showToast(String(e && e.message || e), 'error');
    });
}

/* ── Tab Switching ────────────────────────────────────────────────────── */
function initTabs(tabsSelector, panelPrefix) {
    tabsSelector = tabsSelector || '.settings-tab';
    panelPrefix = panelPrefix || 'tab';
    document.querySelectorAll(tabsSelector).forEach(function (btn) {
        btn.addEventListener('click', function () {
            document.querySelectorAll(tabsSelector).forEach(function (b) { b.classList.remove('active'); });
            document.querySelectorAll('.tab-panel').forEach(function (p) { p.classList.remove('active'); });
            btn.classList.add('active');
            var id = btn.getAttribute('data-tab');
            var panel = document.getElementById(panelPrefix + id.charAt(0).toUpperCase() + id.slice(1));
            if (panel) panel.classList.add('active');
        });
    });
}

/* ── Theme Sync ──────────────────────────────────────────────────────── */
(function () {
    try {
        var theme = localStorage.getItem('selena-theme') || 'dark';
        if (theme === 'light') document.documentElement.classList.add('light');
    } catch (e) {}
    try {
        var p = window.parent.document.documentElement;
        function _syncParent() {
            document.documentElement.classList.toggle('light', p.classList.contains('light'));
            document.documentElement.classList.toggle('has-wallpaper', p.classList.contains('has-wallpaper'));
        }
        _syncParent();
        new MutationObserver(_syncParent).observe(p, { attributes: true, attributeFilter: ['class'] });
    } catch (e) {}
})();

/* ── Message Listener (theme + lang) ─────────────────────────────────── */
window.addEventListener('message', function (e) {
    if (!e.data) return;
    if (e.data.type === 'theme_changed') {
        var theme = e.data.theme || 'dark';
        document.documentElement.classList.toggle('light', theme === 'light');
    }
    if (e.data.type === 'theme_vars_changed') {
        var link = document.querySelector('link[href*="theme.css"]');
        if (link) link.href = '/api/shared/theme.css?t=' + Date.now();
        try {
            var p = window.parent.document.documentElement;
            document.documentElement.classList.toggle('has-wallpaper', p.classList.contains('has-wallpaper'));
        } catch (_) {}
    }
    if (e.data.type === 'lang_changed') {
        try { LANG = localStorage.getItem('selena-lang') || 'en'; } catch (ex) {}
        applyLang();
        if (typeof refresh === 'function') refresh();
        else if (typeof loadStatus === 'function') loadStatus();
        else if (typeof load === 'function') load();
    }
});
"""


@router.get("/widget-common.js")
async def widget_common_js() -> Response:
    return Response(
        content=WIDGET_COMMON_JS,
        media_type="application/javascript",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )
