#!/usr/bin/env python3
"""Homebrew for Alfred: search, install and manage Homebrew formulae and casks.

Script Filter modes print Alfred JSON; `dispatch` runs the chosen action.
"""
import fcntl
import json
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.parse

BUNDLE_ID = os.environ.get("alfred_workflow_bundleid", "com.alfredapp.homebrew")
CACHE_DIR = os.environ.get("alfred_workflow_cache") or os.path.expanduser(
    "~/Library/Caches/com.runningwithcrayons.Alfred/Workflow Data/" + BUNDLE_ID)
os.makedirs(CACHE_DIR, exist_ok=True)

API = "https://formulae.brew.sh/api"
INDEX_TTL = 24 * 3600
LIST_TTL = 10 * 60
VULNS_TTL = 60 * 60
MAX_RESULTS = 60


def find_brew():
    for p in (os.environ.get("brew_path", ""), "/opt/homebrew/bin/brew", "/usr/local/bin/brew",
              "/home/linuxbrew/.linuxbrew/bin/brew"):
        if p and os.access(p, os.X_OK):
            return p
    return "brew"


BREW = find_brew()
PREFIX = os.path.dirname(os.path.dirname(os.path.realpath(BREW))) if os.path.isabs(BREW) else "/opt/homebrew"
GREEDY = os.environ.get("greedy") == "1"
# Anything brew changes when packages are installed, removed, pinned or re-fetched.
INSTALL_DEPS = [os.path.join(PREFIX, p) for p in ("Cellar", "Caskroom", "var/homebrew/pinned", "var/homebrew/linked")]
OUTDATED_DEPS = INSTALL_DEPS + [os.path.expanduser("~/Library/Caches/Homebrew/api")]


def brew_env():
    env = dict(os.environ)
    env["PATH"] = os.path.dirname(BREW) + ":/usr/bin:/bin:/usr/sbin:/sbin:" + env.get("PATH", "")
    env.setdefault("HOMEBREW_NO_ENV_HINTS", "1")
    env.setdefault("HOMEBREW_NO_COLOR", "1")
    if os.environ.get("no_parallel_downloads") == "1":
        env["HOMEBREW_DOWNLOAD_CONCURRENCY"] = "1"
    return env


def run_brew(*args, check=False):
    p = subprocess.run([BREW, *args], capture_output=True, text=True, env=brew_env())
    if check and p.returncode != 0:
        lines = (p.stderr or p.stdout).strip().splitlines()
        raise RuntimeError(lines[-1] if lines else f"brew {' '.join(args)} failed")
    return p


def icon(name):
    return {"path": f"icons/{name}.png"}


# ---------------------------------------------------------------- caching

def cache_path(name):
    return os.path.join(CACHE_DIR, name)


