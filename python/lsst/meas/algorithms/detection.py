# 
# LSST Data Management System
# Copyright 2008, 2009, 2010, 2011 LSST Corporation.
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
# see <http://www.lsstcorp.org/LegalNotices/>.
#
import numpy

import lsstDebug
import lsst.pex.logging as pexLogging 

import lsst.pex.config as pexConfig
import lsst.afw.math as afwMath
import lsst.afw.table as afwTable
import lsst.afw.image as afwImage
import lsst.afw.geom as afwGeom
import lsst.afw.detection as afwDet
import lsst.pipe.base as pipeBase

from . import algorithmsLib

__all__ = ("SourceDetectionConfig", "SourceDetectionTask", "getBackground",
           "estimateBackground", "BackgroundConfig", "MakePsfConfig", "makePsf", "addExposures")

import lsst.daf.persistence as dafPersist
import lsst.pex.config as pexConfig
import lsst.afw.detection as afwDet
import lsst.afw.display.ds9 as ds9
import lsst.afw.geom as afwGeom
import lsst.afw.image as afwImage
import lsst.afw.math as afwMath

class SourceDetectionConfig(pexConfig.Config):
    minPixels = pexConfig.RangeField(
        doc="detected sources with fewer than the specified number of pixels will be ignored",
        dtype=int, optional=False, default=1, min=0,
    )
    isotropicGrow = pexConfig.Field(
        doc="How many pixels to to grow detections",
        dtype=bool, optional=False, default=False,
    )
    nGrow = pexConfig.RangeField(
        doc="How many pixels to to grow detections",
        dtype=int, optional=False, default=1, min=0,
    )
    returnOriginalFootprints = pexConfig.Field(
        doc="Grow detections to set the image mask bits, but return the original (not-grown) footprints",
        dtype=bool, optional=False, default=True    # TODO: set default to False once we have a deblender
    )
    thresholdValue = pexConfig.RangeField(
        doc="Threshold for footprints",
        dtype=float, optional=False, default=5.0, min=0.0,
    )
    includeThresholdMultiplier = pexConfig.RangeField(
        doc="Include threshold relative to thresholdValue",
        dtype=float, default=1.0, min=0.0,
        )        
    thresholdType = pexConfig.ChoiceField(
        doc="specifies the desired flavor of Threshold",
        dtype=str, optional=False, default="stdev",
        allowed={
            "variance": "threshold applied to image variance",
            "stdev": "threshold applied to image std deviation",
            "value": "threshold applied to image value"
        }
    )
    thresholdPolarity = pexConfig.ChoiceField(
        doc="specifies whether to detect positive, or negative sources, or both",
        dtype=str, optional=False, default="positive",
        allowed={
            "positive": "detect only positive sources",
            "negative": "detect only negative sources",
            "both": "detect both positive and negative sources",
        }
    )
    adjustBackground = pexConfig.Field(
        dtype = float,
        doc = "Fiddle factor to add to the background; debugging only",
        default = 0.0,
    )
    reEstimateBackground = pexConfig.Field(
        dtype = bool,
        doc = "Estimate the background again after final source detection?",
        default = True, optional=False,
    )

