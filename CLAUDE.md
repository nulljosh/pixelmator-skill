# pixelmator-skill

Claude skill that builds logos live in Pixelmator Pro. `SKILL.md` is the usage doc. `WHITEPAPER.md` is the design.

- One file of logic: `pxm.py`. Stdlib only. Must stay Python 3.9 compatible (that is what macOS ships).
- `~/.claude/skills/pixelmator` is a symlink to this repo.
- Before pushing: `uvx ruff check .` then `python3 -m unittest`. For app changes also `PXM_LIVE=1 python3 -m unittest`.
- The dictionary is the truth: `sdef "/Applications/Pixelmator Pro.app"`. Probe the real app before trusting a guess. Unknown fonts fail silently. Shape geometry is read-only after `make`. A new document has one blank layer that can only be deleted once another exists.
- Never script System Events.