def read_json(name):
    try:
        with open(cache_path(name)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_json(name, data):
    tmp = cache_path(name + ".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, cache_path(name))


def age(name):
    try:
        return time.time() - os.path.getmtime(cache_path(name))
    except OSError:
        return None


def spawn_refresh(what):
    """Refresh a cache in the background, at most one refresher per cache.

    Returns True while a refresh is running, so callers know to ask Alfred to rerun.
    """
    lock = cache_path(what + ".lock")
    a, err = age(what + ".lock"), age(what + ".error")
    if a is not None and a < 300:
        return True
    if err is not None and err < 60:
        return False  # failed moments ago: don't respawn brew every second
    open(lock, "w").close()
    subprocess.Popen([sys.executable, __file__, "refresh", what], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return True


def newest_mtime(paths):
    m = 0
    for p in paths:
        try:
            m = max(m, os.path.getmtime(p))
        except OSError:
            pass
    return m


class Cached:
    """Stale-while-revalidate: serve old data instantly and refresh behind it."""
    stale = False

    def get(self, name, ttl, producer, sync_if_missing=True, deps=()):
        a = age(name)
        data = read_json(name)
        fresh = a is not None and a < ttl and newest_mtime(deps) < time.time() - a
        if data is not None and fresh:
            return data
        if data is not None:
            self.stale = spawn_refresh(name) or self.stale
            return data
        if not sync_if_missing:
            self.stale = spawn_refresh(name) or self.stale
            return None
        data = producer()
        write_json(name, data)
        return data


def invalidate():
    for n in ("installed.json", "outdated.json", "vulns.json", "services.json", "preview.json"):
        try:
            os.remove(cache_path(n))
        except OSError:
            pass


# ---------------------------------------------------------------- producers

def app_icon_for(cask):
    for art in cask.get("artifacts") or []:
        if isinstance(art, dict) and "app" in art:
            for a in art["app"]:
                if isinstance(a, str):
                    p = os.path.join("/Applications", a)
                    if os.path.exists(p):
                        return p
    return None


def produce_installed():
    d = json.loads(run_brew("info", "--json=v2", "--installed", check=True).stdout)
    out = []
    for f in d.get("formulae", []):
        inst = f.get("installed") or [{}]
        out.append({
            "name": f["name"], "kind": "formula", "desc": f.get("desc") or "",
            "version": inst[-1].get("version", ""), "latest": (f.get("versions") or {}).get("stable") or "",
            "pinned": bool(f.get("pinned")), "outdated": bool(f.get("outdated")),
            "on_request": bool(inst[-1].get("installed_on_request")),
            "homepage": f.get("homepage") or "", "deprecated": bool(f.get("deprecated") or f.get("disabled")),
        })
    for c in d.get("casks", []):
        out.append({
            "name": c["token"], "kind": "cask", "desc": c.get("desc") or "",
            "display": ", ".join(c.get("name") or []),
            "version": c.get("installed") or "", "latest": c.get("version") or "",
            "pinned": bool(c.get("pinned")), "outdated": bool(c.get("outdated")), "on_request": True,
            "homepage": c.get("homepage") or "", "app": app_icon_for(c),
            "deprecated": bool(c.get("deprecated") or c.get("disabled")),
        })
    return out


def produce_outdated():
    d = json.loads(run_brew("outdated", "--json=v2", *(["--greedy"] if GREEDY else []), check=True).stdout)
    out = []
    for kind, key in (("formula", "formulae"), ("cask", "casks")):
        for p in d.get(key, []):
            out.append({"name": p["name"], "kind": kind,
                        "installed": ", ".join(p.get("installed_versions") or []),
                        "current": p.get("current_version") or "", "pinned": bool(p.get("pinned"))})
    return out


def produce_vulns():
    p = run_brew("vulns", "--json", "-m", "0")
    try:
        d = json.loads(p.stdout)
    except ValueError:
        err = (p.stderr or p.stdout).strip()
        raise RuntimeError(err.splitlines()[-1] if err else "brew vulns failed (needs Homebrew 7)")
    return d.get("findings", [])


def produce_services():
    return json.loads(run_brew("services", "list", "--json", check=True).stdout or "[]")


def fetch_json(url):
    # System curl uses the macOS keychain; python.org builds often lack CA certificates.
    p = subprocess.run(["/usr/bin/curl", "-fsSL", "--compressed", "--max-time", "120",
                        "-A", "alfred-brew-workflow", url], capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(f"Download failed: {url} ({p.stderr.decode().strip()})")
    return json.loads(p.stdout)


def produce_index():
    """Compact search index: [name, kind, desc, version, homepage, installs30d, extra-search-terms, deprecated]."""
    counts = {}
    for url, key in ((f"{API}/analytics/install-on-request/30d.json", "formula"),
                     (f"{API}/analytics/cask-install/30d.json", "cask")):
        try:
            for it in fetch_json(url).get("items", []):
                counts[(key, it.get(key))] = int(str(it.get("count", "0")).replace(",", ""))
        except Exception:
            pass
    rows = []
    for f in fetch_json(f"{API}/formula.json"):
        if f.get("disabled"):
            continue
        extra = " ".join((f.get("aliases") or []) + (f.get("oldnames") or []))
        rows.append([f["name"], "formula", f.get("desc") or "", (f.get("versions") or {}).get("stable") or "",
                     f.get("homepage") or "", counts.get(("formula", f["name"]), 0), extra, bool(f.get("deprecated"))])
    for c in fetch_json(f"{API}/cask.json"):
        if c.get("disabled"):
            continue
        extra = " ".join((c.get("name") or []) + (c.get("old_tokens") or []))
        rows.append([c["token"], "cask", c.get("desc") or "", c.get("version") or "",
                     c.get("homepage") or "", counts.get(("cask", c["token"]), 0), extra, bool(c.get("deprecated"))])
    return rows


PRODUCERS = {"mas-installed.json": lambda: produce_mas_installed(), "mas-outdated.json": lambda: produce_mas_outdated(),
             "installed.json": produce_installed, "outdated.json": produce_outdated,
             "vulns.json": produce_vulns, "services.json": produce_services, "index.json": produce_index}


# ---------------------------------------------------------------- output helpers

def emit(items, rerun=None, variables=None):
    out = {"items": items, "skipknowledge": True}
    if rerun:
        out["rerun"] = rerun
    if variables:
        out["variables"] = variables
    print(json.dumps(out))


def action(_a, **kw):
    """Encode an action for `dispatch` as the item arg."""
    return json.dumps({"a": _a, **kw})


def run_arg(cmds, label, done=None):
    """Background run of one or more brew commands (each a list of args, without 'brew')."""
    return action("run", cmds=cmds, label=label, done=done or label)


def term_arg(cmds):
    return action("terminal", cmd=" && ".join(shlex.join(["brew", *c]) for c in cmds))


def with_terminal(item, cmds, label="Run in Terminal"):
    item.setdefault("mods", {})["cmd"] = {"arg": term_arg(cmds), "subtitle": f"⌘ {label}: "
                                          + " && ".join(shlex.join(["brew", *c]) for c in cmds), "valid": True}
    return item


def pkg_url(kind, name):
    return f"https://formulae.brew.sh/{kind}/{name}"


def kind_flag(kind):
    return "--cask" if kind == "cask" else "--formula"


def error_item(msg, sub="Press ↩ to copy the error"):
    return {"title": msg, "subtitle": sub, "icon": icon("error"), "arg": action("copy", text=msg),
            "text": {"largetype": msg, "copy": msg}}


def fuzzy(query, *fields):
    q = query.lower().split()
    hay = " ".join(f for f in fields if f).lower()
    return all(w in hay for w in q)


def fmt_count(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


# ---------------------------------------------------------------- package rows

def package_item(p, installed_map, installs=None):
    """A package row shared by Search, Installed and Upgrades.

    ↩ primary action · ⌘↩ same in Terminal · ⌥↩ all actions · ⌃↩ homepage · ⇧ Quick Look
    """
    name, kind = p["name"], p["kind"]
    inst = installed_map.get((kind, name))
    flag = kind_flag(kind)
    ver = p.get("latest") or p.get("version") or ""
    bits = []
    if inst:
        state = "installed " + (inst["version"] or "")
        if inst["outdated"]:
            state += f" → {inst['latest']}"
        bits.append(state)
        if inst["pinned"]:
            bits.append("pinned")
    elif ver:
        bits.append(ver)
    if kind == "cask":
        bits.append("cask")
    if p.get("deprecated"):
        bits.append("⚠️ deprecated")
    if installs:
        bits.append(f"{fmt_count(installs)} installs/30d")
    desc = p.get("desc") or ""
    subtitle = " · ".join(bits) + (f" — {desc}" if desc else "")

    if not inst:
        cmds, label, done, ic = [["install", flag, name]], f"Installing {name}…", f"Installed {name}", icon(kind)
        verb = "Install"
    elif inst["outdated"] and inst["pinned"]:
        cmds = [["unpin", name] if kind == "formula" else ["unpin", "--cask", name], ["upgrade", flag, name]]
        label, done, ic, verb = f"Unpinning and upgrading {name}…", f"Upgraded {name}", icon("pinned"), "Unpin & upgrade"
    elif inst["outdated"]:
        cmds, label, done, ic = [["upgrade", flag, name]], f"Upgrading {name}…", f"Upgraded {name}", icon("outdated")
        verb = "Upgrade"
    else:
        cmds, verb = None, None
        ic = icon("pinned") if inst["pinned"] else icon("installed")
    if inst and kind == "cask" and inst.get("app"):
        ic = {"type": "fileicon", "path": inst["app"]}

    menu = action("menu", kind=kind, name=name)
    item = {
        "uid": f"{kind}:{name}", "title": name + (f"  ({p['display']})" if p.get("display") and p["display"].lower() != name.lower() else ""),
        "subtitle": subtitle, "icon": ic, "autocomplete": name, "match": f"{name} {desc}",
        "quicklookurl": pkg_url(kind, name),
        "text": {"copy": name, "largetype": f"{name}\n{desc}"},
        "arg": run_arg(cmds, label, done) if cmds else menu,
        "mods": {
            "alt": {"arg": menu, "subtitle": "⌥ Show all actions for " + name},
            "ctrl": {"arg": action("open", url=p.get("homepage") or pkg_url(kind, name)),
                     "subtitle": "⌃ Open homepage " + (p.get("homepage") or "")},
        },
    }
    if cmds:
        item["subtitle"] = f"↩ {verb} · " + item["subtitle"]
        with_terminal(item, cmds, verb + " in Terminal")
    else:
        item["mods"]["cmd"] = {"arg": menu, "subtitle": "Up to date — ↩ shows actions"}
    return item


def installed_map():
    c = Cached()
    try:
        rows = c.get("installed.json", LIST_TTL, produce_installed, deps=INSTALL_DEPS)
    except Exception:
        rows = []
    return {(r["kind"], r["name"]): r for r in rows}, c.stale


# ---------------------------------------------------------------- modes

def kw(var, default):
    return (os.environ.get(var) or default) + " "


COMMANDS = [
    ("Search", "Search formulae & casks and install them", "search", kw("kw_search", "brew")),
    ("Show Installed", "Installed formulae & casks, pinned first", "installed", kw("kw_installed", "brewi")),
    ("Show Upgrades", "Review outdated packages and upgrade them", "outdated", kw("kw_outdated", "brewu")),
    ("Show Vulnerabilities", "Installed formulae with known OSV advisories (brew vulns)", "vuln", kw("kw_vulns", "brewv")),
    ("Run Doctor", "Show the brew doctor report and its fixes", "doctor", kw("kw_doctor", "brewd")),
    ("Manage Services", "Start, stop & restart Homebrew services", "services", kw("kw_services", "brews")),
    ("Mac App Store", "Search the App Store, install and update apps with mas", "appstore", kw("kw_appstore", "brewm")),
]


def mode_search(query):
    query = query.strip()
    if not query:
        items = [{"title": t, "subtitle": s, "icon": icon(i), "valid": i != "search",
                  "autocomplete": "" if i == "search" else None, "arg": action("goto", query=k)}
                 for t, s, i, k in COMMANDS]
        items[0]["subtitle"] = "Type a formula or cask name to search"
        items += [
            {"title": "Upgrade All", "subtitle": "Upgrade every outdated, unpinned package now",
             "icon": icon("outdated"), "arg": action("upgrade_all")},
            {"title": "Update Homebrew", "subtitle": "brew update", "icon": icon("update"),
             "arg": run_arg([["update"]], "Updating Homebrew…", "Homebrew updated")},
            {"title": "Clean up", "subtitle": "Remove cache files and old versions older than 120 days",
             "icon": icon("cleanup"), "arg": run_arg([["cleanup", "--prune=120"]], "Cleaning up…", "Cleanup finished")},
            {"title": "Open Last Log", "subtitle": "Full output of the most recent background job",
             "icon": icon("info"), "arg": action("open_log"), "valid": os.path.exists(cache_path("last.log"))},
            {"title": "Clear Workflow Cache", "subtitle": "Clear the cached search index, installed and outdated lists",
             "icon": icon("cache"), "arg": action("clear_cache")},
        ]
        for it in items:
            if it.get("autocomplete") is None:
                it.pop("autocomplete", None)
        return emit(items)

    imap, stale = installed_map()
    c = Cached()
    index = c.get("index.json", INDEX_TTL, produce_index, sync_if_missing=False)
    if index is None:
        try:
            with open(cache_path("index.json.error")) as f:
                return emit([error_item("Couldn't build the search index", f.read().strip()[:200])])
        except OSError:
            pass
        return emit([{"title": "Building search index…", "subtitle": "Downloading the Homebrew catalogue (first run only, ~50 MB)",
                      "icon": icon("loading"), "valid": False}], rerun=1)

    q = query.lower()
    words = q.split()
    known = {(r[0], r[1]) for r in index}
    # Packages from third-party taps are not in the public API; search them from the installed list.
    taps = [[r["name"], r["kind"], r["desc"], r["latest"], r["homepage"], 0, r.get("display", ""), r["deprecated"]]
            for (k, n), r in imap.items() if (n, k) not in known]
    scored = []
    for row in index + taps:
        name, kind, desc, ver, home, n, extra = row[:7]
        dep = len(row) > 7 and row[7]
        ln = name.lower()
        if ln == q:
            rank = 0
        elif ln.startswith(q):
            rank = 1
        elif q in ln:
            rank = 2
        elif all(w in f"{ln} {extra.lower()}" for w in words):
            rank = 3
        elif all(w in f"{ln} {extra.lower()} {desc.lower()}" for w in words):
            rank = 4
        else:
            continue
        scored.append((rank, dep, -n, len(name), name, kind, desc, ver, home))
    scored.sort()
    items = []
    for rank, dep, negn, _, name, kind, desc, ver, home in scored[:MAX_RESULTS]:
        items.append(package_item({"name": name, "kind": kind, "desc": desc, "latest": ver, "homepage": home,
                                   "deprecated": dep}, imap, installs=-negn))
    if not items:
        items.append({"title": f"No formulae or casks match “{query}”", "icon": icon("search"),
                      "subtitle": "↩ Search on formulae.brew.sh",
                      "arg": action("open", url="https://formulae.brew.sh/?search=" + urllib.parse.quote(query))})
    emit(items, rerun=1 if (stale or c.stale) else None)


def mode_installed(query):
    c = Cached()
    try:
        rows = c.get("installed.json", LIST_TTL, produce_installed, deps=INSTALL_DEPS)
    except Exception as e:
        return emit([error_item(str(e))])
    imap = {(r["kind"], r["name"]): r for r in rows}
    hide_deps = os.environ.get("installed_hide_deps") == "1"
    order = lambda r: (not r["pinned"], r["kind"] != "formula", r["name"])
    items = []
    for r in sorted(rows, key=order):
        if hide_deps and not r["on_request"] and not r["pinned"]:
            continue
        if query and not fuzzy(query, r["name"], r.get("display"), r["desc"]):
            continue
        it = package_item(r, imap)
        section = ("Pinned " if r["pinned"] else "") + ("formula" if r["kind"] == "formula" else "cask")
        if r["kind"] == "formula" and not r["on_request"]:
            section += " (dependency)"
        it["subtitle"] = f"[{section}] " + it["subtitle"]
        items.append(it)
    if not items:
        items.append({"title": "Nothing installed matches", "icon": icon("info"), "valid": False})
    else:
        counts = f"{sum(r['kind'] == 'formula' for r in rows)} formulae, {sum(r['kind'] == 'cask' for r in rows)} casks"
        if not query:
            items.insert(0, {"title": f"{len(rows)} packages installed", "subtitle": counts + " · type to filter",
                             "icon": icon("icon"), "valid": False})
    emit(items, rerun=1 if c.stale else None)


def mode_outdated(query):
    c = Cached()
    try:
        rows = c.get("outdated.json", LIST_TTL, produce_outdated, deps=OUTDATED_DEPS)
    except Exception as e:
        return emit([error_item(str(e))])
    imap, _ = installed_map()
    unpinned = [r for r in rows if not r["pinned"]]
    items = []
    if not rows:
        items.append({"title": "Everything is up to date", "subtitle": "↩ Check again (brew update)",
                      "icon": icon("ok"), "arg": run_arg([["update"]], "Updating Homebrew…", "Homebrew updated")})
        return emit(items, rerun=1 if c.stale else None)
    if not query:
        pinned_n = len(rows) - len(unpinned)
        items.append(with_terminal({
            "title": f"Upgrade All ({len(unpinned)})" if unpinned else "All outdated packages are pinned",
            "subtitle": ("Exactly what a plain `brew upgrade` would do" +
                         (f" · {pinned_n} pinned skipped" if pinned_n else "")),
            "icon": icon("outdated"), "valid": bool(unpinned), "arg": action("upgrade_all"),
        }, [upgrade_all_args()]))
        items.append({"title": "Preview Upgrades", "subtitle": "brew upgrade --dry-run: versions, download sizes and warnings",
                      "icon": icon("preview"), "arg": action("menu", kind="preview", name="")})
    for r in rows:
        if query and not fuzzy(query, r["name"]):
            continue
        p = imap.get((r["kind"], r["name"])) or {"name": r["name"], "kind": r["kind"], "desc": "", "homepage": ""}
        m = {**p, "version": r["installed"], "latest": r["current"], "outdated": True, "pinned": r["pinned"]}
        it = package_item(m, {**imap, (r["kind"], r["name"]): m})
        it["subtitle"] = (("📌 pinned · " if r["pinned"] else "") + f"{r['installed']} → {r['current']}"
                          + (" · cask" if r["kind"] == "cask" else "") + (f" — {p['desc']}" if p.get("desc") else ""))
        it["mods"]["shift"] = {"arg": action("menu", kind="preview", name=r["name"], pkgkind=r["kind"]),
                               "subtitle": "⇧ Preview this upgrade (dry run)"}
        items.append(it)
    emit(items, rerun=1 if c.stale else None)


SEVERITY = {"CRITICAL": 4, "HIGH": 3, "MODERATE": 2, "MEDIUM": 2, "LOW": 1}


def mode_vulns(query):
    c = Cached()
    try:
        rows = c.get("vulns.json", VULNS_TTL, produce_vulns)
    except Exception as e:
        return emit([error_item(str(e), "brew vulns needs Homebrew 7")])
    imap, _ = installed_map()
    items = []
    for f in sorted(rows, key=lambda f: -max([SEVERITY.get(v.get("severity", "").upper(), 0) for v in f.get("vulnerabilities", [])] or [0])):
        vs = f.get("vulnerabilities") or []
        if not vs or (query and not fuzzy(query, f["formula"], *[v.get("summary") for v in vs])):
            continue
        top = max((v.get("severity") or "UNKNOWN" for v in vs), key=lambda s: SEVERITY.get(s.upper(), 0))
        inst = imap.get(("formula", f["formula"]))
        outdated = bool(inst and inst["outdated"])
        it = {
            "uid": "vuln:" + f["formula"], "title": f"{f['formula']} {f.get('version', '')}",
            "subtitle": f"{len(vs)} advisor{'y' if len(vs) == 1 else 'ies'} · highest {top.lower()}"
                        + (" · upgrade available" if outdated else "") + f" — {vs[0].get('summary', '')}",
            "icon": icon("vuln"), "arg": action("menu", kind="vuln", name=f["formula"]),
            "text": {"largetype": "\n".join(f"{v['id']} [{v.get('severity')}] {v.get('summary', '')}" for v in vs)},
        }
        if outdated:
            with_terminal(it, [["upgrade", "--formula", f["formula"]]], "Upgrade in Terminal")
        items.append(it)
    if not items:
        items.append({"title": "No known vulnerabilities", "subtitle": "brew vulns found no advisories for installed formulae",
                      "icon": icon("ok"), "valid": False})
    emit(items, rerun=1 if c.stale else None)


FIX_RE = re.compile(r"^\s{2,}((?:sudo |brew |echo |export |xcode-select |softwareupdate |rm |chown |chmod |mkdir |ln |git |"
                    r"eval |/bin/|launchctl ).+)$")


# Warnings brew explains in prose instead of printing a command.
KNOWN_FIXES = [("Broken symlinks were found", "brew cleanup --prune-prefix")]


def parse_doctor(text):
    warnings = []
    for block in re.split(r"\n(?=(?:Warning|Error): )", "\n" + text):
        block = block.strip()
        if not block.startswith(("Warning:", "Error:")):
            continue
        lines = block.splitlines()
        title = re.sub(r"^(Warning|Error):\s*", "", lines[0])
        body = [l for l in lines[1:]]
        fixes = [m.group(1).strip() for l in body if (m := FIX_RE.match(l))]
        fixes += [fix for pat, fix in KNOWN_FIXES if pat in title and fix not in fixes]
        warnings.append({"title": title, "body": "\n".join(body).strip(), "fixes": fixes, "text": block})
    return warnings


def produce_doctor():
    """Run doctor in the user's login shell so PATH warnings match what they see in Terminal."""
    shell = os.environ.get("SHELL") or "/bin/zsh"
    try:
        p = subprocess.run([shell, "-l", "-i", "-c", shlex.join([BREW, "doctor"])], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=180, env={**brew_env(), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"})
    except (OSError, subprocess.TimeoutExpired):
        p = run_brew("doctor")
    return parse_doctor(p.stdout + "\n" + p.stderr)


def mode_doctor(query):
    # Script Filters rerun on every keystroke; doctor takes seconds, so cache it briefly.
    warnings = Cached().get("doctor.json", 300, produce_doctor)
    items = []
    if not warnings:
        return emit([{"title": "Your system is ready to brew.", "subtitle": "brew doctor found no problems",
                      "icon": icon("ok"), "valid": False}])
    fixes = list(dict.fromkeys(f for w in warnings for f in w["fixes"]))
    if fixes and not query:
        items.append({"title": f"Fix All ({len(fixes)} command{'s' if len(fixes) != 1 else ''})",
                      "subtitle": "Runs every fix in Terminal · lines that append to ~/.zshrc add a line each time",
                      "icon": icon("fix"), "arg": action("terminal", cmd="\n".join(fixes)),
                      "text": {"largetype": "\n".join(fixes), "copy": "\n".join(fixes)}})
    for w in warnings:
        if query and not fuzzy(query, w["title"], w["body"]):
            continue
        first_body = next((l.strip() for l in w["body"].splitlines() if l.strip()), "")
        it = {"title": w["title"], "icon": icon("warning"),
              "subtitle": (f"↩ Run fix: {w['fixes'][0]}" if w["fixes"] else "⌘L to read · ↩ copy") + f" — {first_body}",
              "text": {"largetype": w["text"], "copy": w["text"]},
              "arg": action("terminal", cmd="\n".join(w["fixes"])) if w["fixes"] else action("copy", text=w["text"])}
        items.append(it)
    items.append({"title": "Re-run Doctor", "icon": icon("doctor"), "arg": action("rerun_doctor"),
                  "subtitle": f"{len(warnings)} warning{'s' if len(warnings) != 1 else ''}"})
    emit(items)


def mode_services(query):
    c = Cached()
    try:
        rows = c.get("services.json", 30, produce_services)
    except Exception as e:
        return emit([error_item(str(e))])
    items = []
    for s in rows:
        name, status = s["name"], s.get("status") or "none"
        if query and not fuzzy(query, name, status):
            continue
        running = status in ("started", "scheduled", "other")
        ic = icon("running" if running else "error" if status == "error" else "stopped")
        verb = "stop" if running else "restart" if status == "error" else "start"
        root = s.get("user") == "root"
        it = {"uid": "svc:" + name, "title": name, "icon": ic,
              "subtitle": f"{status}" + (f" as {s['user']}" if s.get("user") else "")
                          + (f" · exit {s['exit_code']}" if s.get("exit_code") not in (None, 0) else "")
                          + f" — ↩ {verb.title()}" + (" (sudo, in Terminal)" if root else "") + " · ⌥ Restart",
              "arg": (action("terminal", cmd=f"sudo {shlex.quote(BREW)} services {verb} {shlex.quote(name)}") if root else
                      run_arg([["services", verb, name]], f"{verb.rstrip('p').title()}ing {name}…".replace("Stoping", "Stopping"),
                              {"stop": f"{name} stopped", "start": f"{name} started", "restart": f"{name} restarted"}[verb])),
              "mods": {"alt": {"arg": run_arg([["services", "restart", name]], f"Restarting {name}…", f"{name} restarted"),
                               "subtitle": f"⌥ Restart {name}"}}}
        if s.get("file"):
            it["quicklookurl"] = s["file"]
        with_terminal(it, [["services", verb, name]], verb.title() + " in Terminal")
        items.append(it)
    if not items:
        items.append({"title": "No Homebrew services", "icon": icon("services"), "valid": False})
    emit(items, rerun=1 if c.stale else None)


# ---------------------------------------------------------------- Mac App Store

def find_mas():
    for p in ("/opt/homebrew/bin/mas", "/usr/local/bin/mas"):
        if os.access(p, os.X_OK):
            return p
    return None


MAS = find_mas()
MAS_LINE = re.compile(r"^\s*(\d+)\s+(.+?)\s+\((.+?)(?:\s*->\s*(.+?))?\)\s*$")


def store_country():
    """The storefront to search: the preference, else the Mac's region (en_US@rg=pkzzzz -> pk)."""
    c = (os.environ.get("appstore_country") or "").strip().lower()
    if c:
        return c
    loc = subprocess.run(["defaults", "read", "NSGlobalDomain", "AppleLocale"], capture_output=True, text=True).stdout
    m = re.search(r"rg=([a-z]{2})", loc) or re.search(r"_([A-Za-z]{2})", loc)
    return m.group(1).lower() if m else "us"


def parse_mas(text):
    apps = {}
    for line in text.splitlines():
        m = MAS_LINE.match(line)
        if m:
            apps[m.group(1)] = {"name": m.group(2), "version": m.group(3), "latest": m.group(4)}
    return apps


def produce_mas_installed():
    return parse_mas(subprocess.run([MAS, "list"], capture_output=True, text=True).stdout) if MAS else {}


def produce_mas_outdated():
    return parse_mas(subprocess.run([MAS, "outdated"], capture_output=True, text=True).stdout) if MAS else {}


def itunes_search(query, country):
    key = "appstore-" + re.sub(r"[^a-z0-9]+", "-", f"{country}-{query.lower()}")[:80] + ".json"
    if (age(key) or 1e9) < 3600:
        return read_json(key)
    url = ("https://itunes.apple.com/search?" +
           urllib.parse.urlencode({"entity": "macSoftware", "term": query, "country": country, "limit": 30}))
    data = fetch_json(url).get("results", [])
    write_json(key, data)
    return data


def artwork(results):
    """Alfred icons must be local files: fetch the 60px artwork once per app, in parallel."""
    folder = cache_path("artwork")
    os.makedirs(folder, exist_ok=True)
    procs = []
    for r in results:
        path = os.path.join(folder, f"{r['trackId']}.jpg")
        r["_icon"] = path
        if not os.path.exists(path) and r.get("artworkUrl60"):
            procs.append(subprocess.Popen(["/usr/bin/curl", "-fsS", "--max-time", "4", "-o", path, r["artworkUrl60"]],
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    for p in procs:
        p.wait()


def store_links(track_id):
    return (f"macappstore://apps.apple.com/app/id{track_id}", f"https://apps.apple.com/app/id{track_id}")


def app_item(track_id, name, sub_bits, desc, installed, outdated, price, bundle, icon_path=None, web=None):
    store, web = store_links(track_id)[0], web or store_links(track_id)[1]
    mas = shlex.quote(MAS) if MAS else None
    free = price in (None, 0, 0.0)
    if outdated and mas:
        verb, arg = "Update in Terminal (sudo)", action("terminal", cmd=f"sudo {mas} update {track_id}")
    elif installed:
        verb = "Open app"
        arg = action("open_app", bundle=bundle, name=name) if bundle else action("open", url=store)
    elif mas and free:
        verb, arg = "Get in Terminal (sudo)", action("terminal", cmd=f"sudo {mas} get {track_id}")
    else:
        verb, arg = "Open in App Store", action("open", url=store)
    subtitle = f"↩ {verb} · " + " · ".join(b for b in sub_bits if b) + (f" — {desc}" if desc else "")
    ic = {"path": icon_path} if icon_path and os.path.exists(icon_path) else \
        icon("installed" if installed else "appstore" if free else "paid")
    mods = {
        "cmd": {"arg": action("open", url=store), "subtitle": "⌘ Open in the App Store"},
        "alt": {"arg": action("open", url=web), "subtitle": "⌥ Open the App Store page in your browser"},
        "ctrl": {"arg": action("copy", text=web), "subtitle": "⌃ Copy App Store link"},
    }
    if installed and mas:
        mods["shift"] = {"arg": action("terminal", cmd=f"sudo {mas} uninstall {track_id}"),
                         "subtitle": f"⇧ Uninstall {name} in Terminal (sudo)"}
    elif not installed and mas and not free:
        mods["shift"] = {"arg": action("terminal", cmd=f"sudo {mas} install {track_id}"),
                         "subtitle": "⇧ Install in Terminal if you already bought it (sudo mas install)"}
    return {"uid": f"mas:{track_id}", "title": name, "subtitle": subtitle, "icon": ic, "arg": arg, "mods": mods,
            "quicklookurl": web, "text": {"copy": web, "largetype": f"{name}\n{desc}"}}


def mode_appstore(query):
    query = query.strip()
    c = Cached()
    apps_dir_deps = ["/Applications"]
    installed = c.get("mas-installed.json", LIST_TTL, produce_mas_installed, deps=apps_dir_deps)
    outdated = c.get("mas-outdated.json", 30 * 60, produce_mas_outdated, deps=apps_dir_deps)
    hint = None if MAS else {"title": "Install mas to get and update apps from here", "icon": icon("info"),
                              "subtitle": "↩ Search Homebrew for mas · without it, apps open in the App Store",
                              "arg": action("goto", query=kw("kw_search", "brew") + "mas")}
    if not query:
        items = []
        if outdated:
            items.append({"title": f"Update All App Store Apps ({len(outdated)})", "icon": icon("outdated"),
                          "subtitle": "sudo mas update — runs in Terminal",
                          "arg": action("terminal", cmd=f"sudo {shlex.quote(MAS)} update")})
        rows = sorted(installed.items(), key=lambda kv: (kv[0] not in outdated, kv[1]["name"].lower()))
        for tid, a in rows:
            o = outdated.get(tid)
            bits = [f"{a['version']} → {o['latest']}" if o and o.get("latest") else f"installed {a['version']}"]
            it = app_item(tid, a["name"], bits, "", True, bool(o), None, None)
            path = f"/Applications/{a['name']}.app"
            if os.path.exists(path):
                it["icon"] = {"type": "fileicon", "path": path}
                if not o:
                    it["arg"] = action("open", url=path)
            items.append(it)
        if not items:
            items.append({"title": "Search the Mac App Store", "subtitle": "Type an app name",
                          "icon": icon("appstore"), "valid": False})
        return emit(([hint] if hint else []) + items, rerun=1 if c.stale else None)

    country = store_country()
    try:
        results = itunes_search(query, country)
    except Exception as e:
        return emit([error_item(f"App Store search failed: {e}")])
    artwork(results)
    items = []
    for r in results:
        tid = str(r["trackId"])
        inst, o = installed.get(tid), outdated.get(tid)
        rating = r.get("averageUserRating")
        bits = [
            (f"installed {inst['version']}" + (f" → {o['latest']}" if o and o.get("latest") else "")) if inst else r.get("version"),
            r.get("formattedPrice") or ("Free" if not r.get("price") else None),
            f"★ {rating:.1f} ({fmt_count(r.get('userRatingCount') or 0)})" if rating else None,
            r.get("sellerName"),
        ]
        desc = " ".join((r.get("description") or "").split())[:140]
        items.append(app_item(tid, r.get("trackName", tid), bits, desc, bool(inst), bool(o), r.get("price"),
                              r.get("bundleId"), r.get("_icon"), (r.get("trackViewUrl") or "").split("?")[0] or None))
    if not items:
        items.append({"title": f"No App Store apps match “{query}”", "subtitle": f"Storefront: {country.upper()}",
                      "icon": icon("appstore"), "valid": False})
    emit(([hint] if hint else []) + items, rerun=1 if c.stale else None)


def mode_menu(query):
    """Second-level list: every action for one package, a vuln's advisories, or a dry-run preview."""
    try:
        m = json.loads(os.environ.get("menu_arg") or "{}")
    except ValueError:
        m = {}
    kind, name = m.get("kind", ""), m.get("name", "")
    if not kind:
        return emit([{"title": "Pick a package first", "subtitle": "Open this list with ⌥↩ on a package",
                      "icon": icon("info"), "valid": False}])
    if kind == "preview":
        return menu_preview(name, m.get("pkgkind", ""), query)
    if kind == "vuln":
        return menu_vuln(name, query)
    imap, _ = installed_map()
    inst = imap.get((kind, name))
    flag = kind_flag(kind)
    rows = []

    def add(title, sub, ic, cmds=None, label=None, done=None, arg=None, terminal=True):
        it = {"title": title, "subtitle": sub, "icon": icon(ic), "arg": arg or run_arg(cmds, label, done)}
        if cmds and terminal:
            with_terminal(it, cmds)
        rows.append(it)

    if not inst:
        add(f"Install {name}", f"brew install {flag} {name}", "formula" if kind == "formula" else "cask",
            [["install", flag, name]], f"Installing {name}…", f"Installed {name}")
    else:
        if inst["outdated"]:
            if inst["pinned"]:
                add(f"Unpin & Upgrade {name}", f"{inst['version']} → {inst['latest']} (pinned: unpins first)", "outdated",
                    [["unpin", name] if kind == "formula" else ["unpin", "--cask", name], ["upgrade", flag, name]],
                    f"Upgrading {name}…", f"Upgraded {name}")
            else:
                add(f"Upgrade {name}", f"{inst['version']} → {inst['latest']}", "outdated",
                    [["upgrade", flag, name]], f"Upgrading {name}…", f"Upgraded {name}")
            add("Preview Upgrade", "brew upgrade --dry-run " + name, "preview",
                arg=action("menu", kind="preview", name=name, pkgkind=kind))
        pin_cmd = ["pin", name] if kind == "formula" else ["pin", "--cask", name]
        unpin_cmd = ["unpin", name] if kind == "formula" else ["unpin", "--cask", name]
        if inst["pinned"]:
            add(f"Unpin {name}", "Allow brew to upgrade it again", "pinned", [unpin_cmd], f"Unpinning {name}…", f"Unpinned {name}")
        else:
            add(f"Pin {name}", "Lock the current version: brew won't upgrade or uninstall it", "pinned",
                [pin_cmd], f"Pinning {name}…", f"Pinned {name}")
        add(f"Reinstall {name}", f"brew reinstall {flag} {name}", "update",
            [["reinstall", flag, name]], f"Reinstalling {name}…", f"Reinstalled {name}")
        if inst["pinned"]:
            add(f"Uninstall {name}", "Pinned: unpins first", "uninstall",
                [unpin_cmd, ["uninstall", flag, name]], f"Uninstalling {name}…", f"Uninstalled {name}")
        else:
            add(f"Uninstall {name}", f"brew uninstall {flag} {name}", "uninstall",
                [["uninstall", flag, name]], f"Uninstalling {name}…", f"Uninstalled {name}")
        if kind == "cask":
            add(f"Uninstall {name} and Zap", "Also remove its preferences and caches (brew uninstall --zap)", "uninstall",
                [["uninstall", "--zap", "--cask", name]], f"Zapping {name}…", f"Uninstalled {name}")
    add("Show Info", f"brew info {flag} {name} in Terminal", "info",
        arg=action("terminal", cmd=shlex.join(["brew", "info", flag, name])), terminal=False)
    home = (inst or {}).get("homepage") or next(
        (r[4] for r in read_json("index.json") or [] if r[0] == name and r[1] == kind), "")
    add("Open on formulae.brew.sh", "Details, dependencies and install statistics (30/90/365 days)", "link",
        arg=action("open", url=pkg_url(kind, name)), terminal=False)
    add("Open Homepage", home or "Project homepage", "link",
        arg=action("open", url=home or pkg_url(kind, name)) if home else action("homepage", kind=kind, name=name), terminal=False)
    add("Copy Install Command", f"brew install {flag} {name}", "terminal",
        arg=action("copy", text=f"brew install {flag} {name}"), terminal=False)
    for r in rows:
        r["quicklookurl"] = pkg_url(kind, name)
    emit([r for r in rows if not query or fuzzy(query, r["title"], r["subtitle"])])


def menu_vuln(name, query):
    try:
        findings = Cached().get("vulns.json", VULNS_TTL, produce_vulns)
    except Exception as e:
        return emit([error_item(str(e))])
    f = next((x for x in findings if x.get("formula") == name), None)
    if not f:
        return emit([error_item(f"No vulnerability data for {name}")])
    imap, _ = installed_map()
    inst = imap.get(("formula", name))
    items = []
    if inst and inst["outdated"]:
        items.append(with_terminal({"title": f"Upgrade {name}", "subtitle": f"{inst['version']} → {inst['latest']}",
                                    "icon": icon("outdated"),
                                    "arg": run_arg([["upgrade", "--formula", name]], f"Upgrading {name}…", f"Upgraded {name}")},
                                   [["upgrade", "--formula", name]]))
    for v in f.get("vulnerabilities") or []:
        if query and not fuzzy(query, v["id"], v.get("summary"), *(v.get("aliases") or [])):
            continue
        fixed = ", ".join(x[:12] for x in v.get("fixed_versions") or []) or "no fix released"
        aliases = " ".join(v.get("aliases") or [])
        items.append({"title": f"{v['id']}  [{(v.get('severity') or 'unknown').lower()}]",
                      "subtitle": f"{v.get('summary', '')} · fixed in: {fixed}" + (f" · {aliases}" if aliases else ""),
                      "icon": icon("vuln"), "quicklookurl": f"https://osv.dev/vulnerability/{v['id']}",
                      "arg": action("open", url=f"https://osv.dev/vulnerability/{v['id']}"),
                      "text": {"largetype": f"{v['id']}\n{v.get('summary', '')}\nFixed in: {fixed}\n{aliases}", "copy": v["id"]}})
    emit(items)


DRY_RE = re.compile(r"^(\S+)\s+(\S+)\s+->\s+(\S+)(?:\s+\(([\d.]+)\s*([KMGT]?B)\))?\s*$")
UNITS = {"B": 1, "KB": 1e3, "MB": 1e6, "GB": 1e9, "TB": 1e12}


def human(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1000:
            return f"{n:.1f}{u}" if u != "B" else f"{int(n)}B"
        n /= 1000
    return f"{n:.1f}TB"


def menu_preview(name, pkgkind, query):
    args = ["upgrade", "--dry-run"] + ([kind_flag(pkgkind), name] if name else (["--greedy"] if GREEDY else []))
    key = "preview.json"
    cached = read_json(key)
    if cached and cached.get("args") == args and (age(key) or 1e9) < 120:
        out = cached["out"]
    else:
        p = run_brew(*args)
        out = p.stdout + "\n" + p.stderr
        write_json(key, {"args": args, "out": out})
    items, total, section = [], 0.0, ""
    warnings = []
    for line in out.splitlines():
        if line.startswith("==> "):
            section = line[4:]
            continue
        if line.startswith(("Warning:", "Error:")):
            warnings.append(line)
            continue
        m = DRY_RE.match(line.strip())
        if m:
            pkg, old, new, size, unit = m.groups()
            size_b = float(size) * UNITS[unit] if size else 0
            total += size_b
            items.append({"title": f"{pkg}   {old} → {new}",
                          "subtitle": (f"{human(size_b)} · running total {human(total)}" if size else "size unknown")
                                      + (f" · {section}" if section else ""),
                          "icon": icon("outdated"), "valid": False})
        elif section.startswith("Would install") and line.strip() and not line.startswith(" "):
            parts = line.split()
            if len(parts) >= 2 and not line.startswith(("Some ", "`", "Hide ")):
                items.append({"title": f"{parts[0]}   new dependency {parts[1]}", "subtitle": section,
                              "icon": icon("formula"), "valid": False})
    head = []
    if items:
        upgrades = sum(1 for i in items if "→" in i["title"])
        cmds = [["upgrade", kind_flag(pkgkind), name]] if name else [upgrade_all_args()]
        head.append(with_terminal({"title": f"Upgrade {name}" if name else f"Upgrade All ({upgrades})",
                                   "subtitle": f"Total download ≈ {human(total)}",
                                   "icon": icon("outdated"),
                                   "arg": run_arg(cmds, f"Upgrading {name}…", f"Upgraded {name}") if name else action("upgrade_all")},
                                  cmds))
    else:
        head.append({"title": "Nothing would be upgraded", "subtitle": (out.strip().splitlines() or ["Everything is up to date"])[-1],
                     "icon": icon("ok"), "valid": False, "text": {"largetype": out}})
    for w in warnings:
        head.append({"title": w, "icon": icon("warning"), "valid": False, "text": {"largetype": w}})
    emit(head + [i for i in items if not query or fuzzy(query, i["title"])])


# ---------------------------------------------------------------- dispatch

def applescript_str(text):
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def alfred_trigger(trigger, argument=""):
    script = (f'tell application id "com.runningwithcrayons.Alfred" to run trigger "{trigger}" '
              f'in workflow "{BUNDLE_ID}" with argument {applescript_str(argument)}')
    subprocess.run(["osascript", "-e", script], capture_output=True)


def alfred_search(query):
    subprocess.run(["osascript", "-e", f'tell application id "com.runningwithcrayons.Alfred" to search {applescript_str(query)}'],
                   capture_output=True)


def notify(msg):
    alfred_trigger("notify", msg)


def upgrade_all_args():
    return ["upgrade", "--greedy"] if GREEDY else ["upgrade"]


class JobLock:
    """brew refuses to run two package operations at once, so background jobs queue up."""

    def __init__(self, label):
        self.label = label

    def __enter__(self):
        self.f = open(cache_path("job.lock"), "w")
        try:
            fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            notify("Queued: " + self.label)
            fcntl.flock(self.f, fcntl.LOCK_EX)
        self.log = open(cache_path("last.log"), "w")
        return self

    def __exit__(self, *exc):
        self.log.close()
        self.f.close()
        invalidate()


NEEDS_TTY = re.compile(r"sudo|a terminal is required|password|askpass", re.I)


def run_logged(job, cmd):
    job.log.write(f"$ brew {shlex.join(cmd)}\n")
    job.log.flush()
    p = subprocess.run([BREW, *cmd], stdout=job.log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                       text=True, env=brew_env())
    return p.returncode


def last_log_lines():
    try:
        with open(cache_path("last.log")) as f:
            return [l.rstrip() for l in f if l.strip()]
    except OSError:
        return []


def failure_message(lines):
    errs = [l for l in lines if l.startswith("Error:")]
    return (errs or lines or ["unknown error"])[-1]


def do_run(a):
    with JobLock(a["label"]) as job:
        notify(a["label"])
        for i, cmd in enumerate(a["cmds"]):
            if run_logged(job, cmd) != 0:
                job.log.flush()
                lines = last_log_lines()
                if any(NEEDS_TTY.search(l) for l in lines[-15:]):
                    rest = " && ".join(shlex.join(["brew", *c]) for c in a["cmds"][i:])
                    alfred_trigger("terminal", rest)
                    return "Needs your password — continuing in Terminal"
                return "Failed: " + failure_message(lines) + " (brew ▸ Open Last Log)"
        job.log.flush()
        if a["cmds"][-1][:1] == ["install"] and any(l.startswith("==> Caveats") for l in last_log_lines()):
            return a["done"] + " — has caveats (brew ▸ Open Last Log)"
    return a["done"]


UPGRADING_RE = re.compile(r"^==> Upgrading (?!\d+ outdated package)(\S+)\s*$")


def do_upgrade_all():
    """Upgrade everything outdated, reporting progress per package."""
    with JobLock("Upgrade All") as job:
        notify("Upgrading all outdated packages…")
        cmd = upgrade_all_args()
        job.log.write(f"$ brew {shlex.join(cmd)}\n")
        p = subprocess.Popen([BREW, *cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             text=True, env=brew_env())
        done, tail = [], []
        for line in p.stdout:
            job.log.write(line)
            tail = (tail + [line.rstrip()])[-40:]
            m = UPGRADING_RE.match(line)
            if m:
                done.append(m.group(1))
                notify(f"Upgrading {m.group(1)}…")
        p.wait()
    if p.returncode != 0:
        if any(NEEDS_TTY.search(l) for l in tail):
            alfred_trigger("terminal", shlex.join(["brew", *cmd]))
            return "Some upgrades need your password — continuing in Terminal"
        return f"Upgraded {len(done)}, then failed: " + failure_message(tail) + " (brew ▸ Open Last Log)"
    return f"Upgraded {len(done)} package{'s' if len(done) != 1 else ''}" if done else "Everything was already up to date"


def dispatch(raw):
    try:
        a = json.loads(raw)
    except ValueError:
        return
    kind = a.get("a")
    if kind == "run":
        print(do_run(a))
    elif kind == "upgrade_all":
        print(do_upgrade_all())
    elif kind == "terminal":
        # Caches notice the change themselves (Cellar/Caskroom mtimes) once the command finishes.
        alfred_trigger("terminal", a["cmd"])
    elif kind == "menu":
        alfred_trigger("menu", json.dumps({k: a.get(k, "") for k in ("kind", "name", "pkgkind")}))
    elif kind == "goto":
        alfred_search(a["query"])
    elif kind == "rerun_doctor":
        try:
            os.remove(cache_path("doctor.json"))
        except OSError:
            pass
        alfred_search(kw("kw_doctor", "brewd"))
    elif kind == "open":
        subprocess.run(["open", a["url"]])
    elif kind == "open_log":
        subprocess.run(["open", "-t", cache_path("last.log")])
    elif kind == "open_app":
        if subprocess.run(["open", "-b", a["bundle"]]).returncode != 0:
            print(f"Couldn't open {a.get('name') or a['bundle']}")
    elif kind == "homepage":
        subprocess.run(["open", pkg_url(a["kind"], a["name"])])
    elif kind == "copy":
        subprocess.run(["pbcopy"], input=a["text"], text=True)
        print("Copied to clipboard")
    elif kind == "clear_cache":
        for n in os.listdir(CACHE_DIR):
            if n.endswith((".json", ".error", ".tmp", ".log")):
                try:
                    os.remove(cache_path(n))
                except OSError:
                    pass
        print("Workflow cache cleared")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "search"
    arg = sys.argv[2] if len(sys.argv) > 2 else ""
    if mode == "refresh":
        try:
            write_json(arg, PRODUCERS[arg]())
            if os.path.exists(cache_path(arg + ".error")):
                os.remove(cache_path(arg + ".error"))
        except Exception as e:
            with open(cache_path(arg + ".error"), "w") as f:
                f.write(str(e))
        finally:
            try:
                os.remove(cache_path(arg + ".lock"))
            except OSError:
                pass
        return
    if mode == "dispatch":
        return dispatch(arg)
    modes = {"search": mode_search, "installed": mode_installed, "outdated": mode_outdated, "vulns": mode_vulns,
             "doctor": mode_doctor, "services": mode_services, "menu": mode_menu, "appstore": mode_appstore}
    try:
        modes[mode](arg)
    except Exception as e:
        emit([error_item(f"{type(e).__name__}: {e}")])


if __name__ == "__main__":
    main()