class SourceDetectionTask(pipeBase.Task):
    """
    Detect positive and negative sources on an exposure and return a new SourceCatalog.
    """
    ConfigClass = SourceDetectionConfig

    def __init__(self, schema=None, **kwds):
        """Create the detection task.  Most arguments are simply passed onto pipe_base.Task.

        If schema is not None, it will be used to register a 'flags.negative' flag field
        that will be set for negative detections.
        """
        pipeBase.Task.__init__(self, **kwds)
        if schema is not None:
            self.negativeFlagKey = schema.addField(
                "flags.negative", type="Flag",
                doc="set if source was detected as significantly negative"
                )
        else:
            if self.config.thresholdPolarity == "both":
                self.log.log(self.log.WARN, "Detection polarity set to 'both', but no flag will be "\
                             "set to distinguish between positive and negative detections")
            self.negativeFlagKey = None

    @pipeBase.timeMethod
    def makeSourceCatalog(self, table, exposure):
        """Run source detection and create a SourceCatalog.

        To avoid dealing with sources and tables, use detectFootprints() to just get the FootprintSets.

        @param table    lsst.afw.table.SourceTable object that will be used to created the SourceCatalog.
        @param exposure Exposure to process; DETECTED mask plane will be set in-place.
        
        @return an lsst.afw.table.SourceCatalog object
        """
        assert exposure, "No exposure provided"
        assert self.negativeFlagKey is None or self.negativeFlagKey in table.getSchema(), \
            "Table has incorrect Schema"
        fpSets = self.detectFootprints(exposure)
        sources = afwTable.SourceCatalog(table)
        table.preallocate(fpSets.numPos + fpSets.numNeg) # not required, but nice
        if fpSets.negative:
            fpSets.positive.makeSources(sources)
            if self.negativeFlagKey:
                for record in sources:
                    record.set(self.negativeFlagKey, True)
        if fpSets.positive:
            fpSets.positive.makeSources(sources)
        return sources

    @pipeBase.timeMethod
    def detectFootprints(self, exposure):
        """Detect footprints.

        @param exposure Exposure to process; DETECTED mask plane will be set in-place.

        @return a lsst.pipe.base.Struct with fields:
        - positive: lsst.afw.detection.FootprintSet with positive polarity footprints (may be None)
        - negative: lsst.afw.detection.FootprintSet with negative polarity footprints (may be None)
        - numPos: number of footprints in positive or 0 if detection polarity was negative
        - numNeg: number of footprints in negative or 0 if detection polarity was positive
        """
        try:
            import lsstDebug
            display = lsstDebug.Info(__name__).display
        except ImportError, e:
            try:
                display
            except NameError:
                display = False

        if exposure is None:
            raise RuntimeException("No exposure for detection")

        maskedImage = exposure.getMaskedImage()
        region = maskedImage.getBBox(afwImage.PARENT)

        mask = maskedImage.getMask()
        psf = exposure.getPsf()
        mask &= ~(mask.getPlaneBitMask("DETECTED") | mask.getPlaneBitMask("DETECTED_NEGATIVE"))
        del mask

        if psf is None:
            convolvedImage = maskedImage.Factory(maskedImage)
            middle = convolvedImage
        else:
            # use a separable psf for convolution ... the psf width for the center of the image will do

            xCen = maskedImage.getX0() + maskedImage.getWidth()/2
            yCen = maskedImage.getY0() + maskedImage.getHeight()/2

            # measure the 'sigma' of the psf we were given
            psfAttrib = algorithmsLib.PsfAttributes(psf, xCen, yCen)
            sigma = psfAttrib.computeGaussianWidth()

            # make a SingleGaussian (separable) kernel with the 'sigma'
            gaussFunc = afwMath.GaussianFunction1D(sigma)
            gaussKernel = afwMath.SeparableKernel(psf.getKernel().getWidth(), psf.getKernel().getHeight(),
                                                  gaussFunc, gaussFunc)

            convolvedImage = maskedImage.Factory(maskedImage.getDimensions())
            convolvedImage.setXY0(maskedImage.getXY0())

            afwMath.convolve(convolvedImage, maskedImage, gaussKernel, afwMath.ConvolutionControl())
            #
            # Only search psf-smooth part of frame
            #
            goodBBox = gaussKernel.shrinkBBox(convolvedImage.getBBox(afwImage.PARENT))
            middle = convolvedImage.Factory(convolvedImage, goodBBox, afwImage.PARENT, False)
            #
            # Mark the parts of the image outside goodBBox as EDGE
            #
            self.setEdgeBits(maskedImage, goodBBox, maskedImage.getMask().getPlaneBitMask("EDGE"))

        fpSets = pipeBase.Struct(positive=None, negative=None)

        if self.config.thresholdPolarity != "negative":
            fpSets.positive = self.thresholdImage(middle, "positive")
        if self.config.reEstimateBackground or self.config.thresholdPolarity != "positive":
            fpSets.negative = self.thresholdImage(middle, "negative")

        for polarity, maskName in (("positive", "DETECTED"), ("negative", "DETECTED_NEGATIVE")):
            fpSet = getattr(fpSets, polarity)
            if fpSet is None:
                continue
            fpSet.setRegion(region)
            if self.config.nGrow > 0:
                fpSet = afwDet.FootprintSet(fpSet, self.config.nGrow, False)
            fpSet.setMask(maskedImage.getMask(), maskName)
            if not self.config.returnOriginalFootprints:
                setattr(fpSets, polarity, fpSet)
            

        fpSets.numPos = len(fpSets.positive.getFootprints()) if fpSets.positive is not None else 0
        fpSets.numNeg = len(fpSets.negative.getFootprints()) if fpSets.negative is not None else 0

        self.log.log(self.log.INFO, "Detected %d positive sources to %g sigma." %
                     (fpSets.numPos, self.config.thresholdValue))

        if self.config.reEstimateBackground:
            backgroundConfig = BackgroundConfig()

            mi = exposure.getMaskedImage()
            bkgd = getBackground(mi, backgroundConfig)

            if self.config.adjustBackground:
                self.log.log(self.log.WARN, "Fiddling the background by %g" % self.config.adjustBackground)

                bkgd += self.config.adjustBackground

            self.log.log(self.log.INFO, "Resubtracting the background after object detection")
            mi -= bkgd.getImageF()
            del mi

        if self.config.thresholdPolarity == "positive":
            mask = maskedImage.getMask()
            mask &= ~mask.getPlaneBitMask("DETECTED_NEGATIVE")
            del mask
            fpSets.negative = None
        else:
            self.log.log(self.log.INFO, "Detected %d negative sources to %g sigma" %
                         (fpSets.numNeg, self.config.thresholdValue))

        if display:
            ds9.mtv(exposure, frame=0, title="detection")

            if convolvedImage and display and display > 1:
                ds9.mtv(convolvedImage, frame=1, title="PSF smoothed")

            if middle and display and display > 1:
                ds9.mtv(middle, frame=2, title="middle")

        return fpSets

    def thresholdImage(self, image, thresholdParity, maskName="DETECTED"):
        """Threshold the convolved image, returning a FootprintSet.
        Helper function for detect().

        @param image The (optionally convolved) MaskedImage to threshold
        @param thresholdParity Parity of threshold
        @param maskName Name of mask to set

        @return FootprintSet
        """
        parity = False if thresholdParity == "negative" else True
        threshold = afwDet.createThreshold(self.config.thresholdValue, self.config.thresholdType, parity)
        threshold.setIncludeMultiplier(self.config.includeThresholdMultiplier)
        fpSet = afwDet.FootprintSet(image, threshold, maskName, self.config.minPixels)
        return fpSet

    @staticmethod
    def setEdgeBits(maskedImage, goodBBox, edgeBitmask):
        """Set the edgeBitmask bits for all of maskedImage outside goodBBox"""
        msk = maskedImage.getMask()

        mx0, my0 = maskedImage.getXY0()
        for x0, y0, w, h in ([0, 0,
                              msk.getWidth(), goodBBox.getBeginY() - my0],
                             [0, goodBBox.getEndY() - my0, msk.getWidth(),
                              maskedImage.getHeight() - (goodBBox.getEndY() - my0)],
                             [0, 0,
                              goodBBox.getBeginX() - mx0, msk.getHeight()],
                             [goodBBox.getEndX() - mx0, 0,
                              maskedImage.getWidth() - (goodBBox.getEndX() - mx0), msk.getHeight()],
                             ):
            edgeMask = msk.Factory(msk, afwGeom.BoxI(afwGeom.PointI(x0, y0),
                                                     afwGeom.ExtentI(w, h)), afwImage.LOCAL)
            edgeMask |= edgeBitmask

