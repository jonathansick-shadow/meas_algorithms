// -*- LSST-C++ -*-
#include <numeric>
#include <cmath>
#include <functional>
#include "lsst/pex/exceptions.h"
#include "lsst/pex/logging/Trace.h"
#include "lsst/afw/geom/Point.h"
#include "lsst/afw/image.h"
#include "lsst/afw/math/Integrate.h"
#include "lsst/meas/algorithms/Measure.h"

#include "lsst/afw/detection/Psf.h"
#include "lsst/afw/detection/Photometry.h"

namespace pexExceptions = lsst::pex::exceptions;
namespace pexLogging = lsst::pex::logging;
namespace afwDetection = lsst::afw::detection;
namespace afwGeom = lsst::afw::geom;
namespace afwImage = lsst::afw::image;
namespace afwMath = lsst::afw::math;

namespace lsst {
namespace meas {
namespace algorithms {

/**
 * @brief A class that knows how to calculate fluxes using the PSF photometry algorithm
 * @ingroup meas/algorithms
 */
class PsfPhotometry : public afwDetection::Photometry
{
public:
    typedef boost::shared_ptr<PsfPhotometry> Ptr;
    typedef boost::shared_ptr<PsfPhotometry const> ConstPtr;

    /// Ctor
    PsfPhotometry(double flux,
                  float fluxErr=-1) {
        init();                         // This allocates space for everything in the schema

        set<FLUX>(flux);                // ... if you don't, these set calls will fail an assertion
        set<FLUX_ERR>(fluxErr);         // the type of the value must match the schema
    }

    /// Add desired fields to the schema
    virtual void defineSchema(afwDetection::Schema::Ptr schema ///< our schema; == _mySchema
                     ) {
        Photometry::defineSchema(schema);
    }

    static bool doConfigure(lsst::pex::policy::Policy const& policy);

    template<typename ImageT>
    static Photometry::Ptr doMeasure(typename ImageT::ConstPtr im, afwDetection::Peak const&);
};

namespace {
/**
 * Accumulate sum(x) and sum(x**2)
 */
template<typename T>
struct getSum2 {
    getSum2() : sum(0.0), sum2(0.0) {}
    
    getSum2& operator+(T x) {
        sum += x;
        sum2 += x*x;
        
        return *this;
    }
    
    double sum;                         // \sum_i(x_i)
    double sum2;                        // \sum_i(x_i^2)
};

template <typename MaskedImageT, typename WeightImageT>
class FootprintWeightFlux : public afwDetection::FootprintFunctor<MaskedImageT> {
public:
    FootprintWeightFlux(MaskedImageT const& mimage, ///< The image the source lives in
                        typename WeightImageT::Ptr wimage    ///< The weight image
                       ) : afwDetection::FootprintFunctor<MaskedImageT>(mimage),
                           _wimage(wimage),
                           _sum(0), _x0(0), _y0(0) {}
    
    /// @brief Reset everything for a new Footprint
    void reset() {}        
    void reset(afwDetection::Footprint const& foot) {
        _sum = 0.0;

        afwImage::BBox const& bbox(foot.getBBox());
        _x0 = bbox.getX0();
        _y0 = bbox.getY0();

        if (bbox.getDimensions() != _wimage->getDimensions()) {
            throw LSST_EXCEPT(pexExceptions::LengthErrorException,
                              (boost::format("Footprint at %d,%d -- %d,%d is wrong size "
                                             "for %d x %d weight image") %
                               bbox.getX0() % bbox.getY0() % bbox.getX1() % bbox.getY1() %
                               _wimage->getWidth() % _wimage->getHeight()).str());
        }
    }
    
    /// @brief method called for each pixel by apply()
    void operator()(typename MaskedImageT::xy_locator iloc, ///< locator pointing at the image pixel
                    int x,                                 ///< column-position of pixel
                    int y                                  ///< row-position of pixel
                   ) {
        typename MaskedImageT::Image::Pixel ival = iloc.image(0, 0);
        typename WeightImageT::Pixel wval = (*_wimage)(x - _x0, y - _y0);
        _sum += wval*ival;
    }

    /// Return the Footprint's flux
    double getSum() const { return _sum; }

private:
    typename WeightImageT::Ptr const& _wimage;        // The weight image
    double _sum;                                      // our desired sum
    int _x0, _y0;                                     // the origin of the current Footprint
};

}
/************************************************************************************************************/
/**
 * Set parameters controlling how we do measurements
 */
bool PsfPhotometry::doConfigure(lsst::pex::policy::Policy const& policy)
{
    return true;
}
    
/************************************************************************************************************/
/**
 * Calculate the desired aperture flux using the psf algorithm
 */
template<typename ImageT>
afwDetection::Photometry::Ptr PsfPhotometry::doMeasure(typename ImageT::ConstPtr img,
                                                       afwDetection::Peak const& peak
                                                      )
{
    typedef typename ImageT::Image Image;
    typedef typename ImageT::Image::Pixel Pixel;
    typedef typename Image::Ptr ImagePtr;

    double const xcen = peak.getFx();   ///< object's column position
    double const ycen = peak.getFy();   ///< object's row position
    
    int const ixcen = afwImage::positionToIndex(xcen);
    int const iycen = afwImage::positionToIndex(ycen);

    afwImage::BBox imageBBox(afwImage::PointI(img->getX0(), img->getY0()),
                             img->getWidth(), img->getHeight()); // BBox for data image
    
    double flux = std::numeric_limits<double>::quiet_NaN();
    double fluxErr = std::numeric_limits<double>::quiet_NaN();

    std::cerr << "FAKING PSF" << std::endl;
    afwDetection::Psf::Ptr psf = afwDetection::createPsf("SingleGaussian", 15, 15, 1.0);

    if (psf) {
        afwDetection::Psf::Image::Ptr wimage = psf->computeImage(afwGeom::makePointD(xcen, ycen));
        
        FootprintWeightFlux<ImageT, afwDetection::Psf::Image> wfluxFunctor(*img, wimage);
        // Build a rectangular Footprint corresponding to wimage
        afwDetection::Footprint foot(afwImage::BBox(afwImage::PointI(0, 0),
                                                    wimage->getWidth(), wimage->getHeight()), imageBBox);
        foot.shift(ixcen - wimage->getWidth()/2, iycen - wimage->getHeight()/2);
        
        wfluxFunctor.apply(foot);
        
        getSum2<afwDetection::Psf::Pixel> sum;
        sum = std::accumulate(wimage->begin(true), wimage->end(true), sum);
        
        flux = wfluxFunctor.getSum()/sum.sum2;
    }

    return boost::make_shared<PsfPhotometry>(flux, fluxErr);
}

//
// Explicit instantiations
//
// We need to make an instance here so as to register it with MeasurePhotometry
//
// \cond
#define MAKE_PHOTOMETRYS(TYPE)                                          \
    NewMeasurePhotometry<afwImage::MaskedImage<TYPE> >::declare("PSF", \
        &PsfPhotometry::doMeasure<afwImage::MaskedImage<TYPE> >, \
        &PsfPhotometry::doConfigure \
    )

namespace {
    volatile bool isInstance[] = {
        MAKE_PHOTOMETRYS(float)
#if 0
        ,MAKE_PHOTOMETRYS(double)
#endif
    };
}
    
// \endcond

}}}
