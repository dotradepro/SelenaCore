#!/usr/bin/env python3
"""Inject theme CSS variables and detection script into system module HTML files.

Replaces hardcoded dark-only colors with CSS custom properties that support
both dark and light themes, matching the main app's design tokens from src/index.css.
"""
import re
import os

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "system_modules")

FILES = [
    "automation_engine/widget.html",
    "automation_engine/settings.html",
    "device_watchdog/widget.html",
    "device_watchdog/settings.html",
    "energy_monitor/widget.html",
    "energy_monitor/settings.html",
    "device_control/widget.html",
    "device_control/settings.html",
    "media_player/widget.html",
    "media_player/settings.html",
    "notification_router/widget.html",
    "notification_router/settings.html",
    "presence_detection/widget.html",
    "presence_detection/settings.html",
    "protocol_bridge/widget.html",
    "protocol_bridge/settings.html",
    "scheduler/settings.html",
    "update_manager/widget.html",
    "update_manager/settings.html",
    "user_manager/settings.html",
    "voice_core/widget.html",
    "voice_core/settings.html",
    "weather_service/widget.html",
    "weather_service/settings.html",
]

# Design tokens matching src/index.css
CSS_VARS = """        :root {
            --bg: #0B0C10;
            --sf: #12131A;
            --sf2: #191A22;
            --sf3: #20212C;
            --b: rgba(255, 255, 255, .07);
            --b2: rgba(255, 255, 255, .13);
            --tx: #EDEEF5;
            --tx2: #888EA8;
            --tx3: #484D66;
            --ac: #4F8CF7;
            --gr: #2EC98A;
            --am: #F5A93A;
            --rd: #E05454;
        }

        :root.light {
            --bg: #F3F4F8;
            --sf: #FFFFFF;
            --sf2: #F0F1F5;
            --sf3: #E4E5ED;
            --b: rgba(0, 0, 0, .08);
            --b2: rgba(0, 0, 0, .14);
            --tx: #1A1C24;
            --tx2: #5C6178;
            --tx3: #9498AD;
            --ac: #3B7AE8;
            --gr: #1FAF75;
            --am: #DB8F1C;
            --rd: #C94040;
        }

"""

# Detects parent page theme class and mirrors it to iframe's <html>
THEME_SCRIPT = (
    '    <script>(function(){try{var p=window.parent.document.documentElement;'
    'function s(){document.documentElement.classList.toggle("light",'
    'p.classList.contains("light"))}s();new MutationObserver(s).observe(p,'
    '{attributes:true,attributeFilter:["class"]});}catch(e){}})();</script>\n'
)


def replace_hex(content, old_hex, new_var):
    """Replace a hex color, ensuring no partial match with longer hex."""
    pattern = re.escape(old_hex) + r'(?![0-9a-fA-F])'
    return re.sub(pattern, new_var, content, flags=re.IGNORECASE)