class BackgroundConfig(pexConfig.Config):
    statisticsProperty = pexConfig.ChoiceField(
        doc="type of statistic to use for grid points",
        dtype=str, default="MEANCLIP",
        allowed={
            "MEANCLIP": "clipped mean",
            "MEAN": "unclipped mean",
            "MEDIAN": "median",
            }
        )
    undersampleStyle = pexConfig.ChoiceField(
        doc="behaviour if there are too few points in grid for requested interpolation style",
        dtype=str, default="THROW_EXCEPTION",
        allowed={
            "THROW_EXCEPTION": "throw an exception if there are too few points",
            "REDUCE_INTERP_ORDER": "use an interpolation style with a lower order.",
            "INCREASE_NXNYSAMPLE": "Increase the number of samples used to make the interpolation grid.",
            }
        )
    binSize = pexConfig.RangeField(
        doc="how large a region of the sky should be used for each background point",
        dtype=int, default=256, min=10
        )
    algorithm = pexConfig.ChoiceField(
        doc="how to interpolate the background values. This maps to an enum; see afw::math::Background",
        dtype=str, default="NATURAL_SPLINE", optional=True,
        allowed={
            "NATURAL_SPLINE" : "cubic spline with zero second derivative at endpoints",
            "AKIMA_SPLINE": "higher-level nonlinear spline that is more robust to outliers",
            "NONE": "No background estimation is to be attempted",
            }
        )
    ignoredPixelMask = pexConfig.ListField(
        doc="Names of mask planes to ignore while estimating the background",
        dtype=str, default = ["EDGE", "DETECTED", "DETECTED_NEGATIVE"],
        itemCheck = lambda x: x in afwImage.MaskU.getMaskPlaneDict().keys(),
        )

    def validate(self):
        pexConfig.Config.validate(self)
        # Allow None to be used as an equivalent for "NONE", even though C++ expects the latter.
        if self.algorithm is None:
            self.algorithm = "NONE"

