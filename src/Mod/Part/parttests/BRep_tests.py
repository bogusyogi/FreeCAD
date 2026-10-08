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

    def testAdaptiveVolumeRationalSpline(self):
        # A rational quadratic profile extruded by one unit. Its volume is
        # integral(z(t) * x'(t), t=0..1). Default OCCT integration errs by 0.6%.
        weight = 10.0
        poles = [App.Vector(0, 0, 1), App.Vector(0.5, 0, 10), App.Vector(1, 0, 1)]
        weights = [1.0, weight, 1.0]
        corners = [App.Vector(1, 0, 1), App.Vector(1, 0, 0),
                   App.Vector(0, 0, 0), App.Vector(0, 0, 1)]
        for curve_type in ("bspline", "bezier"):
            with self.subTest(curve_type=curve_type):
                if curve_type == "bspline":
                    curve = Part.BSplineCurve()
                    curve.buildFromPolesMultsKnots(
                        poles, [3, 3], [0.0, 1.0], False, 2, weights
                    )
                else:
                    curve = Part.BezierCurve()
                    curve.setPoles(poles)
                    for index, curve_weight in enumerate(weights, 1):
                        curve.setWeight(index, curve_weight)
                edges = [curve.toShape()] + [Part.makeLine(a, b)
                                               for a, b in zip(corners, corners[1:])]
                solid = Part.Face(Part.Wire(edges)).extrude(App.Vector(0, 1, 0))
                self.assertTrue(solid.isValid())
                volume, error = solid.getVolumeProperties(1e-8)
                # Independently integrated rational Bernstein polynomials, not mesh volume.
                self.assertAlmostEqual(volume, 5.40871353861894, places=6)
                self.assertTrue(math.isfinite(error))
                self.assertGreaterEqual(error, 0)
                self.assertLessEqual(error, 1e-8)
                self.assertAlmostEqual(solid.Volume, 5.40871353861894, places=6)

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
