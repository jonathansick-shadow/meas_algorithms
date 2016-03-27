#
# LSST Data Management System
# Copyright 2008-2015 AURA/LSST.
#
# This product includes software developed by the
# LSST Project (http://www.lsst.org/).
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the LSST License Statement and
# the GNU General Public License along with this program.  If not,
# see <https://www.lsstcorp.org/LegalNotices/>.
#
import sys

import numpy
try:
    import matplotlib.pyplot as pyplot
    fig = None
except ImportError:
    pyplot = None

import lsst.pex.config as pexConfig
import lsst.pex.logging as pexLogging
import lsst.afw.display.ds9 as ds9
import lsst.afw.math as afwMath
import lsst.afw.geom as afwGeom
import lsst.afw.geom.ellipses as geomEllip
import lsst.afw.cameraGeom as cameraGeom
from . import algorithmsLib
from lsst.meas.algorithms.starSelectorRegistry import starSelectorRegistry


class ObjectSizeStarSelectorConfig(pexConfig.Config):
    fluxMin = pexConfig.Field(
        doc = "specify the minimum psfFlux for good Psf Candidates",
        dtype = float,
        default = 12500.0,
        #        minValue = 0.0,
        check = lambda x: x >= 0.0,
    )
    fluxMax = pexConfig.Field(
        doc = "specify the maximum psfFlux for good Psf Candidates (ignored if == 0)",
        dtype = float,
        default = 0.0,
        check = lambda x: x >= 0.0,
    )
    kernelSize = pexConfig.Field(
        doc = "size of the Psf kernel to create",
        dtype = int,
        default = 21,
    )
    borderWidth = pexConfig.Field(
        doc = "number of pixels to ignore around the edge of PSF candidate postage stamps",
        dtype = int,
        default = 0,
    )
    badFlags = pexConfig.ListField(
        doc = "List of flags which cause a source to be rejected as bad",
        dtype = str,
        default = ["base_PixelFlags_flag_edge",
                   "base_PixelFlags_flag_interpolatedCenter",
                   "base_PixelFlags_flag_saturatedCenter",
                   "base_PixelFlags_flag_crCenter",
                   "base_PixelFlags_flag_bad",
                   "base_PixelFlags_flag_interpolated",
                   ],
    )
    widthMin = pexConfig.Field(
        doc = "minimum width to include in histogram",
        dtype = float,
        default = 0.0,
        check = lambda x: x >= 0.0,
    )
    widthMax = pexConfig.Field(
        doc = "maximum width to include in histogram",
        dtype = float,
        default = 10.0,
        check = lambda x: x >= 0.0,
    )
    sourceFluxField = pexConfig.Field(
        doc = "Name of field in Source to use for flux measurement",
        dtype = str,
        default = "base_GaussianFlux_flux",
    )
    widthStdAllowed = pexConfig.Field(
        doc = "Standard deviation of width allowed to be interpreted as good stars",
        dtype = float,
        default = 0.15,
        check = lambda x: x >= 0.0,
    )
    nSigmaClip = pexConfig.Field(
        doc = "Keep objects within this many sigma of cluster 0's median",
        dtype = float,
        default = 2.0,
        check = lambda x: x >= 0.0,
    )

    def validate(self):
        pexConfig.Config.validate(self)
        if self.widthMin > self.widthMax:
            raise pexConfig.FieldValidationError("widthMin (%f) > widthMax (%f)"
                                                 % (self.widthMin, self.widthMax))


class EventHandler(object):
    """A class to handle key strokes with matplotlib displays"""

    def __init__(self, axes, xs, ys, x, y, frames=[0]):
        self.axes = axes
        self.xs = xs
        self.ys = ys
        self.x = x
        self.y = y
        self.frames = frames

        self.cid = self.axes.figure.canvas.mpl_connect('key_press_event', self)

    def __call__(self, ev):
        if ev.inaxes != self.axes:
            return

        if ev.key and ev.key in ("p"):
            dist = numpy.hypot(self.xs - ev.xdata, self.ys - ev.ydata)
            dist[numpy.where(numpy.isnan(dist))] = 1e30

            which = numpy.where(dist == min(dist))

            x = self.x[which][0]
            y = self.y[which][0]
            for frame in self.frames:
                ds9.pan(x, y, frame=frame)
            ds9.cmdBuffer.flush()
        else:
            pass

#-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-


