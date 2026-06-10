# Map-state refactor — implementation plan (Cluster A)

Status: **planned, not implemented.** Scoped from Erin + crystallographer feedback
(2026-06). Addresses the "the maps were frustrating" cluster, which is mostly one
root cause: **map session state is torn down and rebuilt from scratch on every
event/dataset switch**, and **map colour is left to Moorhen's molNo-rotating
default**. All work is in the client; no API/schema changes.

## Why these are one problem

Every event load rebuilds `maps` from nothing via `setMaps(loaded)`
([InspectDrawer.tsx:462](../client/src/components/InspectDrawer.tsx#L462)) with
each map hardcoded `visible: true`
([371](../client/src/components/InspectDrawer.tsx#L371),
[408](../client/src/components/InspectDrawer.tsx#L408)) and a freshly-seeded
contour. New maps get a new `molNo` each load, so Moorhen's palette hands each one
a different colour. Nothing the user set survives the switch.

**What "active map" actually is (corrected).** Moorhen's *active map* is the
**client-side interactive refinement target** (Coot `set_imol_refinement_map`),
and also the scroll-wheel contour target. It is **NOT** the centre-follow
mechanism — recentring is `dispatch(setOrigin(...))`, a separate concern (see
CLAUDE.md). Conflating active-map with centre-tracking — via the
`MapScrollWheelListener`/`mapCentre` crash — is what led to it being deferred/left
unset. (Server-side servalcat is a *different*, whole-crystal refinement path —
`pollRefineJob` — and is unrelated to the active map.)

So the correct default active map is **the current event's event map**, and not as
a viewing fallback: the BDC-corrected event map is the *right* target for
interactively fitting/refining a PanDDA ligand into bound-state density. Scroll-to-
contour then acts on the event map by default too, which is also what you want.

## Feedback → fix map

| Feedback (Erin / crystallographer) | Fix | PR |
|---|---|---|
| "Active map was not the event map, had to change it every dataset" / "would like event map auto-active" | Default active = Event map | A1 |
| "Maps kept changing colours between datasets" / "Could the map colours be standardised" | Deterministic colour per map type | A2 |
| "Had to keep turning maps off every dataset — didn't remember settings" | Persist visibility + contour across switches | A3 |
| "Can a reset to default be added for sigma contouring?" | Per-map reset-to-default button | A3 |
| "Mini map toggles slower… didn't show change if state changed via map tools" / "don't show which map is active" | Subscribe control rows to Redux; badge active map | A4 |

(Out of scope here — tracked in the full triage: crash cluster B, decision/site
semantics C, auto-advance/hydrogens/declutter D.)

---

## PR A1 — Event map active by default

**Change.** Flip the active-target preference from 2Fo-Fc to Event.

- [InspectDrawer.tsx:478-480](../client/src/components/InspectDrawer.tsx#L478):
  ```
  const activeTarget =
    loaded.find((m) => m.label === "Event") ??
    loaded.find((m) => m.label === "2Fo-Fc");
  ```
- Keep the existing `fetchMapCentre()` guard
  ([481-501](../client/src/components/InspectDrawer.tsx#L481)) verbatim — a fresh
  CCP4 event map has `mapCentre=null`, so the guard matters *more* now that the
  event (CCP4) map is the one being activated. Verify activation still succeeds
  for a CCP4 event map (it calls Coot `get_map_molecule_centre`); if it
  intermittently yields null, that's the seam to harden (relates to crash
  cluster B "active map has no data").

**Why the event map is the *correct* target (not a compromise).** The active map
is the client-side interactive refinement target (`set_imol_refinement_map`); the
BDC-corrected event map is exactly what you want to fit/refine a PanDDA ligand
against (bound-state density restored toward full occupancy), and scroll-to-contour
then acts on it too. No 2Fo-Fc fallback transient is needed for the primary task —
if someone wants to refine protein geometry against 2Fo-Fc they can switch the
active map manually, but the default is right for ligand fitting.

**Risk:** low. **Test:** load an event → the event map is the active/refinement
target (scroll-wheel contours it; interactive refine targets it); centre-follow
still works via `setOrigin` independently.

---

## PR A2 — Standardise map colours by type

### The wheel, confirmed

Coot/Moorhen assigns map colour from a **hue-rotating wheel**, not a fixed value:
`MoorhenMap.setDefaultColour()`
([MoorhenMap.ts:1228](../../emsdk/Moorhen/baby-gru/src/utils/MoorhenMap.ts#L1228))
takes the base blue `rgb(0.3,0.3,0.7)` and rotates
`h += 10° × (count of currently-valid non-difference maps with molNo < this one)`.
Difference maps are exempt (early return) and use fixed ±green/red
(`_DEFAULT_POSITIVE/NEGATIVE_MAP_COLOUR`). Molecules share the heritage: their
default colours come from libcoot `get_colour_rules(molNo)`
([MoorhenMolecule.ts:2208](../../emsdk/Moorhen/baby-gru/src/utils/MoorhenMolecule.ts#L2208)),
the same molNo-derived wheel materialised as per-chain rules.

**Why colours drift between datasets:** we churn maps on every switch — molNo
climbs monotonically and the count of *concurrently-valid* maps varies per dataset
— so the wheel lands on a different hue each time. The wheel position is *derived*
from valid-map count, **not a resettable register**, so there is no literal
"reset the wheel" call (confirmed: no such fn in `libcoot.d.ts`).

**The override seam (this is what we use).** The Redux action `setMapColours({
molNo, rgb })`
([mapContourSettingsSlice.ts:126](../../emsdk/Moorhen/baby-gru/src/store/mapContourSettingsSlice.ts#L126))
writes `mapContourSettings.mapColours[]`, and `getMapContourParams`
([MoorhenMap.ts:539](../../emsdk/Moorhen/baby-gru/src/utils/MoorhenMap.ts#L539))
reads it **in preference to** the wheel default. Dispatch it per map after load and
the wheel becomes irrelevant — colour is pinned. (`rgb` is 0–255; the slice divides
by 255 at draw time. Difference maps use `setPositive/NegativeMapColours`.)

### Decisions taken

- **Pin by role**, fixed forever: `Event` → teal, `2Fo-Fc` → Coot's canonical blue
  `rgb(76,76,178)`, `Fo-Fc` → leave the ±green/red difference default (do **not**
  flatten it to one colour).
- **Reset is automatic per event-navigation, not a button.** Because we re-dispatch
  the canonical role colours on every load, every navigation *is* the reset — the
  user never has to intervene, and the wheel can never drift back in.
- Applies to **maps and molecules** ("both", as asked):
  - **Ligand pose** (per-event, new molNo each load) → pin to a distinct fixed
    colour (e.g. yellow carbons).
  - **Protein model** → pin to **one canonical colour, always** (override Coot's
    per-chain molNo wheel via a molecule-wide colour rule). The current event's
    model must read the *same* colour every navigation — this is now a firm
    requirement, not Coot-default.

### Two colour tiers (focal vs foreign)

This is the frame for everything below. There are two classes of object in the
view:

- **Focal** = the current event's own objects → **canonical, role-pinned**
  colours (above). Stable every navigation. The wheel is overridden away.
- **Foreign** = objects *pulled in from other events* for comparison (PR A5) →
  **per-source rotating hue**. Here the colour wheel is the *right* tool: each
  pulled-in source event gets its own hue so multiple are mutually
  distinguishable, and they never collide with the canonical focal palette.

So the same wheel we override for the focal event is re-homed, deliberately, for
the foreign tier.

**Change.**
1. In `moorhen-shim.ts`, add typed wrappers mirroring the existing
   `setContourLevel`/`setActiveMap` pattern ([shim:157-174](../client/src/moorhen-shim.ts#L157)):
   `setMapColour({ molNo, rgb })` (+ positive/negative variants if needed).
2. Add a role→colour constant. In `InspectDrawer.tsx`, dispatch `setMapColour`
   right after each `addMap`: event-map block
   ([355-363](../client/src/components/InspectDrawer.tsx#L355)) and `stageModelMap`
   ([397-400](../client/src/components/InspectDrawer.tsx#L397)). Skip difference maps.
3. Pin the ligand-pose colour where the pose molecule is added
   ([537-561](../client/src/components/InspectDrawer.tsx#L537)) via a colour rule.
   No manual reset control — the per-load dispatch is the reset.

**Risk:** low — the override seam is confirmed and read-preferred over the wheel.
**Test:** load three successive datasets; event map is teal every time, 2Fo-Fc blue,
ligand pose the same colour — no drift, no user action.

---

## PR A3 — Persist visibility + contour across switches, + reset-to-default

**Core idea.** Remember per-map-*type* prefs (keyed by `label`, since `molNo`
changes each load) in a ref that survives switches. Seed each freshly-loaded map
from the remembered pref instead of the hardcoded default; keep the seeded default
so "reset" can restore it.

> Note — two different "resets", don't conflate them. **Colour** reset is
> automatic per-navigation (PR A2, pinned by role). **Contour** reset-to-default
> is the crystallographer's explicit ask ("reset to default for sigma
> contouring") and stays a **manual** per-map button — contour is a user-tuned
> axis we deliberately *persist*, so the user needs a way back to the seed.

**Changes (`InspectDrawer.tsx`):**
1. Extend `LoadedMap` ([112-120](../client/src/components/InspectDrawer.tsx#L112))
   with `defaultValue: number` (the seeded contour for reset).
2. Add a session ref:
   `const mapPrefsRef = useRef<Record<string, { visible: boolean; sliderValue: number }>>({})`
   keyed by label (`"Event" | "2Fo-Fc" | "Fo-Fc"`).
3. At load, after computing each map's seeded `level`/`visible`, **override from
   prefs if present**: in the event block
   ([364-372](../client/src/components/InspectDrawer.tsx#L364)) and `stageModelMap`
   ([401-409](../client/src/components/InspectDrawer.tsx#L401)). Dispatch the
   *remembered* contour/visibility to Redux so the map renders as the user left it
   (this also flows through the existing re-assert macrotask at
   [520-526](../client/src/components/InspectDrawer.tsx#L520)).
4. Write prefs back in `onContour`
   ([585-601](../client/src/components/InspectDrawer.tsx#L585)) and
   `onToggleVisible` ([605-617](../client/src/components/InspectDrawer.tsx#L605)).
5. **Reset button:** add a small reset `IconButton` to each map control row
   ([1435-1483](../client/src/components/InspectDrawer.tsx#L1435)); on click,
   restore `defaultValue`, update `maps` state + prefs + dispatch
   `setContourLevel`.

**Decision for the user:** persistence scope.
- **Recommended (this PR):** session-only (component-lifetime ref). Survives every
  switch while the drawer is open; resets on reload.
- **Later:** mirror prefs to `localStorage` for cross-session memory. Trivial
  add-on once the ref exists.

**Risk:** medium — interacts with the known contour-clobber race
([503-526](../client/src/components/InspectDrawer.tsx#L503)); seed prefs into the
same `desired` re-assert so we win the macrotask. **Test:** hide 2Fo-Fc + retune
event contour, switch dataset and back — both remembered; reset restores seed.

---

## PR A4 — Sync the control rows to Redux + show active map

The per-map rows ([1435-1483](../client/src/components/InspectDrawer.tsx#L1435))
are Erin's "mini map toggles." They read only local `maps` state, so changes made
with Moorhen's *native* map UI don't reflect, and they don't mark the active map.

**Changes (`InspectDrawer.tsx`):**
1. `useSelector` the `mapContourSettings.contourLevels` + `visibleMaps` slices and
   the active-map slice (confirm slice paths in the shim/Moorhen source). Add
   typed selectors to `moorhen-shim.ts` alongside the existing action wrappers.
2. Render each row's slider value + visibility icon from the Redux-derived value
   (converted absolute→unit for the σ display via `mapRmsd`), falling back to
   local `maps`. This makes external changes show up.
3. Badge the active map (e.g. a dot / "active" chip on the row whose `molNo` ===
   active map molNo).

**Risk:** medium — must reconcile σ-vs-absolute units (Redux stores absolute;
event rows display absolute, model rows display σ). **Test:** change a contour via
Moorhen's own control → the row slider tracks it; active map shows a badge.

---

## PR A5 — Pin sibling observations for comparison (manual, per-source hue)

The biggest item, and a genuine *feature* rather than a refactor. Bring in
maps/models from **other events** so they sit alongside the current event,
visually distinguished.

### What "other events" means — and why overlay just works

Siblings = events sharing `selected.finding` (the run-independent locus). The
[finding axis](../client/src/grouping.ts#L179) already gathers exactly this set:
"the same binding site seen by different runs." Events carry `finding`, `run_id`,
`run_group` ([api.ts:96-98](../client/src/api.ts#L96)).

**Key enabler:** a Finding is *one crystal* (the finding group is per-dataset);
its sibling events are different PanDDA **runs** over the *same data*, so they
share a coordinate frame. Pinned objects therefore **overlay directly — no
superposition needed.** The interesting signal you'll see is the **ligand-pose
wander** across runs (~20Å autobuild drift — see the multi-run-matching memory)
against a *shared* protein/event-map frame. (If we ever extend "pull in" to
cross-*crystal* events, that assumption breaks and superposition becomes
required — flagged here so nobody silently relies on the shared frame.)

### Lifecycle (this is the crux)

Decision: **manual pin per sibling; pinned objects persist across navigation until
dismissed.** That means foreign objects must survive the focal teardown.

1. New state `pinned: PinnedSource[]`, each = `{ eventId, runLabel, hue,
   molNos: number[] }`, plus `pinnedMolNosRef = useRef<Set<number>>()`.
2. **Make `clearMaps`/`clearLoaded` skip pinned molNos.** Today they nuke *every*
   map/molecule in the store
   ([186-216](../client/src/components/InspectDrawer.tsx#L186)); filter out
   `pinnedMolNosRef` so navigation tears down only focal objects. **This is the
   single riskiest edit in the whole plan** — get the filter wrong and you either
   leak focal objects or delete pinned ones.
3. **Pin action:** load the sibling's chosen artifacts as new maps/molecules,
   assign the next source hue, register molNos in `pinnedMolNosRef`, push to
   `pinned`. Do **not** recentre (keep the current view).
4. **Dismiss action:** delete that source's molNos, drop from ref + state.

### What pulls in

Per sibling, default to **event map + ligand pose** (the comparison-worthy pair);
make protein-model optional (it's near-identical across runs of one crystal, so
usually redundant). User-selectable in the pin panel.

### Colour — per-source rotating hue (the wheel, re-homed)

Maintain a hue cursor; source *i* gets `hue = base + GOLDEN_ANGLE*i` (137.5° → max
separation) — apply to that source's maps via `setMapColours` and its molecules
via a colour rule. Keep clear of the canonical focal hues (teal/blue). Render a
small **legend** mapping hue → run label so the overlay is readable.

### UI

A collapsible "Compare runs" panel listing siblings (run label · decision ·
quality) each with a pin toggle + an object-selector; pinned sources show their
hue swatch and a dismiss control. Pinned maps also appear as control rows
(PR A4 list), tagged foreign with source + swatch.

**Risk:** high — persistent cross-navigation state + the clear-lifecycle surgery +
colour/legend management. Strongly consider shipping this as its **own milestone**
after A1–A4 land, not folded into the map-state PRs.

---

## Suggested order & sizing

A1 (tiny) → A2 (small) → A3 (medium, the satisfaction win) → A4 (medium) →
**A5 (large — its own milestone)**. A1–A3 are independently shippable; A4 builds
on A3's pref plumbing; A5 builds on A2's colour seam (focal canonical vs foreign
wheel) and A4's control-row list, and rewires the clear lifecycle.

Lint gate for every PR: `npx tsc --noEmit -p tsconfig.json` in `client/`, 79-col.
