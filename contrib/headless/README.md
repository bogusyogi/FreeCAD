# Headless native FreeCAD prototype

This fork starts with a small agent-facing Python runner using FreeCAD alone.
It accepts an explicit FreeCADCmd executable from an official installation or
compiled fork. The runner does not compile the application. Runtime checks below
distinguish official binaries from compiled fork changes. No build123d dependency, MCP server,
running GUI, custom viewer, or additional Python package is required.

## Generate a part

Use system Python to launch a **trusted** model file exposing `build(params)`.
The function returns a FreeCAD document object with solid geometry, including
an `App::Part` assembly. Geometry is resolved through `Part.getShape`.
The runner creates a new output directory, starts FreeCADCmd with a timeout,
saves the native document & STEP, then reopens both for consistency checks.

macOS:

```sh
python3 contrib/headless/run.py contrib/headless/plate.py \
  --freecad /Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd \
  --params '{"length":60,"width":24,"thickness":5}' \
  --out /tmp/freecad-plate
```

Windows PowerShell (substitute your installed executable path):

```powershell
py -3.11 contrib/headless/run.py contrib/headless/plate.py `
  --freecad 'C:\Program Files\FreeCAD 1.1\bin\FreeCADCmd.exe' `
  --out "$env:TEMP\freecad-plate"
```

Outputs: `model.FCStd`, `model.step`, `request.json`, `result.json`, `freecad.log`.
Exit status is nonzero on failure, including when FreeCAD swallows a script error.
Operational failures also return JSON on stdout, including missing inputs,
malformed parameters, launch errors, timeouts & missing worker output.
The output directory must not exist, preventing stale-success results & accidental
overwrites. Model scripts run as the current user, not in a security sandbox.

## Editable features

`plate.py` produces ordinary `Part::Box`, `Part::Cylinder`, `Part::MultiFuse`
& `Part::Cut` objects. Dimensions & hole positions use FreeCAD expressions.
Open `model.FCStd` in FreeCAD to edit the feature tree directly; no custom Python
proxy or addon is needed to reopen it.
The runner stores result visibility & a fitted isometric camera in native GUI
metadata so FreeCAD 1.1.4 opens headless output visibly, with construction hidden.

`revise_plate.py` reopens that document, changes length/thickness, verifies
expressions recompute, then adds another native cylinder/cut feature. Pass
`{"document":"/absolute/path/to/model.FCStd"}` as its parameters.

`import_step.py` accepts `{"source":"/absolute/path/to/input.step"}`. It imports
named parts, nested assembly containers & placements using FreeCAD's native
STEP importer. It does not recover original CAD feature history. Assembly
roundtrips compare component names, hierarchy, solid counts & placed bounds.
`assembly.py` exercises nested translation & rotation independently of external
fixtures. Export temporarily bakes a sole assembly root's placement into its
children because the native exporter otherwise drops that placement; an aborted
transaction restores the editable document, which is checked again afterward.
User STEP fixtures are external inputs & are not included in this public fork.

## Edit a real Yokai scale

`yokai_scale.py` imports the supplied Yokai mini assembly & selects its scale
labelled `FIXED_20205_v04`. It adds a native 2 mm square recess on an exposed
planar face. Mount-hole geometry, mating face & untouched components are checked
against the imported source. This is an editing example, not a manufacturing
specification.

```sh
python3 contrib/headless/yokai_e2e.py \
  --freecad /Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd \
  --source '/absolute/path/to/Yokai mini.STEP' \
  --out /tmp/yokai-native-e2e
```

This creates native FCStd documents & STEP exports. Separate FreeCAD processes
create a 0.5 mm recess, then reopen successive saved revisions at 0.75, 0.25,
unchanged 0.25 & 1.0 mm. Each input document's hash is checked afterward.
Boundary depths 0.1 & 1.0 mm are exercised; 1.01 mm is rejected. In FreeCAD,
select `YokaiParameters` & edit `RecessDepth`; native
expressions update the recess without loading an addon. Source STEP & initial
FCStd hashes must remain unchanged. Inspect `yokai-e2e.json` for results. This
exercise passed on official macOS 26.3rc1 & Windows 1.1.4. A macOS GUI console
edit also recomputed, saved & reopened at 0.75 mm with seven solids & no invalid
features. Add `--require-adaptive` to require compiled-fork volume measurement.

