# Changelog

## 1.2.0

- **Mac App Store search** (`brewm`): price, rating, developer, artwork and installed state, using Apple's public search API. With `mas` installed, get free apps, update, uninstall, or install purchased apps via `sudo mas` in Terminal. An empty query lists your App Store apps and pending updates.

## 1.1.0

- Background jobs queue instead of failing when Homebrew is busy.
- Jobs that need your password continue in Terminal.
- Open Last Log shows the full output of the last job.
- Lists refresh on their own when packages change outside Alfred.
- Doctor runs in your login shell, so it shows the same warnings as Terminal. Fix All no longer repeats commands, and broken symlinks get a fix.
- Doctor and Preview Upgrades are cached instead of re-running brew on every keystroke.
- Search includes installed packages from third-party taps and marks deprecated packages.
- New setting: include auto-updating casks in upgrades (`--greedy`).
- Fixed: Upgrade All counted brew's summary line as a package; renamed keywords weren't used by menu shortcuts; garbled notification text; services in the error state; advisories missing after an action; a failed refresh retrying every second.

## 1.0.0

- First version: search, installed, upgrades with dry-run preview, Upgrade All, vulnerabilities, doctor, services, clean up.
