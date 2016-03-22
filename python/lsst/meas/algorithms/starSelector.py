# 
# LSST Data Management System
# Copyright 2008, 2009, 2010 LSST Corporation.
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
from __future__ import absolute_import, division, print_function

import abc

import numpy as np

from lsst.afw.table import SourceCatalog
import lsst.afw.math as afwMath
import lsst.pex.config as pexConfig
import lsst.pipe.base as pipeBase
from . import algorithmsLib

__all__ = ["StarSelectorConfig", "StarSelectorTask"]

class StarSelectorConfig(pexConfig.Config):
    kernelSize = pexConfig.Field(
        doc = "size of the kernel to create",
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
        default = [
            "base_PixelFlags_flag_edge",
            "base_PixelFlags_flag_interpolatedCenter",
            "base_PixelFlags_flag_saturatedCenter",
            "base_PixelFlags_flag_crCenter",
            "base_PixelFlags_flag_bad",
            "base_PixelFlags_flag_interpolated",
        ],
    )


class StarSelectorTask(pipeBase.Task):
    """!Base class for star selectors
    """
    __metaclass__ = abc.ABCMeta

    usesMatches = False # 
    ConfigClass = StarSelectorConfig
    _DefaultName = "starSelector"

    def run(self, exposure, sourceCat, matches=None, isStarField=None):
        """!Select stars, make PSF candidates, and set a flag field True for stars in the input catalog

        @param[in] exposure  the exposure containing the sources
        @param[in] sourceCat  catalog of sources that may be stars (an lsst.afw.table.SourceCatalog)
        @param[in] matches  astrometric matches; ignored by this star selector
                (an lsst.afw.table.ReferenceMatchVector), or None. Some star selectors
                will ignore this argument, others may require it. See the usesMatches class variable.
        @param[in] isStarField  name of flag field to set True for stars, or None to not set a field;
            the field is left unchanged for non-stars

        @return an lsst.pipe.base.Struct containing:
        - starCat  catalog of stars that were selected as stars and successfuly made into PSF candidates
                    (a subset of sourceCat)
        - psfCandidates  list of PSF candidates (lsst.meas.algorithms.PsfCandidate)
        """
        selRes = self.selectStars(exposure=exposure, sourceCat=sourceCat, matches=matches)
        psfRes = self.makePsfCandidates(exposure=exposure, starCat=selRes.starCat)

        if isStarField is not None:
            isStarKey = sourceCat.schema[isStarField].asKey()
            for star in psfRes.goodStarCat:
                star.set(isStarKey, True)

        return pipeBase.Struct(
            starCat = psfRes.goodStarCat,
            psfCandidates = psfRes.psfCandidates,
        )

    @abc.abstractmethod
    def selectStars(self, exposure, sourceCat, matches=None):
        """!Return a catalog of stars: a subset of sourceCat
        
        A list of PSF candidates may be used by a PSF fitter to construct a PSF.
        
        @param[in] exposure  the exposure containing the sources
        @param[in] sourceCat  catalog of sources that may be stars (an lsst.afw.table.SourceCatalog)
        @param[in] matches  astrometric matches; ignored by this star selector
                (an lsst.afw.table.ReferenceMatchVector), or None. Some star selectors
                will ignore this argument, others may require it. See the usesMatches class variable.
        
        @return a pipeBase.Struct containing:
        - starCat  a catalog of stars
        """
        raise NotImplementedError("StarSelectorTask is abstract, subclasses must override this method")

    def makePsfCandidates(self, exposure, starCat):
        """!Make a list of PSF candidates from a star catalog

        @param[in] exposure  the exposure containing the sources
        @param[in] starCat  catalog of stars (an lsst.afw.table.SourceCatalog),
                            e.g. as returned by the run or selectStars method

        @return an lsst.pipe.base.Struct with fields:
        - psfCandidates  list of PSF candidates (lsst.meas.algorithms.PsfCandidate)
        - goodStarCat  catalog of stars that were successfully made into PSF candidates (a subset of starCat)
        """
        goodStarCat = SourceCatalog(starCat.schema)

        psfCandidateList = []
        for star in starCat:
            try:
                psfCandidate = algorithmsLib.makePsfCandidate(star, exposure)
                
                # The setXXX methods are class static, but it's convenient to call them on
                # an instance as we don't know Exposure's pixel type
                # (and hence psfCandidate's exact type)
                if psfCandidate.getWidth() == 0:
                    psfCandidate.setBorderWidth(self.config.borderWidth)
                    psfCandidate.setWidth(self.config.kernelSize + 2*self.config.borderWidth)
                    psfCandidate.setHeight(self.config.kernelSize + 2*self.config.borderWidth)

                im = psfCandidate.getMaskedImage().getImage()
                vmax = afwMath.makeStatistics(im, afwMath.MAX).getValue()
                if not np.isfinite(vmax):
                    continue
                psfCandidateList.append(psfCandidate)
                goodStarCat.append(star)
            except Exception as err:
                self.log.warn("Failed to make a psfCandidate from star %d: %s" % (star.getId(), err))

        return pipeBase.Struct(
            psfCandidates = psfCandidateList,
            goodStarCat = goodStarCat,
        )
