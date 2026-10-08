# SPDX-License-Identifier: LGPL-2.1-or-later
"""Bring a STEP solid/compound into an editable FreeCAD document.

Imports geometry, not the originating application's feature history.
"""

from pathlib import Path

import FreeCAD as App
import Part


def build(params):
    source = Path(params["source"]).resolve(strict=True)
    doc = App.newDocument("ImportedModel")
    obj = doc.addObject("Part::Feature", "ImportedShape")
    obj.Label = source.stem
    obj.Shape = Part.read(str(source))
    return obj