def _assignClusters(yvec, centers):
    """Return a vector of centerIds based on their distance to the centers"""
    assert len(centers) > 0

    minDist = numpy.nan*numpy.ones_like(yvec)
    clusterId = numpy.empty_like(yvec)
    clusterId.dtype = int               # zeros_like(..., dtype=int) isn't in numpy 1.5

    for i, mean in enumerate(centers):
        dist = abs(yvec - mean)
        if i == 0:
            update = dist == dist       # True for all points
        else:
            update = dist < minDist

        minDist[update] = dist[update]
        clusterId[update] = i

    return clusterId


def _kcenters(yvec, nCluster, useMedian=False, widthStdAllowed=0.15):
    """A classic k-means algorithm, clustering yvec into nCluster clusters

    Return the set of centres, and the cluster ID for each of the points

    If useMedian is true, use the median of the cluster as its centre, rather than
    the traditional mean

    Serge Monkewitz points out that there other (maybe smarter) ways of seeding the means:
       "e.g. why not use the Forgy or random partition initialization methods"
    however, the approach adopted here seems to work well for the particular sorts of things
    we're clustering in this application
    """

    assert nCluster > 0

    mean0 = sorted(yvec)[len(yvec)//10]  # guess
    delta = mean0 * widthStdAllowed * 2.0
    centers = mean0 + delta * numpy.arange(nCluster)

    func = numpy.median if useMedian else numpy.mean

    clusterId = numpy.zeros_like(yvec) - 1            # which cluster the points are assigned to
    clusterId.dtype = int                             # zeros_like(..., dtype=int) isn't in numpy 1.5
    while True:
        oclusterId = clusterId
        clusterId = _assignClusters(yvec, centers)

        if numpy.all(clusterId == oclusterId):
            break

        for i in range(nCluster):
            # Only compute func if some points are available; otherwise, default to NaN.
            pointsInCluster = (clusterId == i)
            if numpy.any(pointsInCluster):
                centers[i] = func(yvec[pointsInCluster])
            else:
                centers[i] = numpy.nan

    return centers, clusterId


def _improveCluster(yvec, centers, clusterId, nsigma=2.0, nIteration=10, clusterNum=0, widthStdAllowed=0.15):
    """Improve our estimate of one of the clusters (clusterNum) by sigma-clipping around its median"""

    nMember = sum(clusterId == clusterNum)
    if nMember < 5:  # can't compute meaningful interquartile range, so no chance of improvement
        return clusterId
    for iter in range(nIteration):
        old_nMember = nMember

        inCluster0 = clusterId == clusterNum
        yv = yvec[inCluster0]

        centers[clusterNum] = numpy.median(yv)
        stdev = numpy.std(yv)

        syv = sorted(yv)
        stdev_iqr = 0.741*(syv[int(0.75*nMember)] - syv[int(0.25*nMember)])
        median = syv[int(0.5*nMember)]

        sd = stdev if stdev < stdev_iqr else stdev_iqr

        if False:
            print "sigma(iqr) = %.3f, sigma = %.3f" % (stdev_iqr, numpy.std(yv))
        newCluster0 = abs(yvec - centers[clusterNum]) < nsigma*sd
        clusterId[numpy.logical_and(inCluster0, newCluster0)] = clusterNum
        clusterId[numpy.logical_and(inCluster0, numpy.logical_not(newCluster0))] = -1

        nMember = sum(clusterId == clusterNum)
        # 'sd < widthStdAllowed * median' prevents too much rejections
        if nMember == old_nMember or sd < widthStdAllowed * median:
            break

    return clusterId


def plot(mag, width, centers, clusterId, marker="o", markersize=2, markeredgewidth=0, ltype='-',
         magType="model", clear=True):

    global fig
    if not fig:
        fig = pyplot.figure()
    else:
        if clear:
            fig.clf()

    axes = fig.add_axes((0.1, 0.1, 0.85, 0.80))

    xmin = sorted(mag)[int(0.05*len(mag))]
    xmax = sorted(mag)[int(0.95*len(mag))]

    axes.set_xlim(-17.5, -13)
    axes.set_xlim(xmin - 0.1*(xmax - xmin), xmax + 0.1*(xmax - xmin))
    axes.set_ylim(0, 10)

    colors = ["r", "g", "b", "c", "m", "k", ]
    for k, mean in enumerate(centers):
        if k == 0:
            axes.plot(axes.get_xlim(), (mean, mean,), "k%s" % ltype)

        l = (clusterId == k)
        axes.plot(mag[l], width[l], marker, markersize=markersize, markeredgewidth=markeredgewidth,
                  color=colors[k%len(colors)])

    l = (clusterId == -1)
    axes.plot(mag[l], width[l], marker, markersize=markersize, markeredgewidth=markeredgewidth,
              color='k')

    if clear:
        axes.set_xlabel("Instrumental %s mag" % magType)
        axes.set_ylabel(r"$\sqrt{(I_{xx} + I_{yy})/2}$")

    return fig

#-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-


class ObjectSizeStarSelector(object):
    """!
    A measurePsfTask star selector
    """
    ConfigClass = ObjectSizeStarSelectorConfig
    usesMatches = False  # selectStars does not use its matches argument

    def __init__(self, config):
        """!
        Construct a star selector that looks for a cluster of small objects in a size-magnitude plot.

        \param[in] config An instance of ObjectSizeStarSelectorConfig
        """
        self._kernelSize = config.kernelSize
        self._borderWidth = config.borderWidth
        self._widthMin = config.widthMin
        self._widthMax = config.widthMax
        self._fluxMin = config.fluxMin
        self._fluxMax = config.fluxMax
        self._badFlags = config.badFlags
        self._sourceFluxField = config.sourceFluxField
        self._widthStdAllowed = config.widthStdAllowed
        self._nSigmaClip = config.nSigmaClip

    def selectStars(self, exposure, sourceCat, matches=None):
        """!Return a list of PSF candidates that represent likely stars

        A list of PSF candidates may be used by a PSF fitter to construct a PSF.

        \param[in] exposure  the exposure containing the sources
        \param[in] sourceCat  catalog of sources that may be stars (an lsst.afw.table.SourceCatalog)
        \param[in] matches  astrometric matches; ignored by this star selector

        \return psfCandidateList a list of PSF candidates.
        """
        import lsstDebug
        display = lsstDebug.Info(__name__).display
        displayExposure = lsstDebug.Info(__name__).displayExposure     # display the Exposure + spatialCells
        plotMagSize = lsstDebug.Info(__name__).plotMagSize             # display the magnitude-size relation
        dumpData = lsstDebug.Info(__name__).dumpData                   # dump data to pickle file?

        # create a log for my application
        logger = pexLogging.Log(pexLogging.getDefaultLog(), "meas.algorithms.objectSizeStarSelector")

        detector = exposure.getDetector()
        pixToTanXYTransform = None
        if detector is not None:
            tanSys = detector.makeCameraSys(cameraGeom.TAN_PIXELS)
            pixToTanXYTransform = detector.getTransformMap().get(tanSys)
        #
        # Look at the distribution of stars in the magnitude-size plane
        #
        flux = sourceCat.get(self._sourceFluxField)

        xx = numpy.empty(len(sourceCat))
        xy = numpy.empty_like(xx)
        yy = numpy.empty_like(xx)
        for i, source in enumerate(sourceCat):
            Ixx, Ixy, Iyy = source.getIxx(), source.getIxy(), source.getIyy()
            if pixToTanXYTransform:
                p = afwGeom.Point2D(source.getX(), source.getY())
                linTransform = pixToTanXYTransform.linearizeForwardTransform(p).getLinear()
                m = geomEllip.Quadrupole(Ixx, Iyy, Ixy)
                m.transform(linTransform)
                Ixx, Iyy, Ixy = m.getIxx(), m.getIyy(), m.getIxy()

            xx[i], xy[i], yy[i] = Ixx, Ixy, Iyy

        width = numpy.sqrt(0.5*(xx + yy))

        badFlags = self._badFlags

        bad = reduce(lambda x, y: numpy.logical_or(x, sourceCat.get(y)), badFlags, False)
        bad = numpy.logical_or(bad, flux < self._fluxMin)
        bad = numpy.logical_or(bad, numpy.logical_not(numpy.isfinite(width)))
        bad = numpy.logical_or(bad, numpy.logical_not(numpy.isfinite(flux)))
        bad = numpy.logical_or(bad, width < self._widthMin)
        bad = numpy.logical_or(bad, width > self._widthMax)
        if self._fluxMax > 0:
            bad = numpy.logical_or(bad, flux > self._fluxMax)
        good = numpy.logical_not(bad)

        if not numpy.any(good):
            raise RuntimeError("No objects passed our cuts for consideration as psf stars")

        mag = -2.5*numpy.log10(flux[good])
        width = width[good]
        #
        # Look for the maximum in the size histogram, then search upwards for the minimum that separates
        # the initial peak (of, we presume, stars) from the galaxies
        #
        if dumpData:
            import os
            import cPickle as pickle
            _ii = 0
            while True:
                pickleFile = os.path.expanduser(os.path.join("~", "widths-%d.pkl" % _ii))
                if not os.path.exists(pickleFile):
                    break
                _ii += 1

            with open(pickleFile, "wb") as fd:
                pickle.dump(mag, fd, -1)
                pickle.dump(width, fd, -1)

        centers, clusterId = _kcenters(width, nCluster=4, useMedian=True,
                                       widthStdAllowed=self._widthStdAllowed)

        if display and plotMagSize and pyplot:
            fig = plot(mag, width, centers, clusterId, magType=self._sourceFluxField.split(".")[-1].title(),
                       marker="+", markersize=3, markeredgewidth=None, ltype=':', clear=True)
        else:
            fig = None

        clusterId = _improveCluster(width, centers, clusterId,
                                    nsigma = self._nSigmaClip, widthStdAllowed=self._widthStdAllowed)

        if display and plotMagSize and pyplot:
            plot(mag, width, centers, clusterId, marker="x", markersize=3, markeredgewidth=None, clear=False)

        stellar = (clusterId == 0)
        #
        # We know enough to plot, if so requested
        #
        frame = 0

        if fig:
            if display and displayExposure:
                ds9.mtv(exposure.getMaskedImage(), frame=frame, title="PSF candidates")

                global eventHandler
                eventHandler = EventHandler(fig.get_axes()[0], mag, width,
                                            sourceCat.getX()[good], sourceCat.getY()[good], frames=[frame])

            fig.show()

            #-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-

            while True:
                try:
                    reply = raw_input("continue? [c h(elp) q(uit) p(db)] ").strip()
                except EOFError:
                    reply = None
                if not reply:
                    reply = "c"

                if reply:
                    if reply[0] == "h":
                        print """\
    We cluster the points; red are the stellar candidates and the other colours are other clusters.
    Points labelled + are rejects from the cluster (only for cluster 0).

    At this prompt, you can continue with almost any key; 'p' enters pdb, and 'h' prints this text

    If displayExposure is true, you can put the cursor on a point and hit 'p' to see it in ds9.
    """
                    elif reply[0] == "p":
                        import pdb
                        pdb.set_trace()
                    elif reply[0] == 'q':
                        sys.exit(1)
                    else:
                        break

        if display and displayExposure:
            mi = exposure.getMaskedImage()

            with ds9.Buffering():
                for i, source in enumerate(sourceCat):
                    if good[i]:
                        ctype = ds9.GREEN  # star candidate
                    else:
                        ctype = ds9.RED  # not star

                    ds9.dot("+", source.getX() - mi.getX0(),
                            source.getY() - mi.getY0(), frame=frame, ctype=ctype)
        #
        # Time to use that stellar classification to generate psfCandidateList
        #
        with ds9.Buffering():
            psfCandidateList = []
            for isStellar, source in zip(stellar, [s for g, s in zip(good, sourceCat) if g]):
                if not isStellar:
                    continue

                try:
                    psfCandidate = algorithmsLib.makePsfCandidate(source, exposure)
                    # The setXXX methods are class static, but it's convenient to call them on
                    # an instance as we don't know Exposure's pixel type
                    # (and hence psfCandidate's exact type)
                    if psfCandidate.getWidth() == 0:
                        psfCandidate.setBorderWidth(self._borderWidth)
                        psfCandidate.setWidth(self._kernelSize + 2*self._borderWidth)
                        psfCandidate.setHeight(self._kernelSize + 2*self._borderWidth)

                    im = psfCandidate.getMaskedImage().getImage()
                    vmax = afwMath.makeStatistics(im, afwMath.MAX).getValue()
                    if not numpy.isfinite(vmax):
                        continue
                    psfCandidateList.append(psfCandidate)

                    if display and displayExposure:
                        ds9.dot("o", source.getX() - mi.getX0(), source.getY() - mi.getY0(),
                                size=4, frame=frame, ctype=ds9.CYAN)
                except Exception as err:
                    logger.logdebug("Failed to make a psfCandidate from source %d: %s" %
                                    (source.getId(), err))

        return psfCandidateList

starSelectorRegistry.register("objectSize", ObjectSizeStarSelector)