def process_file(filepath):
    with open(filepath, 'r') as f:
        content = f.read()

    original = content

    # ── Step 1: Replace colors BEFORE injecting CSS vars ──
    #    (to avoid replacing colors inside the variable definitions)

    # 6-digit hex colors
    hex6 = [
        # Body backgrounds
        ('#0f1117', 'var(--bg)'),
        ('#0f172a', 'var(--bg)'),
        ('#0e1220', 'var(--bg)'),
        ('#12131A', 'var(--bg)'),
        # Surfaces / cards
        ('#1a1d27', 'var(--sf)'),
        ('#1e293b', 'var(--sf)'),
        ('#191A22', 'var(--sf)'),
        ('#1e2a44', 'var(--sf)'),
        # Deep surfaces
        ('#111827', 'var(--sf2)'),
        ('#20212C', 'var(--sf2)'),
        ('#161926', 'var(--sf2)'),
        ('#292A36', 'var(--sf2)'),
        # Primary text
        ('#e0e0e0', 'var(--tx)'),
        ('#e2e8f0', 'var(--tx)'),
        ('#f1f5f9', 'var(--tx)'),
        ('#f8fafc', 'var(--tx)'),
        ('#EDEEF5', 'var(--tx)'),
        ('#e8eaf0', 'var(--tx)'),
        ('#cbd5e1', 'var(--tx)'),
        # Secondary text
        ('#94a3b8', 'var(--tx2)'),
        ('#888EA8', 'var(--tx2)'),
        ('#8890b0', 'var(--tx2)'),
        ('#7880a0', 'var(--tx2)'),
        ('#c8cce8', 'var(--tx2)'),
        ('#666e90', 'var(--tx2)'),
        # Tertiary text
        ('#64748b', 'var(--tx3)'),
        ('#484D66', 'var(--tx3)'),
        ('#475569', 'var(--tx3)'),
        ('#5a6180', 'var(--tx3)'),
        ('#5a6080', 'var(--tx3)'),
        ('#454c6a', 'var(--tx3)'),
        ('#404866', 'var(--tx3)'),
        ('#30364e', 'var(--tx3)'),
        ('#252c44', 'var(--tx3)'),
        ('#252c40', 'var(--tx3)'),
        # Borders
        ('#334155', 'var(--b2)'),
        ('#2a2a3a', 'var(--b2)'),
        ('#1e2333', 'var(--b2)'),
        # Accents
        ('#5c8ee6', 'var(--ac)'),
        ('#4a7bd4', 'var(--ac)'),
        ('#0ea5e9', 'var(--ac)'),
        ('#38bdf8', 'var(--ac)'),
        ('#3b82f6', 'var(--ac)'),
        ('#2563eb', 'var(--ac)'),
        ('#1d4ed8', 'var(--ac)'),
        ('#4F8CF7', 'var(--ac)'),
        ('#3D7AE5', 'var(--ac)'),
        ('#60a5fa', 'var(--ac)'),
        # Greens
        ('#50c878', 'var(--gr)'),
        ('#4ade80', 'var(--gr)'),
        ('#34d399', 'var(--gr)'),
        ('#2EC98A', 'var(--gr)'),
        # Reds
        ('#e06c75', 'var(--rd)'),
        ('#f87171', 'var(--rd)'),
        ('#E05454', 'var(--rd)'),
        ('#C94444', 'var(--rd)'),
        # Ambers
        ('#f59e0b', 'var(--am)'),
        ('#F5A93A', 'var(--am)'),
        ('#FFD700', 'var(--am)'),
    ]
    for old, new in hex6:
        content = replace_hex(content, old, new)

    # 3-digit hex colors — context-aware for #333 and #444
    # #333 in border context → var(--b2), as background → var(--sf3)
    content = re.sub(
        r'(border[^:]*:\s*[^;]*?)#333(?![0-9a-fA-F])',
        r'\1var(--b2)',
        content,
        flags=re.IGNORECASE,
    )
    content = replace_hex(content, '#333', 'var(--sf3)')  # remaining #333 (backgrounds)

    content = replace_hex(content, '#444', 'var(--sf3)')

    # Other 3-digit hex codes
    hex3 = [
        ('#aaa', 'var(--tx2)'),
        ('#888', 'var(--tx2)'),
        ('#ccc', 'var(--tx2)'),
        ('#666', 'var(--tx3)'),
        ('#555', 'var(--tx3)'),
    ]
    for old, new in hex3:
        content = replace_hex(content, old, new)

    # #111 — only when standalone (not part of a longer hex)
    content = replace_hex(content, '#111', 'var(--sf2)')

    # color: #fff → color: var(--tx)  (text headings, labels)
    content = re.sub(
        r'(color\s*:\s*)#fff(?![0-9a-fA-F])',
        r'\1var(--tx)',
        content,
        flags=re.IGNORECASE,
    )

    # rgba(255,255,255,alpha) overlays — flexible spacing
    def replace_rgba_w(text, alpha_re, var_name):
        return re.sub(
            r'rgba\(\s*255\s*,\s*255\s*,\s*255\s*,\s*' + alpha_re + r'\s*\)',
            var_name, text,
        )

    for alpha in [r'0?\.02\d?', r'0?\.04\d?', r'0?\.05\d?', r'0?\.06\d?', r'0?\.07\d?']:
        content = replace_rgba_w(content, alpha, 'var(--b)')
    content = replace_rgba_w(content, r'0?\.13', 'var(--b2)')

    # Energy monitor warm gradient → flat bg
    content = re.sub(
        r'linear-gradient\(\s*135deg\s*,\s*#1a0a00\s*0%\s*,\s*#1e1400\s*100%\s*\)',
        'var(--bg)',
        content,
        flags=re.IGNORECASE,
    )

    # ── Step 2: Inject CSS vars AFTER all replacements ──
    content = re.sub(
        r'(<style[^>]*>)\s*\n',
        r'\g<0>' + CSS_VARS,
        content,
        count=1,
    )

    # ── Step 3: Inject theme detection script before </body> ──
    content = content.replace('</body>', THEME_SCRIPT + '</body>')

    with open(filepath, 'w') as f:
        f.write(content)

    return content != original


def main():
    ok = 0
    for rel_path in FILES:
        filepath = os.path.join(BASE, rel_path)
        if os.path.exists(filepath):
            changed = process_file(filepath)
            status = "UPDATED" if changed else "NO CHANGE"
            print(f"  {status}: {rel_path}")
            if changed:
                ok += 1
        else:
            print(f"  MISSING: {rel_path}")
    print(f"\n✓ {ok}/{len(FILES)} files updated")


if __name__ == "__main__":
    main()
