# SPDX-License-Identifier: LGPL-2.1-or-later

import FreeCAD as App
import Part

import unittest
import math


class BRepTests(unittest.TestCase):

    def testAdaptiveVolumeProperties(self):
        outer = Part.makeBox(10, 10, 10)
        hole = Part.makeCylinder(2, 10, App.Vector(5, 5, 0))
        solid = outer.cut(hole).Solids[0]
        solid.translate(App.Vector(1234, -456, 78))
        volume, error = solid.getVolumeProperties(1e-7)
        self.assertAlmostEqual(volume, 1000 - 40 * math.pi, places=6)
        self.assertTrue(math.isfinite(error))
        self.assertGreaterEqual(error, 0)
        self.assertLessEqual(error, 1e-7)
        solid.reverse()
        self.assertAlmostEqual(solid.getVolumeProperties()[0], -volume, places=6)

    def testAdaptiveVolumeRejectsInvalidArguments(self):
        box = Part.makeBox(1, 2, 3)
        for eps in (0, -1, 0.01, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                box.getVolumeProperties(eps)
        for shape in (Part.Shape(), box.Faces[0], Part.makeCompound([box])):
            with self.assertRaises(ValueError):
                shape.getVolumeProperties()

    def testProject(self):
        """
        This is a unit test for PR #13507
        """
        num = 18
        alt = [0, 1] * num
        pts = [App.Vector(i, alt[i], 0) for i in range(num)]

        bsc = Part.BSplineCurve()
        bsc.buildFromPoles(pts, False, 1)
        edge = bsc.toShape()

        rts = Part.RectangularTrimmedSurface(Part.Plane(), -50, 50, -50, 50)
        plane_shape = rts.toShape()

        proj = plane_shape.project([edge])
        self.assertFalse(proj.isNull())
        self.assertEqual(len(proj.Edges), 1)

    def testEdgeSplitFace(self):
        coords2d = [(0.5, -0.5), (1.0, -0.5), (1.0, 0.5), (0.5, 0.5)]
        pts2d = [App.Base.Vector2d(u, v) for u, v in coords2d]
        pts2d.append(pts2d[0])

        sphere = Part.Sphere()
        edges = []
        for i in range(1, len(pts2d)):
            ls = Part.Geom2d.Line2dSegment(pts2d[i - 1], pts2d[i])
            edges.append(ls.toShape(sphere))

        split = edges[0].split(0.25)
        new_edges = split.Edges + edges[1:]
        wire = Part.Wire(new_edges)
        face = Part.Face(wire, "Part::FaceMakerSimple")
        self.assertTrue(face.isValid())

    def testEdgeSplitReplace(self):
        cyl = Part.makeCylinder(2, 5)
        e1 = cyl.Edge3
        split = e1.split([1.0, 2.0])
        newcyl = cyl.replaceShape([(e1, split), (cyl.Vertex2, split.Vertex1)])
        self.assertTrue(newcyl.isValid())
