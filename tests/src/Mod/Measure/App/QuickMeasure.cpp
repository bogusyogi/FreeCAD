// SPDX-License-Identifier: LGPL-2.1-or-later

#include <src/App/InitApplication.h>
#include <App/Document.h>
#include <Mod/Measure/App/Measurement.h>
#include <Mod/Measure/App/MassPropertiesResult.h>
#include <Mod/Part/App/PartFeature.h>

#include <BRepBuilderAPI_MakeEdge.hxx>
#include <BRepBuilderAPI_MakeFace.hxx>
#include <BRepBuilderAPI_MakeWire.hxx>
#include <BRepPrimAPI_MakePrism.hxx>
#include <Geom_BezierCurve.hxx>
#include <TColgp_Array1OfPnt.hxx>
#include <TColStd_Array1OfReal.hxx>
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

    Part::Feature* makeRationalSolid() const
    {
        TColgp_Array1OfPnt poles(1, 3);
        poles(1) = gp_Pnt(0.0, 0.0, 1.0);
        poles(2) = gp_Pnt(0.5, 0.0, 10.0);
        poles(3) = gp_Pnt(1.0, 0.0, 1.0);
        TColStd_Array1OfReal weights(1, 3);
        weights(1) = 1.0;
        weights(2) = 10.0;
        weights(3) = 1.0;
        Handle(Geom_BezierCurve) curve = new Geom_BezierCurve(poles, weights);
        BRepBuilderAPI_MakeWire wire;
        wire.Add(BRepBuilderAPI_MakeEdge(curve).Edge());
        const gp_Pnt bottomRight(1.0, 0.0, 0.0);
        const gp_Pnt bottomLeft(0.0, 0.0, 0.0);
        wire.Add(BRepBuilderAPI_MakeEdge(poles(3), bottomRight).Edge());
        wire.Add(BRepBuilderAPI_MakeEdge(bottomRight, bottomLeft).Edge());
        wire.Add(BRepBuilderAPI_MakeEdge(bottomLeft, poles(1)).Edge());
        auto feature = document->addObject<Part::Feature>("RationalSolid");
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
    auto feature = makeRationalSolid();
    Measure::Measurement measurement;
    measurement.addReference3D(feature, "");
    constexpr double expectedVolume = 5.40871353861894;
    EXPECT_NEAR(measurement.volume(), expectedVolume, 1e-6);
    MassPropertiesInput input;
    input.object = feature;
    input.shape = feature->Shape.getValue();
    auto result = CalculateMassProperties({input}, MassPropertiesMode::CenterOfGravity, nullptr);
    EXPECT_NEAR(result.volume.getValue(), expectedVolume, 1e-6);
    EXPECT_NEAR(result.mass.getValue(), expectedVolume * 1e-6, 1e-12);
    EXPECT_NEAR(result.cov.x, 0.5, 1e-6);
    EXPECT_NEAR(result.cov.y, 0.5, 1e-6);
}
// NOLINTEND(readability-magic-numbers)
