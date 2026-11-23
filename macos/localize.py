#!/usr/bin/env python3
"""Port: route the author's user-visible strings through tr() (L10n.swift).

Runs at build time on a fresh upstream checkout, so the author's sources stay
untouched in git. Like the Windows build's install_translation(): every
Text / Button / Menu / TextField / .help / .accessibilityLabel first argument
and every NSMenuItem title goes through tr(). Also adds the language row to
the panel and the right-click menu, and lets the pairing-code field take
the Russian keyboard layout.

usage: localize.py <path to Sources/Fling>
"""
import pathlib
import re
import sys

CALLS = re.compile(r"(?<![\w.])(Text|Button|Menu|TextField)\(|\.(help|accessibilityLabel)\(")
LABELED = re.compile(r"\s*[A-Za-z_]\w*\s*:(?!:)")


def arg_end(src: str, i: int) -> int:
    """Index of the ',' or ')' that ends the argument starting at i (string/paren aware)."""
    depth = 0
    stack = []          # 's' = inside a string literal, 'i' = inside \( ) interpolation
    while i < len(src):
        c = src[i]
        if stack and stack[-1] == "s":
            if c == "\\" and src[i + 1] == "(":
                stack.append("i"); depth_i = 0; i += 2; stack.append(0); continue
            if c == "\\":
                i += 2; continue
            if c == '"':
                stack.pop()
            i += 1; continue
        if stack and isinstance(stack[-1], int):       # inside interpolation
            if c == '"':
                stack.append("s")
            elif c == "(":
                stack[-1] += 1
            elif c == ")":
                if stack[-1] == 0:
                    stack.pop(); stack.pop()            # leave interpolation, back in string
                else:
                    stack[-1] -= 1
            i += 1; continue
        if c == '"':
            stack.append("s")
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0:
                return i
            depth -= 1
        elif c == "," and depth == 0:
            return i
        i += 1
    raise ValueError("unbalanced call")


def wrap_calls(src: str) -> tuple[str, int]:
    out, pos, n = [], 0, 0
    for m in CALLS.finditer(src):
        start = m.end()
        if start < pos:
            continue
        rest = src[start:start + 40]
        if rest.lstrip().startswith((")", "Image(", "tr(")) or LABELED.match(rest):
            continue
        end = arg_end(src, start)
        out.append(src[pos:start]); out.append("tr(" + src[start:end] + ")")
        pos = end; n += 1
    out.append(src[pos:])
    return "".join(out), n


def must_sub(src: str, old: str, new: str, name: str) -> str:
    if old not in src:
        sys.exit(f"localize.py: anchor not found in {name}: {old!r} (upstream changed?)")
    return src.replace(old, new, 1)


def main(root: pathlib.Path):
    total = 0
    for f in sorted(root.glob("*.swift")):
        if f.name in ("L10n.swift", "PreviewHarness.swift"):
            continue
        src = f.read_text()
        src, n = wrap_calls(src)
        if f.name == "ContextMenuBuilder.swift":
            src = src.replace("NSMenuItem(title: title,", "NSMenuItem(title: tr(title),")
            src = must_sub(src, 'menu.addItem(action("Quit Fling"',
                           "menu.addItem(action(Lang.switchTitle) { Lang.toggle() })\n"
                           '        menu.addItem(action("Quit Fling"', f.name)
        if f.name == "PanelRows.swift":
            # pairing code: Russian layout keys → the same Latin hex letters (Windows _RU_KEYS)
            src = must_sub(src, ".onSubmit { submit() }",
                           ".onSubmit { submit() }\n"
                           "                    .onChange(of: pin) { _, v in let c = pinClean(v); if c != v { pin = c } }",
                           f.name)
        if f.name == "PanelView.swift":
            src = must_sub(src, 'MenuRow(title: "Quit Fling"',
                           "MenuRow(title: Lang.switchTitle) { Lang.toggle() }\n"
                           '            MenuRow(title: "Quit Fling"', f.name)
        f.write_text(src)
        total += n
    print(f"localize: {total} strings routed through tr()")


if __name__ == "__main__":
    main(pathlib.Path(sys.argv[1]))