## Real-process end-to-end exercise

```sh
python3 contrib/headless/e2e.py \
  --freecad /Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd \
  --out /tmp/freecad-e2e \
  --step /absolute/path/to/real-part.step
```

Runs separate FreeCAD processes for generation, a parameter variant, reopening
& revising saved native features, invalid-input rejection, & each optional STEP
fixture. Checks analytic plate volume/dimensions before & after native/STEP
roundtrips, plus preservation of the original document. Windows uses the same
script with `py -3.11` & its native FreeCADCmd executable.

Initial verification on 2026-10-09 used official FreeCAD 1.1.4 binaries on
macOS arm64 & Windows x86_64. The assembly revision also passed all three
external STEP fixtures on both hosts, retaining 1, 7 & 15 solids, part labels,
nesting & placed component bounds through native & STEP roundtrips. Final eight
scenarios, including the translated/rotated assembly, passed on Mac 26.3rc1 &
Windows 1.1.4. Private fixtures are never uploaded to CI.

## Confirmed issues & fixes

| Issue | Resolution & evidence |
| --- | --- |
| macOS accessibility crashes | Qt 6.8.3 crashes during imports/inspection. Qt 6.11.2 passed those operations but also crashed while accessibility inspected an active parameter editor. The latter remains under investigation; upgrading Qt alone is not a complete fix. |
| Headless documents opening hidden | Result visibility & fitted camera are persisted. Native generated plate, bead & assembly output were opened visually. |
| Headless STEP import flattening assembly structure | Native `Import.insert`/`Import.export` replace flattened shape import/export. Component structure & placed bounds are checked on both roundtrips. |
| STEP export dropping moved/rotated root placement | Transactional export normalization preserves world geometry & restores native placements. Nested placement fixture covers this. |
| Default volume integration overstating a sculpted fixture by about 1.3% | Native Volume, Measure & Mass Properties request adaptive OCCT integration; `Shape.getVolumeProperties(eps)` exposes its estimated relative error. Compiled C++ testing revealed a separate rational Bezier integration error. An alternate integrator is being tested before qualification. |

The earlier crash matches [FreeCAD #30720](https://github.com/FreeCAD/FreeCAD/issues/30720)
& Qt's [accessibility reference-count fix](https://github.com/qt/qtbase/commit/b1ed5f656f064e553b33752f8e87d2f5b9553e38).
The parameter-editor crash also occurs inside Qt accessibility, but has not been
shown to share that earlier root cause. 26.3rc1 is an upstream release candidate,
not a compiled binary of this fork.

## Adaptive measurement

On a compiled fork, measurement sums adaptive integrations over individual solids
at `eps=1e-6`, reporting `volume_estimated_error_mm3`. OCCT's error estimate is
not a certified geometric error bound. The runner rejects non-finite, negative
or insufficiently converged results. Use `--require-adaptive` with `run.py` or
`e2e.py` to reject official binaries that lack this API. The rejection itself was
verified on 26.3rc1. Without this option, older runtimes remain usable for file
operations, but their default-integration volumes are explicitly unqualified.
Strict E2E additionally extrudes equivalent rational BSpline & Bezier profiles
with independently integrated volume 5.40871353861894 mm³, checking native
Volume & Measure results
plus native & STEP roundtrips. C++ coverage also checks Mass Properties.
Default OCCT integration misses this synthetic fixture by about 0.6%.

## Scope of this first prototype

Shape validity, closed solids, positive volume & roundtrip consistency are checked.
Roundtrip volume has a declared 10 ppm sanity limit; drift above
0.1 ppm & 0.00001 mm³ is reported as a warning. Those are serialization checks,
not a geometric accuracy guarantee; analytic plate checks remain tighter.
Assembly interference validation, stable face selection after topology changes
& a finished GUI workbench remain outside this prototype.
The prototype establishes native editable documents & unattended execution first.
