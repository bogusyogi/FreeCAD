// SPDX-License-Identifier: LGPL-2.1-or-later

#include <src/App/InitApplication.h>
#include <App/Document.h>
#include <Mod/Measure/App/Measurement.h>
#include <Mod/Measure/App/MassPropertiesResult.h>
#include <Mod/Part/App/PartFeature.h>

#include <BRepBuilderAPI_MakeEdge.hxx>
#include <BRepBuilderAPI_MakeFace.hxx>
#include <BRepBuilderAPI_MakeWire.hxx>
#include <BRepGProp.hxx>
#include <BRepPrimAPI_MakePrism.hxx>
#include <Geom_BezierCurve.hxx>
#include <Geom_BSplineCurve.hxx>
#include <TColgp_Array1OfPnt.hxx>
#include <TColStd_Array1OfInteger.hxx>
#include <TColStd_Array1OfReal.hxx>
#include <GProp_GProps.hxx>
#include <gp_Ax2.hxx>
#include <gp_Circ.hxx>
#include <gp_Dir.hxx>
#include <gp_Pln.hxx>
#include <gp_Pnt.hxx>
#include <gp_Vec.hxx>
#include <Precision.hxx>
#include <gtest/gtest.h>

class QuickMeasureTest: public ::testing::Test
{
protected:
    static void SetUpTestSuite()
    {
        tests::initApplication();
    }

    void SetUp() override
    {
        document = App::GetApplication().newDocument("QuickMeasure");
    }

    void TearDown() override
    {
        App::GetApplication().closeDocument(document->getName());
    }

    Part::Feature* makeDisc(const char* name, const gp_Pnt& center) const
    {
        gp_Circ circle(gp_Ax2(center, gp_Dir(0.0, 0.0, 1.0)), 5.0);
        TopoDS_Edge edge = BRepBuilderAPI_MakeEdge(circle).Edge();
        TopoDS_Wire wire = BRepBuilderAPI_MakeWire(edge).Wire();

        auto feature = document->addObject<Part::Feature>(name);
        feature->Shape.setValue(BRepBuilderAPI_MakeFace(wire).Face());
        return feature;
    }

    Part::Feature* makePlane(const char* name, double z) const
    {
        gp_Pln plane(gp_Pnt(0.0, 0.0, z), gp_Dir(0.0, 0.0, 1.0));

        auto feature = document->addObject<Part::Feature>(name);
        feature->Shape.setValue(BRepBuilderAPI_MakeFace(plane, -5.0, 5.0, -5.0, 5.0).Face());
        return feature;
    }

    Part::Feature* makeRationalSolid(const char* name, bool useBezier) const
    {
        TColgp_Array1OfPnt poles(1, 3);
        poles(1) = gp_Pnt(0.0, 0.0, 1.0);
        poles(2) = gp_Pnt(0.5, 0.0, 10.0);
        poles(3) = gp_Pnt(1.0, 0.0, 1.0);
        TColStd_Array1OfReal weights(1, 3);
        weights(1) = 1.0;
        weights(2) = 10.0;
        weights(3) = 1.0;
        TopoDS_Edge curveEdge;
        if (useBezier) {
            Handle(Geom_BezierCurve) curve = new Geom_BezierCurve(poles, weights);
            curveEdge = BRepBuilderAPI_MakeEdge(curve).Edge();
        }
        else {
            TColStd_Array1OfInteger multiplicities(1, 2);
            multiplicities(1) = 3;
            multiplicities(2) = 3;
            TColStd_Array1OfReal knots(1, 2);
            knots(1) = 0.0;
            knots(2) = 1.0;
            Handle(Geom_BSplineCurve) curve = new Geom_BSplineCurve(
                poles, weights, knots, multiplicities, 2, Standard_False
            );
            curveEdge = BRepBuilderAPI_MakeEdge(curve).Edge();
        }
        BRepBuilderAPI_MakeWire wire;
        wire.Add(curveEdge);
        const gp_Pnt bottomRight(1.0, 0.0, 0.0);
        const gp_Pnt bottomLeft(0.0, 0.0, 0.0);
        wire.Add(BRepBuilderAPI_MakeEdge(poles(3), bottomRight).Edge());
        wire.Add(BRepBuilderAPI_MakeEdge(bottomRight, bottomLeft).Edge());
        wire.Add(BRepBuilderAPI_MakeEdge(bottomLeft, poles(1)).Edge());
        auto feature = document->addObject<Part::Feature>(name);
        feature->Shape.setValue(
            BRepPrimAPI_MakePrism(
                BRepBuilderAPI_MakeFace(wire.Wire()).Face(),
                gp_Vec(0.0, 1.0, 0.0)
            ).Shape()
        );
        return feature;
    }

private:
    App::Document* document {};
};

// NOLINTBEGIN(readability-magic-numbers)
TEST_F(QuickMeasureTest, SingleDiscKeepsDiscType)
{
    Measure::Measurement measurement;
    measurement.addReference3D(makeDisc("Disc", gp_Pnt(0.0, 0.0, 0.0)), "Face1");

    EXPECT_EQ(measurement.getType(), Measure::MeasureType::Disc);
}

