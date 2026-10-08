# Headless native FreeCAD prototype

This fork starts with a small agent-facing Python runner using FreeCAD alone.
It requires an official FreeCAD installation; it does not compile or replace the
application. Tested runtime: FreeCAD 1.1.4. No build123d dependency, MCP server,
running GUI, custom viewer, or additional Python package is required.

## Generate a part

Use system Python to launch a **trusted** model file exposing `build(params)`.
The function returns a FreeCAD document object with a solid `Shape`.
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
The output directory must not exist, preventing stale-success results & accidental
overwrites. Model scripts run as the current user, not in a security sandbox.

## Editable features

`plate.py` produces ordinary `Part::Box`, `Part::Cylinder`, `Part::MultiFuse`
& `Part::Cut` objects. Dimensions & hole positions use FreeCAD expressions.
Open `model.FCStd` in FreeCAD to edit the feature tree directly; no custom Python
proxy or addon is needed to reopen it.

`revise_plate.py` reopens that document, changes length/thickness, verifies
expressions recompute, then adds another native cylinder/cut feature. Pass
`{"document":"/absolute/path/to/model.FCStd"}` as its parameters.

`import_step.py` accepts `{"source":"/absolute/path/to/input.step"}`. It imports
geometry as a compound, not original CAD feature history or assembly metadata.
User STEP fixtures are external inputs & are not included in this public fork.

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

Verified on 2026-10-09 with official FreeCAD 1.1.4 binaries on macOS arm64 &
Windows x86_64: all four modeling/error scenarios & three external STEP fixtures
completed on each host. Imported fixtures retained 1, 7 & 15 solids through native
save/reopen & STEP export/reimport. Default-integration volume drift warnings were
retained in reports, not treated as proof of geometric accuracy.

## Scope of this first prototype

Shape validity, closed solids, positive volume & roundtrip consistency are checked.
Reports explicitly label volume as FreeCAD's default integration with no numerical
error bound. Roundtrip volume has a declared 10 ppm sanity limit; drift above
0.1 ppm & 0.00001 mm³ is reported as a warning. Those are serialization checks,
not a geometric accuracy guarantee; analytic plate checks remain tighter.
This is not adaptive metrology, assembly interference validation,
stable face selection after topology changes, or a finished GUI workbench.
The prototype establishes native editable documents & unattended execution first.
