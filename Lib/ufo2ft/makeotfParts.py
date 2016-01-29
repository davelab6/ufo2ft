from __future__ import print_function, division, absolute_import, unicode_literals

import os
import re
import tempfile

from fontTools.feaLib.builder import addOpenTypeFeatures
from fontTools import mtiLib


class FeatureOTFCompiler(object):
    """Generates OpenType feature tables for a UFO.

    If mtiFeaFiles is passed to the constructor, it should be a dictionary
    mapping feature table tags to source files which should be compiled by
    mtiLib into that respective table.
    """

    def __init__(self, font, outline, kernWriter, markWriter, mtiFeaFiles=None):
        self.font = font
        self.outline = outline
        self.kernWriter = kernWriter
        self.markWriter = markWriter
        self.mtiFeaFiles = mtiFeaFiles
        self.setupAnchorPairs()
        self.setupAliases()

    def compile(self):
        """Compile the features.

        Starts by generating feature syntax for the kern, mark, and mkmk
        features. If they already exist, they will not be overwritten unless
        the compiler's `overwriteFeatures` attribute is True.
        """

        self.precompile()
        self.setupFile_features()
        self.setupFile_featureTables()

        # only after compiling features can usMaxContext be calculated
        self.font['OS/2'].usMaxContext = maxCtxFont(self.font)

    def precompile(self):
        """Set any attributes needed before compilation.

        **This should not be called externally.** Subclasses
        may override this method if desired.
        """

        self.overwriteFeatures = False

    def setupFile_features(self):
        """
        Make the features source file. If any tables
        or the kern feature are defined in the font's
        features, they will not be overwritten.

        **This should not be called externally.** Subclasses
        may override this method to handle the file creation
        in a different way if desired.
        """

        if self.mtiFeaFiles is not None:
            return

        kernRE = r"feature\s+kern\s+{.*?}\s+kern\s*;"
        markRE = re.compile(kernRE.replace("kern", "mark"), re.DOTALL)
        mkmkRE = re.compile(kernRE.replace("kern", "mkmk"), re.DOTALL)
        kernRE = re.compile(kernRE, re.DOTALL)

        existing = self.font.features.text or ""

        # build the GPOS features as necessary
        autoFeatures = {}
        if self.overwriteFeatures or not kernRE.search(existing):
            autoFeatures["kern"] = self.writeFeatures_kern()
        if self.overwriteFeatures or not markRE.search(existing):
            autoFeatures["mark"] = self.writeFeatures_mark()
        if self.overwriteFeatures or not mkmkRE.search(existing):
            autoFeatures["mkmk"] = self.writeFeatures_mkmk()

        if self.overwriteFeatures:
            existing = kernRE.sub("", markRE.sub("", mkmkRE.sub("", existing)))

        # write the features
        features = [existing]
        for name, text in sorted(autoFeatures.items()):
            features.append(text)
        self.features = "\n\n".join(features)

    def writeFeatures_kern(self):
        """
        Write the kern feature to a string and return it.

        **This should not be called externally.** Subclasses
        may override this method to handle the string creation
        in a different way if desired.
        """
        writer = self.kernWriter(self.font)
        return writer.write()

    def writeFeatures_mark(self):
        """
        Write the mark feature to a string and return it.

        **This should not be called externally.** Subclasses
        may override this method to handle the string creation
        in a different way if desired.
        """
        writer = self.markWriter(self.font, self.anchorPairs,
                                 aliases=self.aliases)
        return writer.write()

    def writeFeatures_mkmk(self):
        """
        Write the mkmk feature to a string and return it.

        **This should not be called externally.** Subclasses
        may override this method to handle the string creation
        in a different way if desired.
        """
        writer = self.markWriter(self.font, self.mkmkAnchorPairs,
                                 aliases=self.aliases, mkmk=True)
        return writer.write()

    def setupAnchorPairs(self):
        """
        Try to determine the base-accent anchor pairs to use in building the
        mark and mkmk features.

        **This should not be called externally.** Subclasses
        may override this method to set up the anchor pairs
        in a different way if desired.
        """

        self.anchorPairs = []
        anchorNames = set()
        for glyph in self.font:
            for anchor in glyph.anchors:
                if anchor.name is None:
                    print("warning: unnamed anchor discarded in", glyph.name)
                    continue
                anchorNames.add(anchor.name)
        for baseName in sorted(anchorNames):
            accentName = "_" + baseName
            if accentName in anchorNames:
                self.anchorPairs.append((baseName, accentName))

        self.mkmkAnchorPairs = []

    def setupAliases(self):
        """
        Initialize an empty list of glyph aliases, which would be used in
        building the mark and mkmk features.

        **This should not be called externally.** Subclasses
        may override this method to set up the glyph aliases
        in a different way if desired.
        """

        self.aliases = ()

    def setupFile_featureTables(self):
        """
        Compile and return OpenType feature tables from the source.
        Raises a FeaLibError if the feature compilation was unsuccessful.

        **This should not be called externally.** Subclasses
        may override this method to handle the table compilation
        in a different way if desired.
        """

        if self.mtiFeaFiles is not None:
            for tag, feapath in self.mtiFeaFiles.items():
                with open(feapath) as feafile:
                    self.outline[tag] = mtiLib.build(feafile, self.outline)

        elif self.features.strip():
            if self.font.path is not None:
                self.features = forceAbsoluteIncludesInFeatures(self.features, self.font.path)
            fd, fea_path = tempfile.mkstemp()
            with open(fea_path, "w") as feafile:
                feafile.write(self.features)
            addOpenTypeFeatures(fea_path, self.outline)
            os.close(fd)
            os.remove(fea_path)

