"""The ``org.opencv`` surface ``activities/coc_slider_android.py`` calls.

Android links the native OpenCV 4 library. iOS is built without it, so the few
primitives the slider recognizer touches are provided here on top of
``iosbridge.imaging``. Names and argument order mirror the Java API exactly, so
``coc_slider_android.py`` runs unchanged.

The data flow is the Java one, not a shortcut:

    imdecode   -> Mat holding an RGB image
    cvtColor   -> Mat holding an 8-bit gray grid
    GaussianBlur / Canny -> Mat holding an 8-bit single-channel grid
    matchTemplate -> Mat holding the float32 score surface
    minMaxLoc  -> the location of the maximum

Every intermediate stays inside ``Mat`` exactly as in OpenCV, so a mistake in
the pipeline shows up in the same place it would on Android.
"""

from __future__ import annotations

import base64

from . import imaging


class CvException(Exception):
    """Stands in for ``org.opencv.core.CvException``."""


class _Point:
    """``org.opencv.core.Point``."""
    __slots__ = ("x", "y")

    def __init__(self, x=0.0, y=0.0):
        self.x = x
        self.y = y


class _MinMax:
    """``org.opencv.core.MinMaxLocResult``."""
    __slots__ = ("minVal", "maxVal", "minLoc", "maxLoc")

    def __init__(self, min_val=0.0, max_val=0.0, min_loc=None, max_loc=None):
        self.minVal, self.maxVal = min_val, max_val
        self.minLoc = min_loc or _Point()
        self.maxLoc = max_loc or _Point()


class Mat:
    """``org.opencv.core.Mat``: decoded image, single-channel grid, or score map.

    ``kind`` records what the matrix currently holds, which keeps the Java
    contract (two Mats of a given kind are interchangeable) without guessing
    from the contents.
    """

    def __init__(self, kind: str = "empty", payload=None, width: int = 0, height: int = 0):
        self.kind = kind          # "empty" | "bgr" | "gray" | "score"
        self.payload = payload    # PIL image for "bgr", list[list[int]] for grids
        self.width = width
        self.height = height
        self.released = False

    def empty(self) -> bool:
        return self.kind == "empty" or self.payload is None

    def release(self) -> None:
        self.kind = "empty"
        self.payload = None
        self.released = True

    def __repr__(self) -> str:  # keeps assertion messages readable
        return f"Mat({self.kind}, {self.width}x{self.height})"


class MatOfByte:
    """``org.opencv.core.MatOfByte``: the encoded image bytes."""

    def __init__(self, values=None):
        self.values = bytes(values or b"")

    def empty(self) -> bool:
        return not self.values

    def release(self) -> None:
        self.values = b""


class Size:
    def __init__(self, width, height):
        self.width = width
        self.height = height


class Core:
    """``org.opencv.core.Core``."""

    @staticmethod
    def minMaxLoc(source: Mat) -> _MinMax:
        grid = source.payload if source.kind == "score" else None
        if not grid:
            return _MinMax()
        min_value = max_value = None
        min_at = max_at = _Point(0, 0)
        for y, row in enumerate(grid):
            for x, value in enumerate(row):
                if max_value is None or value > max_value:
                    max_value, max_at = value, _Point(float(x), float(y))
                if min_value is None or value < min_value:
                    min_value, min_at = value, _Point(float(x), float(y))
        return _MinMax(float(min_value), float(max_value), min_at, max_at)


class Imgcodecs:
    """``org.opencv.imgcodecs.Imgcodecs``."""

    IMREAD_COLOR = 1

    @staticmethod
    def imdecode(encoded: MatOfByte, flags: int) -> Mat:
        if flags != Imgcodecs.IMREAD_COLOR:
            raise CvException("only IMREAD_COLOR is implemented")
        if encoded.empty():
            raise CvException("empty input buffer")
        try:
            payload = base64.b64encode(encoded.values).decode("ascii")
            image = imaging.decode(payload)
        except imaging.ImagingError as error:
            # OpenCV returns an empty Mat for undecodable input; the caller then
            # raises the business error, which is the Android control flow.
            return Mat()
        return Mat("bgr", image, image.size[0], image.size[1])


class Imgproc:
    """``org.opencv.imgproc.Imgproc``."""

    COLOR_BGR2GRAY = 6
    TM_CCORR_NORMED = 3

    @staticmethod
    def cvtColor(source: Mat, target: Mat, code: int) -> None:
        if code != Imgproc.COLOR_BGR2GRAY:
            raise CvException("only COLOR_BGR2GRAY is implemented")
        if source.kind != "bgr":
            raise CvException("cvtColor expects a colour source")
        target.kind = "gray"
        target.payload = imaging.to_gray_grid(source.payload)
        target.width, target.height = source.width, source.height

    @staticmethod
    def GaussianBlur(source: Mat, target: Mat, size: Size, sigma) -> None:
        if int(size.width) != 5 or int(size.height) != 5:
            raise CvException("only a 5x5 Gaussian kernel is implemented")
        if source.kind != "gray":
            raise CvException("GaussianBlur expects a single-channel source")
        target.kind = "gray"
        target.payload = imaging.gaussian_blur(source.payload, source.width, source.height)
        target.width, target.height = source.width, source.height

    @staticmethod
    def Canny(source: Mat, target: Mat, low: float, high: float) -> None:
        if int(low) != imaging.CANNY_LOW or int(high) != imaging.CANNY_HIGH:
            raise CvException("only the Canny 30/100 thresholds are implemented")
        if source.kind != "gray":
            raise CvException("Canny expects a single-channel source")
        target.kind = "gray"
        target.payload = imaging.canny(source.payload, source.width, source.height)
        target.width, target.height = source.width, source.height

    @staticmethod
    def matchTemplate(image: Mat, templ: Mat, result: Mat, method: int) -> None:
        if method != Imgproc.TM_CCORR_NORMED:
            raise CvException("only TM_CCORR_NORMED is implemented")
        if image.kind != "gray" or templ.kind != "gray":
            raise CvException("matchTemplate expects single-channel edge maps")
        if templ.width > image.width or templ.height > image.height:
            raise CvException("template larger than source image")
        result.kind = "score"
        result.payload = imaging.score_surface(
            image.payload, image.width, image.height,
            templ.payload, templ.width, templ.height,
        )
        result.width = image.width - templ.width + 1
        result.height = image.height - templ.height + 1