class MakePsfConfig(pexConfig.Config):
    algorithm = pexConfig.Field( # this should probably be a registry
        dtype = str,
        doc = "name of the psf algorithm to use",
        default = "DoubleGaussian",
    )
    width = pexConfig.Field(
        dtype = int,
        doc = "specify the PSF's width (pixels)",
        default = 5,
        check = lambda x: x > 0,
    )
    height = pexConfig.Field(
        dtype = int,
        doc = "specify the PSF's height (pixels)",
        default = 5,
        check = lambda x: x > 0,
    )
    params = pexConfig.ListField(
        dtype = float,
        doc = "specify additional parameters as required for the algorithm" ,
        maxLength = 3,
        default = (1.0,),
    )

def makePsf(config):
    """Construct a Psf
    
    @param[in] config: an instance of MakePsfConfig
    
    A thin wrapper around lsst.afw.detection.createPsf
    
    @todo It would be better to use a registry, but this requires rewriting afwDet.createPsf
    """
    params = [
        config.algorithm,
        config.width,
        config.height,
    ]
    params += list(config.params)
        
    return afwDet.createPsf(*params)
makePsf.ConfigClass = MakePsfConfig

def addExposures(exposureList):
    """
    Add a set of exposures together. 
    Assumes that all exposures in set have the same dimensions
    """
    exposure0 = exposureList[0]
    image0 = exposure0.getMaskedImage()

    addedImage = image0.Factory(image0, True)
    addedImage.setXY0(image0.getXY0())

    for exposure in exposureList[1:]:
        image = exposure.getMaskedImage()
        addedImage += image

    addedExposure = exposure0.Factory(addedImage, exposure0.getWcs())
    return addedExposure

def getBackground(image, backgroundConfig):
    """
    Make a new Exposure which is exposure - background
    """
    backgroundConfig.validate();

    nx = image.getWidth()//backgroundConfig.binSize + 1
    ny = image.getHeight()//backgroundConfig.binSize + 1

    sctrl = afwMath.StatisticsControl()
    sctrl.setAndMask(reduce(lambda x, y: x | image.getMask().getPlaneBitMask(y),
                            backgroundConfig.ignoredPixelMask, 0x0))

    pl = pexLogging.Debug("meas.utils.sourceDetection.getBackground")
    pl.debug(3, "Ignoring mask planes: %s" % ", ".join(backgroundConfig.ignoredPixelMask))

    bctrl = afwMath.BackgroundControl(backgroundConfig.algorithm, nx, ny,
                                      backgroundConfig.undersampleStyle, sctrl,
                                      backgroundConfig.statisticsProperty)

    return afwMath.makeBackground(image, bctrl)

getBackground.ConfigClass = BackgroundConfig
    
def estimateBackground(exposure, backgroundConfig, subtract=True):
    """
    Estimate exposure's background using parameters in backgroundConfig.  
    If subtract is true, make a copy of the exposure and subtract the background.  
    Return background, backgroundSubtractedExposure
    """
    displayBackground = lsstDebug.Info(__name__).displayBackground

    maskedImage = exposure.getMaskedImage()

    background = getBackground(maskedImage, backgroundConfig)

    if not background:
        raise RuntimeError, "Unable to estimate background for exposure"
    
    if displayBackground > 1:
        ds9.mtv(background.getImageF(), title="background", frame=1)

    if not subtract:
        return background, None

    bbox = maskedImage.getBBox(afwImage.PARENT)
    backgroundSubtractedExposure = exposure.Factory(exposure, bbox, afwImage.PARENT, True)
    copyImage = backgroundSubtractedExposure.getMaskedImage().getImage()
    copyImage -= background.getImageF()

    if displayBackground:
        ds9.mtv(backgroundSubtractedExposure, title="subtracted")

    return background, backgroundSubtractedExposure
estimateBackground.ConfigClass = BackgroundConfig