# 3D prints

Printable parts for running freETarget targets with fet-monitor. Each part has its own folder with
ready-to-print STL files, an OpenSCAD source, a build script and a README. The STLs are built from the
source with the part's `build-stls.sh`; rebuild and commit them together whenever the source changes.

| Part | What it is for |
|---|---|
| [pipe-guide-bracket](pipe-guide-bracket/) | Keeps a Competition target from twisting on a 1" steel-pipe stand while it is raised and lowered |

Rebuilding needs OpenSCAD; the development snapshot is recommended. On a Mac: `brew install --cask openscad@snapshot`.
