// -*- LSST-C++ -*-
//
// test a perfect Gaussian PSF and measure aperture photometry at different radii
//
#include <iostream>
#include <limits>
#include <cmath>
#include "lsst/afw.h"
#include "lsst/meas/algorithms/Photometry.h"
#include "lsst/afw/math/Integrate.h"

#define BOOST_TEST_DYN_LINK
#define BOOST_TEST_MODULE Photometry

#include "boost/test/unit_test.hpp"
#include "boost/test/floating_point_comparison.hpp"

using namespace std;
namespace algorithms = lsst::meas::algorithms;
namespace image = lsst::afw::image;
namespace math = lsst::afw::math;

typedef image::MaskedImage<float, short unsigned int, float> MImage;

/* =====================================================================
 * a functor for the PSF
 */
class Gaussian: public std::binary_function<double, double, double> {
public:
    Gaussian(double const xcen, double const ycen, double const sigma, double const a) :
        _xcen(xcen), _ycen(ycen), _sigma(sigma), _a(a) {}
    double operator() (double const x, double const y) const {
        double const xx = x - _xcen;
        double const yy = y - _ycen;
        return _a * (1.0/(2.0*M_PI*_sigma*_sigma)) *
            std::exp( -(xx*xx + yy*yy) / (2.0*_sigma*_sigma)  );
    }
private:
    double const _xcen;
    double const _ycen;
    double const _sigma;
    double const _a;
};


/* =====================================================================
 * a radial functor for the PSF
 */
class RGaussian: public std::unary_function<double, double> {
public:
    RGaussian(double const sigma, double const a, double const apradius, double const aptaper) :
        _sigma(sigma), _a(a), _apradius(apradius), _aptaper(aptaper) {}
    double operator() (double const r) const {
        double const gauss = _a * (1.0/(2.0*M_PI*_sigma*_sigma)) *
            std::exp( -(r*r) / (2.0*_sigma*_sigma)  );
        double aperture;
        if ( r <= _apradius ) {
            aperture = 1.0;
        } else if ( r > _apradius && r < _apradius + _aptaper ) {
            aperture = 0.5*(1.0 + std::cos(M_PI*(r - _apradius)/_aptaper));
        } else {
            aperture = 0.0;
        }
        return aperture*gauss * (r * 2.0 * M_PI);
    }
private:
    double const _sigma;
    double const _a;
    double const _apradius;
    double const _aptaper;
};



/**
 * This test performs a crude comparison between a Sinc-integrated aperture flux for a perfect Gaussian
 *   and the theoretical analytic flux integrated over the same Gaussian and aperture.
 * The Sinc method is expected to be in error by a small amount as the Gaussian psf is
 *   not band-limited (a requirement of the method)
 * The code is an abbreviation of the example "examples/growthcurve.cc" 
 */

BOOST_AUTO_TEST_CASE(PhotometrySinc) {

    // select the radii to test
    std::vector<double> radius;
    double r1 = 3.0;
    double r2 = 4.0;
    double dr = 1.0;
    int nR = static_cast<int>( (r2 - r1)/dr + 1 );
    for (int iR = 0; iR < nR; iR++) {
        radius.push_back(r1 + iR*dr);
    }

    double expectedError = 2.0;  // in percent
    
    // make an image big enough to hold the largest requested aperture
    int const xwidth = 2*(0 + 128);
    int const ywidth = xwidth;
    
    std::vector<double> sigmas(2);
    sigmas[0] = 1.5;
    sigmas[1] = 2.5;
    int const nS = sigmas.size();
    double const a = 100.0;
    double const aptaper = 2.0;
    double const xcen = xwidth/2;
    double const ycen = ywidth/2;


    for (int iS = 0; iS < nS; ++iS) {

        double const sigma = sigmas[iS];

        Gaussian gpsf(xcen, ycen, sigma, a);

        // make a perfect Gaussian PSF in an image
        MImage const mimg(xwidth, ywidth);
        double xBcen = 0.0, yBcen = 0.0; // barycenters - crude centroids
        double fluxBarySum = 0.0;
        for (int iY = 0; iY != mimg.getHeight(); ++iY) {
            int iX = 0;
            for (MImage::x_iterator ptr = mimg.row_begin(iY), end = mimg.row_end(iY);
                 ptr != end; ++ptr, ++iX) {
                double const flux = gpsf(iX, iY);
                ptr.image() = flux;
                if (flux > 0.01) {
                    xBcen += flux*iX;
                    yBcen += flux*iY;
                    fluxBarySum += flux;
                }
            }
        }
        xBcen /= fluxBarySum;
        yBcen /= fluxBarySum;
        
        for (int iR = 0; iR < nR; iR++) {

            double const psfH = 2.0*(r2 + 2.0);
            double const psfW = 2.0*(r2 + 2.0);

            algorithms::PSF::Ptr psf = algorithms::createPSF("DoubleGaussian", psfW, psfH, sigma);
            
            // get the Sinc aperture flux
            algorithms::MeasurePhotometry<MImage> const *mpSinc =
                algorithms::createMeasurePhotometry<MImage>("SINC", radius[iR]);
            algorithms::Photometry photSinc = mpSinc->apply(mimg, xcen, ycen, psf.get(), 0.0);
            double const fluxSinc = photSinc.getApFlux();
            
            // get the exact flux for the theoretical smooth PSF
            RGaussian rpsf(sigma, a, radius[iR], aptaper);
            double const fluxInt = math::integrate(rpsf, 0, radius[iR] + aptaper, 1.0e-8);

            BOOST_CHECK_CLOSE(fluxSinc, fluxInt, expectedError);

        }
    }
}
    