TEST_F(QuickMeasureTest, ParallelDiscsHaveNominalDistance)
{
    Measure::Measurement measurement;
    measurement.addReference3D(makeDisc("Disc1", gp_Pnt(0.0, 0.0, 0.0)), "Face1");
    measurement.addReference3D(makeDisc("Disc2", gp_Pnt(3.0, 0.0, 7.5)), "Face1");

    EXPECT_EQ(measurement.getType(), Measure::MeasureType::TwoDiscs);
    EXPECT_NEAR(measurement.planePlaneDistance(), 7.5, Precision::Confusion());
    EXPECT_NEAR(measurement.discAxisDistance(), 3.0, Precision::Confusion());
}

TEST_F(QuickMeasureTest, ParallelDiscAndPlaneHaveNominalDistance)
{
    Measure::Measurement measurement;
    measurement.addReference3D(makeDisc("Disc", gp_Pnt(0.0, 0.0, 2.0)), "Face1");
    measurement.addReference3D(makePlane("Plane", 10.0), "Face1");

    EXPECT_EQ(measurement.getType(), Measure::MeasureType::TwoPlanes);
    EXPECT_NEAR(measurement.planePlaneDistance(), 8.0, Precision::Confusion());
}

TEST_F(QuickMeasureTest, RationalSolidHasAccurateVolumeAndMassProperties)
{
    auto feature = makeRationalSolid("RationalSolid", true);
    auto splineFeature = makeRationalSolid("RationalBSplineSolid", false);
    ASSERT_TRUE(feature->Shape.getShape().isValid());
    ASSERT_TRUE(splineFeature->Shape.getShape().isValid());

    // Independent 200,000-panel Simpson integration of the rational profile.
    constexpr double expectedVolume = 5.4087135386429628;
    constexpr double expectedCenterZ = 3.2753075434687751;
    constexpr double expectedInertiaX = 28.133044057491972;
    constexpr double expectedInertiaY = 27.95226192324985;
    constexpr double expectedInertiaZ = 0.72067012219837623;
    const auto volumeProperties = [](const TopoDS_Shape& shape) {
        GProp_GProps gkProps;
        const double gkError = BRepGProp::VolumePropertiesGK(
            shape, gkProps, 1e-6, false, false, true, true
        );
        EXPECT_GE(gkError, 0.0);
        EXPECT_LE(gkError, 1e-6);
        return gkProps;
    };
    const GProp_GProps bezierGK = volumeProperties(feature->Shape.getValue());
    const GProp_GProps bsplineGK = volumeProperties(splineFeature->Shape.getValue());
    EXPECT_NEAR(bezierGK.Mass(), expectedVolume, 1e-6);
    EXPECT_NEAR(bsplineGK.Mass(), expectedVolume, 1e-6);
    EXPECT_NEAR(bezierGK.CentreOfMass().X(), 0.5, 1e-6);
    EXPECT_NEAR(bezierGK.CentreOfMass().Y(), 0.5, 1e-6);
    EXPECT_NEAR(bezierGK.CentreOfMass().Z(), expectedCenterZ, 1e-6);
    EXPECT_NEAR(bsplineGK.CentreOfMass().X(), 0.5, 1e-6);
    EXPECT_NEAR(bsplineGK.CentreOfMass().Y(), 0.5, 1e-6);
    EXPECT_NEAR(bsplineGK.CentreOfMass().Z(), expectedCenterZ, 1e-6);
    EXPECT_NEAR(bezierGK.MatrixOfInertia()(1, 1), expectedInertiaX, 1e-6);
    EXPECT_NEAR(bezierGK.MatrixOfInertia()(2, 2), expectedInertiaY, 1e-6);
    EXPECT_NEAR(bezierGK.MatrixOfInertia()(3, 3), expectedInertiaZ, 1e-6);
    EXPECT_NEAR(bsplineGK.MatrixOfInertia()(1, 1), expectedInertiaX, 1e-6);
    EXPECT_NEAR(bsplineGK.MatrixOfInertia()(2, 2), expectedInertiaY, 1e-6);
    EXPECT_NEAR(bsplineGK.MatrixOfInertia()(3, 3), expectedInertiaZ, 1e-6);

    const auto verifyFreeCADProperties = [&](Part::Feature* rationalSolid) {
        Measure::Measurement measurement;
        measurement.addReference3D(rationalSolid, "");
        EXPECT_NEAR(measurement.volume(), expectedVolume, 1e-6);
        MassPropertiesInput input;
        input.object = rationalSolid;
        input.shape = rationalSolid->Shape.getValue();
        auto result = CalculateMassProperties({input}, MassPropertiesMode::CenterOfGravity, nullptr);
        EXPECT_NEAR(result.volume.getValue(), expectedVolume, 1e-6);
        EXPECT_NEAR(result.mass.getValue(), expectedVolume * 1e-6, 1e-12);
        EXPECT_NEAR(result.cov.x, 0.5, 1e-6);
        EXPECT_NEAR(result.cov.y, 0.5, 1e-6);
        EXPECT_NEAR(result.cov.z, expectedCenterZ, 1e-6);
        constexpr double defaultDensity = 1e-6;  // kg/mm^3, MassProperties fallback
        EXPECT_NEAR(result.inertiaJo.x, expectedInertiaX * defaultDensity, 1e-10);
        EXPECT_NEAR(result.inertiaJo.y, expectedInertiaY * defaultDensity, 1e-10);
        EXPECT_NEAR(result.inertiaJo.z, expectedInertiaZ * defaultDensity, 1e-10);
    };
    verifyFreeCADProperties(feature);
    verifyFreeCADProperties(splineFeature);
}
// NOLINTEND(readability-magic-numbers)
