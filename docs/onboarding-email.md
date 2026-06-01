# Colleague onboarding email (template)

A warm, practical note for sharing Reinspect with lab/dev colleagues. Swap in
the real Releases link and tweak the sign-off. Repo:
<https://github.com/martinemnoble1/pandda-inspect-api>

---

**Subject:** Reinspect — a prototype PanDDA event reviewer (have a play)

Hi all,

I've been building a little prototype called **Reinspect** — a desktop app for
reviewing PanDDA events: browse the datasets, triage events, and look at the
electron density (it embeds Moorhen) to decide whether a fragment is really
bound. It's an independent reimagining of the `pandda.inspect` workflow — *not*
the official tool, and not affiliated with the PanDDA project — but it's far
enough along to be worth a look, and I'd love your eyes on it.

**Two ways to try it:**

**1. Just run it (no setup).** Grab an installer from the releases page:

  → https://github.com/martinemnoble1/pandda-inspect-api/releases/latest

  - **macOS (Apple Silicon):** the `…-mac-arm64.dmg`. It's signed + notarized,
    so it just opens — double-click, drag to Applications, go. (Worked
    beautifully on this M1 Mac.)
  - **Windows:** the `…-win-x64.exe`. It's not signed yet, so Windows will warn
    — click **More info → Run anyway**.
  - **Linux:** the `…-.AppImage` or `…-.deb` (also unsigned).

  The app brings its own backend, so there's nothing else to install. First run
  is empty — use **Import** to add a dataset (see test data below).

**2. Run from source** (if you want to hack on it):

  - Requirements: **Python 3.12+** and **Node 20+**.
  - `git clone` the repo, then follow the README's *Develop from source*
    section (venv + `pip install -r requirements.txt`, then `npm install` in
    `client/`). Full detail in `docs/SETUP.md`.

**Getting a dataset to test on.** The public reference is the **BAZ2B** dataset
(Zenodo DOI 10.5281/zenodo.48768). One wrinkle: the Zenodo download is a curated
*results* bundle, not a PanDDA output directory the app can read directly — so
you generate an ingestable dataset by **running PanDDA2 over it yourself**.
PanDDA2 is a separate tool — the xchem repo at
https://github.com/xchem/pandda_2_gemmi (install + run per its own docs). I ran
the latest version on the canonical BAZ2B dataset and it worked beautifully
(incl. on this M1 Mac). That produces a `pandda2_out/` directory, and then:

  - **In the app:** Import → **Browse folder** → select the `pandda2_out`
    folder. It's ingested *in place*, no copy.
  - **From source (CLI):**
    `python manage.py ingest_pandda2 --project BAZ2B --root .../pandda2_out`

If you already have a `pandda2_out/` from a previous run, just point at that.

It's early and rough in places — bug reports, "this is confusing", and
"why doesn't it do X" are all very welcome. Happy to sit down and walk anyone
through it.

Cheers,
Martin
