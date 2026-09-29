#!/usr/bin/env python3
"""Builds dist/Brew.alfredworkflow from src/: python3 tools/build.py"""
import os
import plistlib
import shutil
import uuid
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC, DIST = os.path.join(ROOT, "src"), os.path.join(ROOT, "dist")
BUILD = os.path.join(DIST, "build")
VERSION = "1.2.0"

CMD, ALT, CTRL, SHIFT = 1048576, 524288, 262144, 131072

objects, connections, uidata = [], {}, {}


def uid(seed):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "alfred-brew/" + seed)).upper()


def add(seed, type_, config, x, y, icon=None, note=None):
    u = uid(seed)
    objects.append({"uid": u, "type": type_, "version": 1 if type_ != "alfred.workflow.input.scriptfilter" else 3,
                    "config": config})
    uidata[u] = {"xpos": x, "ypos": y, **({"note": note} if note else {})}
    if icon:
        shutil.copy(os.path.join(SRC, "icons", icon + ".png"), os.path.join(BUILD, u + ".png"))
    return u


def connect(src, dst, mods=(0,)):
    for m in mods:
        connections.setdefault(src, []).append(
            {"destinationuid": dst, "modifiers": m, "modifiersubtext": "", "vitoclose": False})


def script_filter(seed, keyword, mode, title, subtext, running, y, icon):
    return add(seed, "alfred.workflow.input.scriptfilter", {
        "keyword": keyword, "argumenttype": 1, "withspace": True, "title": title, "subtext": subtext,
        "runningsubtext": running, "type": 0, "scriptargtype": 1, "script": f'./run.sh {mode} "$1"',
        "scriptfile": "", "escaping": 102, "alfredfiltersresults": False, "alfredfiltersresultsmatchmode": 0,
        "queuedelaycustom": 3, "queuedelayimmediatelyinitially": True, "queuedelaymode": 0, "queuemode": 1,
        "skipuniversalaction": True,
    }, 30, y, icon)


README = """# Brew for Alfred

Search, install and manage Homebrew formulae and casks. A port of the Raycast "Brew" extension.

## Keywords
- **brew** <query>: search formulae & casks and install them (empty query lists every command)
- **brewi**: installed packages, pinned first
- **brewu**: outdated packages · Upgrade All · Preview Upgrades (dry run)
- **brewv**: installed formulae with known OSV advisories (brew vulns, Homebrew 7)
- **brewd**: brew doctor report, with Fix All
- **brews**: start, stop and restart Homebrew services
- **brewm** <query>: search the Mac App Store (price, rating, installed state). With `mas` installed:
  ↩ gets free apps or updates outdated ones via `sudo mas` in Terminal; ⇧↩ uninstalls or installs a
  purchased app. Without `mas`, apps open in the App Store. Empty query lists installed App Store apps
  and pending updates. ⌘↩ App Store · ⌥↩ web page · ⌃↩ copy link

## Keys on a package
- ↩ primary action (install / upgrade / unpin & upgrade, or all actions when up to date)
- ⌘↩ run the primary action in your Terminal
- ⌥↩ all actions: pin, unpin, reinstall, uninstall, zap, preview upgrade, info…
- ⌃↩ open homepage · ⇧ Quick Look formulae.brew.sh (install statistics) · ⌘C copy name

Background jobs queue up (brew can't run two at once) and report progress with notifications.
Jobs that need your password continue in Terminal. The full output of the last job is under brew ▸ Open Last Log. Keywords are configurable in the workflow's Configure panel.
"""


