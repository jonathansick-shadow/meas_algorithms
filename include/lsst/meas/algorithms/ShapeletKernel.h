#ifndef MeasAlgoShapeletKernel_H
#define MeasAlgoShapeletKernel_H

/**
 * \file
 *
 * \brief Defines LocalShapeletKernel and ShapeletKernel
 *
 * LocalShapeletKernel is appropriate for a small patch of an image where the 
 * variation is expected to be minimal (e.g. over the size of a galaxy).
 *
 * ShapeletKernel is the more general case that includes variation across an image.
 *
 * \author Mike Jarvis
 */

#include "lsst/afw/math/Kernel.h"
#include "lsst/afw/image/Image.h"
#include "lsst/afw/image/Wcs.h"
#include "lsst/afw/geom/Point.h"
#include "boost/shared_ptr.hpp"

#include "lsst/meas/algorithms/Shapelet.h"
#include "lsst/meas/algorithms/ShapeletInterpolation.h"

namespace lsst {
namespace meas {
namespace algorithms {

    class LocalShapeletKernel : public lsst::afw::math::AnalyticKernel
    {
        /* \brief LocalShapeletKernel has no spatial variation
         *
         * LocalShapeletKernel is appropriate for a small patch of an image 
         * where the variation is expected to be minimal (e.g. over the size 
         * of a galaxy).
         *
         * It uses a shapelet description of the PSF for its underlying
         * implementation.  As such, the functional form is an analytic
         * function f(x,y) = Sum_pq b_pq psi_pq(x,y,sigma).
         * (See the Shapelet class for more details.)
         *
         * The method computeImage is the best way to use this class.
         *
         * The Kernel classes allows for individual pixels to be 
         * calculated one at a time, so we are forced to implement
         * this as well, but it is much less efficient than calculating
         * the whole image at once.
         *
         * There is a suggestion that we also include a method to 
         * calculate the Fourier transform directly.  This would likely
         * be more efficient than going through the normal image
         * via an FFT, since shapelets are their own Fourier transforms
         * (modulo factors of i).  
         * One concern about this, which needs to be considered more
         * carefully, is the effect of the telescope distortion (the Wcs).
         * The shapelets are defined in sky coordinates.  However, the 
         * appropriate image is in chip coordinates.  So there is a
         * local distortion that is applied.  This means that the 
         * direct Fourier transform is not quite as simple as I had
         * initially thought.  However, I do suspect that this detail
         * can be correctly calculated analytically and the method 
         * could be written that is significantly more efficient than
         * the FFT.
         */

    public :
        typedef boost::shared_ptr<LocalShapeletKernel> Ptr;
        typedef boost::shared_ptr<const LocalShapeletKernel> ConstPtr;

        typedef lsst::afw::math::AnalyticKernel base;
        typedef lsst::afw::image::Image<double> Image;
        typedef lsst::afw::image::Wcs Wcs;

        /*!
         * \brief Constructor from a Shapelet
         *
         * If the width and height are either 0 or omitted, then they are
         * automatically calculated from the scale size of the shapelet,
         * going out to 5 sigma.
         *
         * Default destructor, copy constructor and op= do the right thing.
         * The copy and op= are shallow copies.
         *
         * FIXME: This should really take a Wcs::ConstPtr, but none is defined.
         *
         * The Wcs information is needed because the natural reference frame for 
         * describing (and especially interpolating) the Psf is usually in world 
         * coordinates rather than chip coordinates.
         * So the dimensional units in the shapelet function are arcsec.  This is 
         * converted to pixels when constructing an image.
         */
        LocalShapeletKernel(
            Shapelet::ConstPtr shapelet, ///< A shapelet function that defines the kernel
            Wcs::Ptr wcs,       ///< The Wcs information for the image
            int width=0,        ///< width of Kernel image if you want to specify something particular.
            int height=0        ///< height of Kernel image if you want to specify something particular.
        );

        /*!
         * \brief Make an image of the kernel.
         *
         * computeImage can be done more efficiently than the AnalyticKernel 
         * version from KernelFunction.
         *
         * x and y are only present for compatibility with the Kernel version.
         * They are not used for anything.
         */
        double computeImage(
            Image& image,       ///< image whose pixels are to be set (output)
            bool doNormalize,   ///< normalize the image (so sum is 1)?
            double x = 0.0,     ///< ignored
            double y = 0.0      ///< ignored
        ) const;

    private :

        Shapelet::ConstPtr _shapelet;
        Wcs::Ptr _wcs;
    };


    class ShapeletKernel : public lsst::afw::math::AnalyticKernel
    {
        /* \brief ShapeletKernel includes spatial variation
         *
         * A ShapeletKernel is basically a function that can return
         * a LocalShapeletKernel for any location on an Image.
         * This is the most efficient way to use this class.
         * Namely to get the LocalShapeletKernel appropriate for a 
         * particular galaxy, and then convolve with that over a 
         * small patch around the galaxy.  
         *
         * Convolving a larger image is probably not efficient the
         * way that AnalyticKernel seems to be implemented.  AnalyticKernel
         * defines a number of functions that seem to be used for 
         * convolution, like determining the parameters of the 
         * analytic function separately.  The interpolation is faster
         * to calculate the full local function as a complete vector
         * rather than one component at a time.
         */
    public :
        typedef boost::shared_ptr<ShapeletKernel> Ptr;
        typedef boost::shared_ptr<const ShapeletKernel> ConstPtr;

        typedef lsst::afw::math::AnalyticKernel base;
        typedef lsst::afw::geom::PointD PointD;
        typedef lsst::afw::image::Image<double> Image;
        typedef lsst::afw::image::Wcs Wcs;

        /*!
         * \brief Constructor from a ShapeletInterpolation
         *
         * If the width and height are either 0 or omitted, then they are
         * automatically calculated from the scale size of the shapelet,
         * going out to 5 sigma.
         *
         * The default destructor, copy constructor and op= do the right thing.
         * The copy and op= are shallow (shared) copies.
         *
         * The Wcs information is needed because the natural reference frame for 
         * describing (and especially interpolating) the Psf is usually in world 
         * coordinates rather than chip coordinates.
         * So the dimensional units in the shapelet function are arcsec.  This is 
         * converted to pixels when constructing an image.
         */
        ShapeletKernel(
            ShapeletInterpolation::ConstPtr interp,  // An interpolating function for shapelets
            Wcs::Ptr wcs,       ///< The Wcs information for the image
            int width=0,        ///< width of Kernel image if you want to specify something particular.
            int height=0        ///< height of Kernel image if you want to specify something particular.
        );

        /*!
         * \brief Get the LocalShapeletKernel at a given point.
         *
         * pos is given in chip coordinates (i.e. units are pixels).
         */
        LocalShapeletKernel::ConstPtr getLocalKernel(
            const PointD& pos   ///< the position to interpolate to
        ) const;

        /*!
         * \brief Make an image of the kernel at a specified location.
         *
         * computeImage can be done more efficiently than the AnalyticKernel 
         * version from KernelFunction.
         *
         * This is equivalent to:
         * getLocalKernel(PointD(x,y))->computeImage(image,doNormalize);
         */
        double computeImage(
            Image& image,       ///< image whose pixels are to be set (output)
            bool doNormalize,   ///< normalize the image (so sum is 1)?
            double x = 0.0,     ///< the x component of the position to interpolate to
            double y = 0.0      ///< the y component of the position to interpolate to
        ) const;

    private :

        ShapeletInterpolation::ConstPtr _interp;
        Wcs::Ptr _wcs;
    };

}}}


#endif