/*!
 * \brief Represent a PSF as a linear combination of PCA (== Karhunen-Loeve) basis functions
 *
 * \file
 *
 * \ingroup algorithms
 */
#include <cmath>
#include <numeric>
#include "lsst/pex/exceptions.h"
#include "lsst/afw/image/ImageUtils.h"
#include "lsst/afw/math/Statistics.h"
#include "lsst/meas/algorithms/detail/pcaPSF.h"
#include "lsst/meas/algorithms/detail/PSFImpl.h"

namespace lsst { namespace meas { namespace algorithms {

/************************************************************************************************************/
/**
 * Constructor for a pcaPSF
 */
pcaPSF::pcaPSF(lsst::afw::math::Kernel::PtrT kernel ///< The desired Kernel
              ) : PSF(kernel) {
    //
    // Check that it's a LinearCombinationKernel
    //
    if (kernel.get() != NULL &&
        dynamic_cast<lsst::afw::math::LinearCombinationKernel *>(kernel.get()) == NULL) {
        throw LSST_EXCEPT(lsst::pex::exceptions::InvalidParameterException,
                          "pcaPSF expects a LinearCombinationKernel");
    }

    static bool first = true;
    if (first) {
        pcaPSF::registerType("PCA", PCA);
        first = false;
    }
}
/**
 * \brief Evaluate the PSF at (dx, dy) (relative to the centre), taking the central amplitude to be 1.0
 *
 * N.b. this routine is very inefficient, recalculating the entire PSF image at every call
 */
double pcaPSF::doGetValue(double const dx,            ///< Desired column (relative to centre of PSF)
                          double const dy,            ///< Desired row (relative to centre of PSF)
                          int xPositionInImage,       ///< Desired column position in image (think "CCD")
                          int yPositionInImage        ///< Desired row position in image (think "CCD")
                        ) const {
    // "ir" : (integer, residual)
    std::pair<int, double> const ir_dx = lsst::afw::image::positionToIndex(dx, true); // fractional part of position
    std::pair<int, double> const ir_dy = lsst::afw::image::positionToIndex(dy, true);

    lsst::afw::image::Image<PSF::PixelT>::Ptr im = getImage(xPositionInImage + ir_dx.second,
                                                            yPositionInImage + ir_dy.second);

    return (*im)(ir_dx.first, ir_dy.first)/makeStatistics(*im, lsst::afw::math::MAX).getValue();
}

/************************************************************************************************************/
/*
 * Return an Image of the the PSF at the point (x, y), setting the sum of all the PSF's pixels to 1.0
 *
 * The specified position is a floating point number, and the resulting image will
 * have a PSF with the correct fractional position, with the centre within pixel (width/2, height/2)
 * Specifically, fractional positions in [0, 0.5] will appear above/to the right of the center,
 * and fractional positions in (0.5, 1] will appear below/to the left (0.9999 is almost back at middle)
 */
lsst::afw::image::Image<PSF::PixelT>::Ptr pcaPSF::getImage(double const x, ///< column position in parent %image
                                                           double const y  ///< row position in parent %image
                                                          ) const {
    lsst::afw::image::Image<PSF::PixelT>::Ptr im(new lsst::afw::image::Image<PSF::PixelT>(getWidth(), getHeight()));

    getKernel()->computeImage(*im, false, x, y);
    
    // "ir" : (integer, residual)
    std::pair<int, double> const ir_dx = lsst::afw::image::positionToIndex(x, true); // fractional part of position
    std::pair<int, double> const ir_dy = lsst::afw::image::positionToIndex(y, true);
    
    im = lsst::afw::math::offsetImage(*im, ir_dx.second, ir_dy.second, "lanczos5");
    //
    // Normalise image
    //
    *im /= std::accumulate(im->begin(true), im->end(true), static_cast<double>(0));

    return im;
}

//
// We need to make an instance here so as to register it with createPSF
//
// \cond
namespace {
    PSF* foo = new pcaPSF(lsst::afw::math::LinearCombinationKernel::PtrT());
}

// \endcond
}}}