def main():
    shutil.rmtree(BUILD, ignore_errors=True)
    os.makedirs(BUILD)
    for f in ("brew.py", "run.sh", "icon.png"):
        shutil.copy2(os.path.join(SRC, f), BUILD)
    shutil.copytree(os.path.join(SRC, "icons"), os.path.join(BUILD, "icons"))

    dispatch = add("dispatch", "alfred.workflow.action.script", {
        "type": 0, "scriptargtype": 1, "script": './run.sh dispatch "$1"', "scriptfile": "",
        "concurrently": True, "escaping": 102}, 520, 250, "terminal", "Runs the chosen action")
    done = add("done-notify", "alfred.workflow.output.notification", {
        "title": "Brew", "text": "{query}", "onlyshowifquerypopulated": True,
        "removeextension": False, "lastpathcomponent": False}, 760, 250, "icon")
    connect(dispatch, done)

    filters = [
        ("search", "{var:kw_search}", "search", "Search Homebrew", "Search, install and manage formulae & casks", "Searching…", "search"),
        ("installed", "{var:kw_installed}", "installed", "Show Installed", "Installed formulae & casks", "Loading installed packages…", "installed"),
        ("outdated", "{var:kw_outdated}", "outdated", "Show Upgrades", "Review outdated packages and upgrade them", "Checking for upgrades…", "outdated"),
        ("vulns", "{var:kw_vulns}", "vulns", "Show Vulnerabilities", "Installed formulae with known advisories", "Running brew vulns…", "vuln"),
        ("doctor", "{var:kw_doctor}", "doctor", "Run Doctor", "Show the brew doctor report and its fixes", "Running brew doctor…", "doctor"),
        ("services", "{var:kw_services}", "services", "Manage Services", "Start, stop & restart Homebrew services", "Loading services…", "services"),
    ]
    for i, (seed, kw, mode, title, sub, running, ic) in enumerate(filters):
        sf = script_filter(seed, kw, mode, title, sub, running, 30 + i * 130, ic)
        connect(sf, dispatch, (0, CMD, ALT, CTRL, SHIFT))

    # App Store search hits Apple's API, so wait for a pause in typing instead of querying every keystroke.
    appstore = script_filter("appstore", "{var:kw_appstore}", "appstore", "Mac App Store",
                             "Search the App Store, install and update apps with mas", "Searching the App Store…", 1220, "appstore")
    next(o for o in objects if o["uid"] == appstore)["config"].update(
        {"queuedelaymode": 1, "queuedelayimmediatelyinitially": True})
    connect(appstore, dispatch, (0, CMD, ALT, CTRL, SHIFT))

    # Second-level action list, opened by dispatch through the "menu" trigger.
    menu_trigger = add("menu-trigger", "alfred.workflow.trigger.external",
                       {"triggerid": "menu", "availableviaurlhandler": False}, 30, 830, "info")
    menu_vars = add("menu-vars", "alfred.workflow.utility.argument", {
        "argument": "", "passthroughargument": False, "variables": {"menu_arg": "{query}"}}, 230, 860)
    menu_sf = add("menu", "alfred.workflow.input.scriptfilter", {
        "keyword": "brewmenu", "argumenttype": 1, "withspace": True, "title": "Brew actions", "subtext": "",
        "runningsubtext": "Loading…", "type": 0, "scriptargtype": 1, "script": './run.sh menu "$1"',
        "scriptfile": "", "escaping": 102, "alfredfiltersresults": False, "alfredfiltersresultsmatchmode": 0,
        "queuedelaycustom": 3, "queuedelayimmediatelyinitially": True, "queuedelaymode": 0, "queuemode": 1,
        "skipuniversalaction": True}, 330, 830, "info")
    connect(menu_trigger, menu_vars)
    connect(menu_vars, menu_sf)
    connect(menu_sf, dispatch, (0, CMD, ALT, CTRL, SHIFT))

    terminal_trigger = add("terminal-trigger", "alfred.workflow.trigger.external",
                           {"triggerid": "terminal", "availableviaurlhandler": False}, 30, 980, "terminal")
    terminal = add("terminal", "alfred.workflow.action.terminalcommand",
                   {"command": "{query}", "escaping": 0}, 330, 980, "terminal", "Uses your Terminal preference")
    connect(terminal_trigger, terminal)

    notify_trigger = add("notify-trigger", "alfred.workflow.trigger.external",
                         {"triggerid": "notify", "availableviaurlhandler": False}, 30, 1110, "icon")
    notify = add("progress-notify", "alfred.workflow.output.notification", {
        "title": "Brew", "text": "{query}", "onlyshowifquerypopulated": True,
        "removeextension": False, "lastpathcomponent": False}, 330, 1110, "icon")
    connect(notify_trigger, notify)

    def kw_field(var, label, default):
        return {"type": "textfield", "variable": var, "label": label, "description": "",
                "config": {"default": default, "placeholder": default, "required": True, "trim": True}}

    def checkbox(var, label, text):
        return {"type": "checkbox", "variable": var, "label": label, "description": "",
                "config": {"default": False, "required": False, "text": text}}

    info = {
        "bundleid": "com.alfredapp.homebrew", "name": "Brew", "version": VERSION, "category": "Tools",
        "createdby": "x-o-r-r-o", "webaddress": "https://github.com/x-o-r-r-o/alfred-brew",
        "description": "Search, install and manage Homebrew formulae and casks",
        "readme": README, "objects": objects, "connections": connections, "uidata": uidata,
        "disabled": False, "variablesdontexport": [],
        "userconfigurationconfig": [
            kw_field("kw_search", "Search keyword", "brew"),
            kw_field("kw_installed", "Installed keyword", "brewi"),
            kw_field("kw_outdated", "Upgrades keyword", "brewu"),
            kw_field("kw_vulns", "Vulnerabilities keyword", "brewv"),
            kw_field("kw_doctor", "Doctor keyword", "brewd"),
            kw_field("kw_services", "Services keyword", "brews"),
            kw_field("kw_appstore", "App Store keyword", "brewm"),
            checkbox("installed_hide_deps", "Installed list", "Hide formulae installed only as dependencies"),
            checkbox("no_parallel_downloads", "Downloads", "Turn off concurrent downloads"),
            checkbox("greedy", "Casks", "Include auto-updating casks in upgrades (--greedy)"),
            {"type": "textfield", "variable": "appstore_country", "label": "App Store country",
             "description": "Two-letter storefront code (us, gb, pk…). Leave empty to use your Mac's region",
             "config": {"default": "", "placeholder": "auto", "required": False, "trim": True}},
            {"type": "textfield", "variable": "brew_path", "label": "brew path",
             "description": "Leave empty to auto-detect /opt/homebrew or /usr/local",
             "config": {"default": "", "placeholder": "/opt/homebrew/bin/brew", "required": False, "trim": True}},
        ],
    }
    with open(os.path.join(BUILD, "info.plist"), "wb") as f:
        plistlib.dump(info, f)

    out = os.path.join(DIST, "Brew.alfredworkflow")
    if os.path.exists(out):
        os.remove(out)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for base, _, files in os.walk(BUILD):
            for f in files:
                p = os.path.join(base, f)
                zi = zipfile.ZipInfo.from_file(p, os.path.relpath(p, BUILD))
                with open(p, "rb") as fh:
                    z.writestr(zi, fh.read(), zipfile.ZIP_DEFLATED)
    print(out)


if __name__ == "__main__":
    main()