includeRE = re.compile(
    "(include\s*\(\s*)"
    "([^\)]+)"
    "(\s*\))" # this won't actually capture a trailing space.
    )


def forceAbsoluteIncludesInFeatures(text, directory):
    for match in reversed(list(includeRE.finditer(text))):
       start, includePath, close = match.groups()
       # absolute path
       if os.path.isabs(includePath):
           continue
       # relative path
       srcPath = os.path.normpath(os.path.join(directory, includePath.strip()))
       includeText = start + srcPath + close
       text = text[:match.start()] + includeText + text[match.end():]
    return text


def maxCtxFont(font):
    """Calculate the usMaxContext value for an entire font."""

    maxCtx = 0
    for tag in ('GSUB', 'GPOS'):
        if tag not in font:
            continue
        table = font[tag].table
        if table.LookupList is None:
            continue
        for lookup in table.LookupList.Lookup:
            for st in lookup.SubTable:
                maxCtx = maxCtxSubtable(maxCtx, tag, lookup.LookupType, st)
    return maxCtx


def maxCtxSubtable(maxCtx, tag, lookupType, st):
    """Calculate usMaxContext based on a single lookup table (and an existing
    max value).
    """

    #TODO don't consider backtrack context? back-chaining?

    # pair positioning
    if tag == 'GPOS' and lookupType == 2:
        ruleCount = 0
        if st.Format == 1:
            ruleCount = st.PairSetCount
        elif st.Format == 2:
            ruleCount = st.Class1Count * st.Class2Count
        if ruleCount > 0:
            maxCtx = max(maxCtx, 2)

    #TODO mark positioning
    elif tag == 'GPOS' and lookupType == 4:
        pass
    elif tag == 'GPOS' and lookupType == 5:
        for ligature in st.LigatureArray.LigatureAttach:
            maxCtx = max(maxCtx, 1 + ligature.ComponentCount)
    elif tag == 'GPOS' and lookupType == 6:
        pass

    # ligatures
    elif tag == 'GSUB' and lookupType == 4:
        for ligatures in st.ligatures.values():
            for ligature in ligatures:
                maxCtx = max(maxCtx, ligature.CompCount)

    # context
    elif (tag == 'GPOS' and lookupType == 7 or
          tag == 'GSUB' and lookupType == 5):
        maxCtx = maxCtxContextualSubtable(
            maxCtx, st, 'Pos' if tag == 'GPOS' else 'Sub')

    # chained context
    elif (tag == 'GPOS' and lookupType == 8 or
          tag == 'GSUB' and lookupType == 6):
        maxCtx = maxCtxContextualSubtable(
            maxCtx, st, 'Pos' if tag == 'GPOS' else 'Sub', 'Chain')

    # extensions
    elif (tag == 'GPOS' and lookupType == 9) or (
          tag == 'GSUB' and lookupType == 7):
        maxCtx = maxCtxSubtable(
            maxCtx, tag, st.ExtensionLookupType, st.ExtSubTable)

    # reverse-chained context
    elif tag == 'GSUB' and lookupType == 8:
        maxCtx = maxCtxContextualRule(maxCtx, st, True)

    return maxCtx


def maxCtxContextualSubtable(maxCtx, st, ruleType, chain=''):
    """Calculate usMaxContext based on a contextual feature subtable."""

    if st.Format == 1:
        for ruleset in getattr(st, '%s%sRuleSet' % (chain, ruleType)):
            if ruleset is None:
                continue
            for rule in getattr(ruleset, '%s%sRule' % (chain, ruleType)):
                if rule is None:
                    continue
                maxCtx = maxCtxContextualRule(maxCtx, rule, chain)

    elif st.Format == 2:
        for ruleset in getattr(st, '%s%sClassSet' % (chain, ruleType)):
            if ruleset is None:
                continue
            for rule in getattr(ruleset, '%s%sClassRule' % (chain, ruleType)):
                if rule is None:
                    continue
                maxCtx = maxCtxContextualRule(maxCtx, rule, chain)

    elif st.Format == 3:
        maxCtx = maxCtxContextualRule(maxCtx, st, chain)

    return maxCtx


def maxCtxContextualRule(maxCtx, st, chain):
    """Calculate usMaxContext based on a contextual feature rule."""

    if not chain:
        return max(maxCtx, st.GlyphCount)

    inputCount = 1
    if hasattr(st, 'InputGlyphCount'):
        inputCount = st.InputGlyphCount
    return max(maxCtx, inputCount + st.LookAheadGlyphCount,
               inputCount + st.BacktrackGlyphCount)
