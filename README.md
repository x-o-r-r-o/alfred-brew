# <img src="src/icon.png" width="48" align="center"> Brew

Search, install and manage Homebrew formulae and casks from Alfred, and search the Mac App Store. A port of the Raycast [Brew](https://www.raycast.com/nhojb/brew) extension to [Alfred 5](https://www.alfredapp.com) with the Powerpack.

## Install

Download `Brew.alfredworkflow` from the [latest release](https://github.com/x-o-r-r-o/alfred-brew/releases/latest) and double-click it.

## Setup

* [Homebrew](https://brew.sh). Brew finds it in `/opt/homebrew` or `/usr/local`, or you can set the path in the Workflow's Configuration.
* Python 3, which comes with Apple's Command Line Tools (`xcode-select --install`). Homebrew already needs these.
* Optional: [mas](https://github.com/mas-cli/mas) (`brew install mas`) to get and update App Store apps from Alfred. Without it, App Store results open in the App Store.

## Usage

Every keyword can be changed in the [Workflow's Configuration](https://www.alfredapp.com/help/workflows/user-configuration/).

| Keyword | Does |
|---|---|
| `brew <query>` | Search formulae and casks, with 30-day install counts, ranked by popularity. Installed packages from third-party taps are included. `brew` on its own lists every command, plus Upgrade All, Update Homebrew, Clean up, Open Last Log and Clear Workflow Cache |
| `brewi` | Installed packages, pinned first |
| `brewu` | Outdated packages, Upgrade All (skips pinned ones, like `brew upgrade`) and Preview Upgrades: a dry run with versions, download sizes and a running total |
| `brewv` | Installed formulae with known OSV advisories, from `brew vulns` (Homebrew 7) |
| `brewd` | The `brew doctor` report. Each fix runs in Terminal, or all at once with Fix All |
| `brews` | Start, stop and restart Homebrew services. Root services use `sudo` in Terminal |
| `brewm <query>` | Search the Mac App Store: price, rating, developer, installed and update state. An empty query lists your App Store apps and pending updates |

### Keys on a package

* <kbd>↩</kbd> The main action: install, upgrade, or unpin and upgrade. When the package is up to date, show all actions.
* <kbd>⌘</kbd><kbd>↩</kbd> Run the main action in your Terminal.
* <kbd>⌥</kbd><kbd>↩</kbd> All actions: pin, unpin, reinstall, uninstall, zap, preview upgrade, info, homepage.
* <kbd>⌃</kbd><kbd>↩</kbd> Open the homepage.
* <kbd>⇧</kbd> Quick Look the formulae.brew.sh page, with install statistics. <kbd>⌘</kbd><kbd>C</kbd> Copy the name.

### Keys on an App Store app

* <kbd>↩</kbd> Open an installed app, update an outdated one, get a free one, or open a paid one in the App Store.
* <kbd>⌘</kbd><kbd>↩</kbd> Open in the App Store. <kbd>⌥</kbd><kbd>↩</kbd> Open the web page. <kbd>⌃</kbd><kbd>↩</kbd> Copy the link.
* <kbd>⇧</kbd><kbd>↩</kbd> Uninstall an installed app, or install a paid app you've already bought.

`mas` needs root to get, install, update and uninstall apps, so those run as `sudo mas …` in Terminal. Search uses your Mac's region; set another country in the Configuration.

### Background jobs

Installs and upgrades run in the background and report progress in notifications. Jobs queue up, because Homebrew can't run two at once. A job that needs your password continues in Terminal. The full output of the last job is under `brew` ▸ **Open Last Log**.

## Configuration

* Keywords for every command.
* Hide formulae installed only as dependencies in `brewi`.
* Turn off concurrent downloads.
* Include auto-updating casks in upgrades (`--greedy`).
* App Store country and `brew` path.

## Not included

Raycast's Services menu bar item (Alfred has no menu bar) and choosing several packages to upgrade at once (Alfred has no multi-select).

## Build

```bash
python3 tools/build.py
```

This writes `dist/Brew.alfredworkflow`. `swift tools/make_icons.swift src/icons` regenerates the icons.

## License

[MIT](LICENSE)
