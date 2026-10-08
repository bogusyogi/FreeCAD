# SPDX-License-Identifier: LGPL-2.1-or-later
"""Curved solid with independently known volume: 5.40871353861894 mm³."""

import FreeCAD as App
import Part


def build(params):
    doc = App.newDocument("RationalProfile")
    curve = Part.BSplineCurve()
    curve.buildFromPolesMultsKnots(
        [App.Vector(0, 0, 1), App.Vector(0.5, 0, 10), App.Vector(1, 0, 1)],
        [3, 3], [0.0, 1.0], False, 2, [1.0, 10.0, 1.0]
    )
    corners = [App.Vector(1, 0, 1), App.Vector(1, 0, 0),
               App.Vector(0, 0, 0), App.Vector(0, 0, 1)]
    edges = [curve.toShape()] + [Part.makeLine(a, b) for a, b in zip(corners, corners[1:])]
    profile = doc.addObject("PartDesign::Feature", "Profile")
    profile.Shape = Part.Face(Part.Wire(edges))
    result = doc.addObject("Part::Extrusion", "Extrusion")
    result.Base = profile
    result.Dir = App.Vector(0, 1, 0)
    result.LengthFwd = 1
    result.Solid = True
    doc.recompute()
    return result
