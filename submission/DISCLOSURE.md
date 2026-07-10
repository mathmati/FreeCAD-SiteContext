PREPARED DRAFT — awaiting user review and sign-off before any external submission.

# AI-Assistance Disclosure — FreeCAD SiteContext addon

This document is the standing AI-assistance disclosure for the FreeCAD
SiteContext ("Add Location…") addon, written to satisfy FreeCAD's
[`AI_POLICY.md`](https://github.com/FreeCAD/FreeCAD/blob/main/AI_POLICY.md)
(effective June 29, 2026). It is meant to be:

- linked from the addon's `README.md` (already done),
- pasted, adapted, into the description of any PR or Issue this addon's
  maintainer opens against FreeCAD-adjacent repositories, and
- kept accurate — update it if the tooling or process changes.

## Natural-language disclosure (paste into PR/issue descriptions)

> This contribution was drafted with the assistance of an AI coding
> assistant (Claude, by Anthropic). All code, data, documentation, and
> copy were reviewed and are fully understood by the named human
> maintainer below, who takes personal responsibility for this
> contribution and personally handles all reviewer communication on it —
> no AI-generated replies are posted under this account. This complies
> with FreeCAD's AI_POLICY.md disclosure requirement.

## Git trailer (append to every prepared commit)

```
Assisted-by: Claude (Anthropic, Sonnet 5)
```

Adjust the model identifier to match whichever Claude model actually
produced the change, per `AI_POLICY.md`'s
`Assisted-by: [Model-Family] ([Version/ID])` format.

## PR-template checkbox

FreeCAD's PR template requires checking:

> "This PR is not unverified AI output, I take responsibility for it, and
> all communication from my side in this PR is done by me personally."

Do not check this box, and do not open the PR, until
mathmati has personally reviewed the diff, tested it
against a live FreeCAD install, and is prepared to answer reviewer
questions personally (not by relaying an AI's answers verbatim).

## What "human-reviewed" needs to mean in practice for this addon

This session performed (and this disclosure records honestly what was and
was not done):

- **Done, this session:** a clean install (fresh copy, not a symlink,
  matching what the Addon Manager does) into FreeCAD's real
  `~/.local/share/FreeCAD/v1-1/Mod/` under FreeCAD 1.1.0, confirmed the
  workbench auto-registers with no Report View errors. Drove the real
  "Add Location…" dialog under Xvfb — both a static open-dialog
  screenshot and a full programmatic Fetch & Build run for the Louvre
  preset (network fetch on a background thread, geometry build on the
  main thread with `processEvents` pumping) — captured a screenshot of
  the generated 3D site. Ran a separate headless (`freecadcmd`)
  regression across all 3 presets against the live Overpass/opentopodata
  APIs, including a specific check that OSM building *relations*
  (multipolygons with courtyard holes) are now built correctly — the
  exact case the v0 prototype silently skipped — and a synthetic-data
  check that the flat-terrain fallback branch behaves correctly.
- **NOT yet done — requires the named human maintainer before
  submission:** independent review of the equirectangular-projection
  accuracy tradeoffs and the "relief exceeds 2m ⇒ terrain mesh" heuristic
  against real-world expectations (this session's own README flags that
  SRTM 90m over dense urban cores likely reflects rooftop noise, not bare
  earth — a human should decide if that threshold/behavior is acceptable
  for a first public release); a live multi-platform install test
  (Windows/macOS Mod paths were not exercised, only Linux); and all the
  human-only steps that would normally live in a `RELEASE_CHECKLIST.md`
  (maintainer contact info, repository creation, version-tag discipline).

## Prior art and heritage disclosure

A dedicated prior-art search was performed before this addon was
considered release-quality (2026-07-10, `ops/novelty-sitecontext.md` in
the build record, live WebSearch/WebFetch only, no git commands, no
sub-agents). It found a real lineage this addon succeeds:
**microelly2/geodata** (the original FreeCAD "GeoData workbench,"
removed from the Addon Manager ~November 2020) and
**rostskadat/FreeCAD-geodata2** (an explicit fork/rework of it, still
listed in the Addon Manager, last commit 2024-09-30, predating FreeCAD
1.0's minimum-version declaration and with no evidence of 1.x
verification). Both are named and credited in the addon's own README
("Heritage & prior art" section) as the community's own prior attempts at
this problem; this addon is positioned as their actively-maintained,
FreeCAD-1.x-verified evolution — adding relation/multipolygon support,
generated terrain, place-name geocoding, and attribution handling that
neither predecessor has — not as a "first of its kind" claim.

## Status

This addon has **not yet been submitted anywhere**. This disclosure is
prepared in advance so that whenever the maintainer does open the Index
issue, a PR, or a forum post, the disclosure language is already reviewed
and ready to attach — not written under time pressure at submission time.
