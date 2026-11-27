"""Generate site-independent CSS and verify the published color combinations."""
import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent
THEMES = ("light", "dark")


def css_value(value, brand):
    if value.startswith("@brand.") and value[7:] in brand:
        return f"var(--op-brand-{value[7:]})"
    if not re.fullmatch(r"#[A-F0-9]{6}", value):
        raise ValueError(f"Expected an opaque sRGB color or a known brand reference: {value}")
    return value


def block(selector, declarations, indent=""):
    body = [f"{indent}  {key}: {value};" for key, value in declarations]
    return "\n".join([f"{indent}{selector} {{", *body, indent + "}"])


def stylesheet(spec):
    brand, roles = spec["brand"], spec["roles"]
    brand_vars = [(f"--op-brand-{k}", css_value(v, {})) for k, v in brand.items()]

    def declarations(theme):
        colors = [(f"--op-{key}", css_value(row[theme], brand)) for key, row in roles.items()]
        return [("color-scheme", theme), *colors]

    return "\n\n".join([
        f"/* Orchpact {spec['version']} · Generated from tokens.json by validate.py. */",
        block(":root", brand_vars),
        block(':root, [data-op-theme="light"]', declarations("light")),
        '@media (prefers-color-scheme: dark) {\n'
        + block(':root:not([data-op-theme="light"])', declarations("dark"), "  ") + '\n}',
        block('[data-op-theme="dark"]', declarations("dark")),
    ]) + "\n"


def luminance(color):
    srgb = (int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in srgb]
    return sum(x * y for x, y in zip(linear, (0.2126, 0.7152, 0.0722)))


def contrast(foreground, background):
    low, high = sorted((luminance(foreground), luminance(background)))
    return (high + 0.05) / (low + 0.05)


def combinations():
    panels = ("page", "surface", "subtle", "selected-bg")
    for fg in ("text", "muted", "link", "link-hover", "link-visited"):
        for bg in panels:
            yield fg, bg, 4.5
    for fg in ("focus", "control", "edge", "edge-selected"):
        for bg in panels:
            yield fg, bg, 3
    for bg in ("action", "action-hover", "action-active"):
        yield "on-action", bg, 4.5
        yield bg, "surface", 3
    for state in ("neutral", "info", "success", "warning", "danger"):
        yield state + "-fg", state + "-bg", 4.5
        yield state + "-fg", "surface", 4.5
    for fg in ("code-text", "code-comment", "code-string", "code-keyword"):
        yield fg, "code-bg", 4.5
    yield "on-hero", "hero-bg", 4.5
    yield "brand-lime", "hero-bg", 4.5
    yield "on-delivery", "delivery-bg", 4.5
    yield "disabled-fg", "disabled-bg", 4.5


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-css", action="store_true", help="regenerate tokens.css after checks pass")
    parser.add_argument("--report", action="store_true", help="show each contrast pair")
    args = parser.parse_args()
    spec = json.loads((ROOT / "tokens.json").read_text())
    css = stylesheet(spec)
    expected = {"indigo": "#262896", "lime": "#EAFF58", "paper": "#F7F4EB"}
    if any(spec["brand"].get(k) != v for k, v in expected.items()):
        raise ValueError("Brand colors differ from the approved icon.")
    count = 0
    for theme in THEMES:
        colors = {"brand-" + k: v for k, v in spec["brand"].items()}
        for role, row in spec["roles"].items():
            val = row[theme]
            colors[role] = spec["brand"][val[7:]] if val.startswith("@brand.") else val
        for fg, bg, minimum in combinations():
            ratio = contrast(colors[fg], colors[bg])
            if ratio < minimum:
                raise ValueError(f"{theme} {fg} on {bg}: {ratio:.3f} below {minimum}")
            if args.report:
                print(f"{theme:5} {fg:18} / {bg:16} {ratio:.2f}:1")
            count += 1
    path = ROOT / "tokens.css"
    if args.write_css:
        path.write_text(css)
    elif not path.exists() or path.read_text() != css:
        raise ValueError("tokens.css has drifted. Run python3 validate.py --write-css.")
    print(f"Orchpact {spec['version']} · {len(spec['roles'])} roles · {count} contrast checks passed.")


if __name__ == "__main__":
    try:
        main()
    except (KeyError, OSError, ValueError) as error:
        sys.exit(str(error))
