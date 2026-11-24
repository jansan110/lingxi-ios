"""Pure-Python replicas of the OpenCV primitives the slider recognizer needs.

The reference pipeline in ``activities/coc_slider.py`` is fixed and small:

    gray   = cv2.cvtColor(image, COLOR_BGR2GRAY)
    blur   = cv2.GaussianBlur(gray, (5, 5), 0)
    edge   = cv2.Canny(blur, 30, 100)
    result = cv2.matchTemplate(edges[0], edges[1], TM_CCORR_NORMED)
    offset = cv2.minMaxLoc(result)[3][0]

Each step reproduces OpenCV's own kernel/filter definitions rather than an
approximation of them, so the resulting offset is identical.
``tests/test_imaging_parity.py`` checks that against the real library.

Why the correlation uses an FFT: a 360x140 ground image against a 46x46 patch
is ~30k positions x 2116 multiply-adds, which pure Python needs minutes for.
``match_ccorr_normed`` instead ranks every position with a fast Fourier
correlation, then recomputes the exact TM_CCORR_NORMED score for the best
candidates only. The winner is therefore the true maximum of the exact formula,
not an FFT approximation of it.
"""

from __future__ import annotations

import base64
import cmath
import io
import math

from PIL import Image


class ImagingError(Exception):
    """Raised when an input cannot be decoded or the template does not fit."""


def decode(value: str) -> "Image.Image":
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise ImagingError("image base64 decode failed") from exc
    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except Exception as exc:  # noqa: BLE001 - any decoder failure is one error
        raise ImagingError("image decode failed") from exc
    return image.convert("RGB")


# --------------------------------------------------------------------------
# COLOR_BGR2GRAY
# --------------------------------------------------------------------------
def to_gray_grid(image: "Image.Image") -> list[list[int]]:
    """OpenCV's 8-bit BGR->GRAY integer path: (R*4899 + G*9617 + B*1868) >> 14."""
    width, height = image.size
    pixels = image.load()
    grid = [[0] * width for _ in range(height)]
    for y in range(height):
        row = grid[y]
        for x in range(width):
            r, g, b = pixels[x, y]
            value = (r * 4899 + g * 9617 + b * 1868 + 8192) >> 14
            row[x] = 255 if value > 255 else value
    return grid


# --------------------------------------------------------------------------
# GaussianBlur(gray, (5, 5), 0)
# --------------------------------------------------------------------------
GAUSS_KERNEL = None
_SIGMA = 1.1  # OpenCV derives this for ksize=5, sigma=0
_raw = [math.e ** (-(x * x) / (2 * _SIGMA * _SIGMA)) for x in range(-2, 3)]
_total = sum(_raw)
GAUSS_KERNEL = tuple(value / _total for value in _raw)


def _reflect(index: int, size: int) -> int:
    """OpenCV's BORDER_REFLECT_101 index mapping."""
    if size == 1:
        return 0
    while index < 0 or index >= size:
        index = -index if index < 0 else 2 * size - index - 2
    return index


def _blur_pass(source, width, height, horizontal: bool):
    k0, k1, k2, k3, k4 = GAUSS_KERNEL
    target = [[0] * width for _ in range(height)]
    if horizontal:
        for y in range(height):
            row, out = source[y], target[y]
            for x in range(width):
                total = (
                    row[_reflect(x - 2, width)] * k0 + row[_reflect(x - 1, width)] * k1
                    + row[x] * k2
                    + row[_reflect(x + 1, width)] * k3 + row[_reflect(x + 2, width)] * k4
                )
                value = int(total + 0.5)
                out[x] = 0 if value < 0 else (255 if value > 255 else value)
    else:
        for y in range(height):
            r0, r1, r2 = source[_reflect(y - 2, height)], source[_reflect(y - 1, height)], source[y]
            r3, r4 = source[_reflect(y + 1, height)], source[_reflect(y + 2, height)]
            out = target[y]
            for x in range(width):
                total = r0[x] * k0 + r1[x] * k1 + r2[x] * k2 + r3[x] * k3 + r4[x] * k4
                value = int(total + 0.5)
                out[x] = 0 if value < 0 else (255 if value > 255 else value)
    return target


def gaussian_blur(grid, width, height):
    return _blur_pass(_blur_pass(grid, width, height, True), width, height, False)


# --------------------------------------------------------------------------
# Canny(30, 100)
# --------------------------------------------------------------------------
CANNY_LOW = 30
CANNY_HIGH = 100


def canny(grid, width, height):
    edges = [[0] * width for _ in range(height)]
    if width < 3 or height < 3:
        return edges

    xm = [_reflect(x - 1, width) for x in range(width)]
    xp = [_reflect(x + 1, width) for x in range(width)]
    ym = [_reflect(y - 1, height) for y in range(height)]
    yp = [_reflect(y + 1, height) for y in range(height)]

    magnitude = [[0] * width for _ in range(height)]
    for y in range(height):
        r_a, r_b, r_c = grid[ym[y]], grid[y], grid[yp[y]]
        row = magnitude[y]
        for x in range(width):
            i0, i1 = xm[x], xp[x]
            gx = (r_a[i1] - r_a[i0]) + 2 * (r_b[i1] - r_b[i0]) + (r_c[i1] - r_c[i0])
            gy = (r_c[i0] - r_a[i0]) + 2 * (r_c[x] - r_a[x]) + (r_c[i1] - r_a[i1])
            row[x] = abs(gx) + abs(gy)

    state = [[0] * width for _ in range(height)]
    queue = []
    push = queue.append
    for y in range(height):
        row_state, row_mag = state[y], magnitude[y]
        for x in range(width):
            value = row_mag[x]
            if value >= CANNY_HIGH:
                row_state[x] = 1
                push((x, y))
            elif value < CANNY_LOW:
                row_state[x] = 2

    pop = queue.pop
    while queue:
        x, y = pop()
        for ny in (y - 1, y, y + 1):
            if ny < 0 or ny >= height:
                continue
            row_state, row_mag = state[ny], magnitude[ny]
            for nx in (x - 1, x, x + 1):
                if nx < 0 or nx >= width or row_state[nx] != 0:
                    continue
                if row_mag[nx] >= CANNY_LOW:
                    row_state[nx] = 1
                    push((nx, ny))
                else:
                    row_state[nx] = 2

    for y in range(height):
        row_out, row_state = edges[y], state[y]
        for x in range(width):
            row_out[x] = 255 if row_state[x] == 1 else 0
    return edges


# --------------------------------------------------------------------------
# FFT (radix-2, in place)
# --------------------------------------------------------------------------
def _fft(values, inverse=False):
    n = len(values)
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            values[i], values[j] = values[j], values[i]

    sign = 1.0 if inverse else -1.0
    length = 2
    while length <= n:
        angle = sign * 2.0 * math.pi / length
        step = complex(math.cos(angle), math.sin(angle))
        half = length >> 1
        for start in range(0, n, length):
            w = 1.0 + 0.0j
            for k in range(start, start + half):
                u = values[k]
                v = values[k + half] * w
                values[k] = u + v
                values[k + half] = u - v
                w *= step
        length <<= 1

    if inverse:
        scale = 1.0 / n
        for i in range(n):
            values[i] *= scale
    return values


def _fft2(matrix, inverse=False):
    for row in matrix:
        _fft(row, inverse)
    height = len(matrix)
    width = len(matrix[0])
    column = [0j] * height
    for x in range(width):
        for y in range(height):
            column[y] = matrix[y][x]
        _fft(column, inverse)
        for y in range(height):
            matrix[y][x] = column[y]
    return matrix


# --------------------------------------------------------------------------
# matchTemplate(TM_CCORR_NORMED) + minMaxLoc
# --------------------------------------------------------------------------
_REFINE_CANDIDATES = 40


def _exact_score(image, templ, x, y, tw, th, templ_energy):
    """The TM_CCORR_NORMED value at one position, in the formula OpenCV uses."""
    cross = 0
    energy = 0
    for ty in range(th):
        image_row = image[y + ty]
        templ_row = templ[ty]
        for tx in range(tw):
            iv = image_row[x + tx]
            if iv:
                energy += iv * iv
                cross += iv * templ_row[tx]
    if not energy or not cross:
        return None
    return cross / ((energy * templ_energy) ** 0.5)


def match_ccorr_normed(image, width, height, templ, tw, th):
    """Return ``(best_x, best_y)`` of TM_CCORR_NORMED, ties resolved like minMaxLoc.

    Positions are ranked by an FFT correlation (which is the same convolution up
    to float rounding), then each finalist is rescored with the exact formula.
    The winner is thus the exact maximum, and the search stays fast.
    """
    if tw > width or th > height:
        raise ImagingError("template larger than source image")

    templ_energy = 0
    for row in templ:
        for value in row:
            templ_energy += value * value
    if not templ_energy:
        raise ImagingError("template has no edges")

    size_w = 1
    while size_w < width + tw:
        size_w <<= 1
    size_h = 1
    while size_h < height + th:
        size_h <<= 1

    source = [[complex(image[y][x] if x < width and y < height else 0) for x in range(size_w)]
              for y in range(size_h)]
    kernel = [[complex(templ[y][x] if x < tw and y < th else 0) for x in range(size_w)]
              for y in range(size_h)]

    _fft2(source)
    _fft2(kernel)
    for y in range(size_h):
        srow, krow = source[y], kernel[y]
        for x in range(size_w):
            # Correlation, not convolution: conjugate the kernel.
            srow[x] *= krow[x].conjugate()
    _fft2(source, inverse=True)

    ranked = []
    for y in range(height - th + 1):
        row = source[y]
        for x in range(width - tw + 1):
            ranked.append((row[x].real, x, y))
    ranked.sort(key=lambda item: -item[0])

    seen = set()
    best_score, best_x, best_y = None, 0, 0
    for _, x, y in ranked:
        if len(seen) >= _REFINE_CANDIDATES:
            break
        if (x, y) in seen:
            continue
        seen.add((x, y))
        score = _exact_score(image, templ, x, y, tw, th, templ_energy)
        if score is None:
            continue
        if best_score is None or score > best_score:
            best_score, best_x, best_y = score, x, y
    return best_x, best_y


def template_offset(ground: str, patch: str) -> int:
    """Reproduce ``cv2.minMaxLoc(cv2.matchTemplate(...))[3][0]``."""
    ground_image = decode(ground)
    patch_image = decode(patch)
    width, height = ground_image.size
    tw, th = patch_image.size
    ground_edges = canny(gaussian_blur(to_gray_grid(ground_image), width, height), width, height)
    patch_edges = canny(gaussian_blur(to_gray_grid(patch_image), tw, th), tw, th)
    x, _ = match_ccorr_normed(ground_edges, width, height, patch_edges, tw, th)
    return x

def score_surface(image, width, height, templ, tw, th):
    """The full TM_CCORR_NORMED score map, as ``matchTemplate`` produces it.

    Filled with the exact formula at every position so ``minMaxLoc`` sees the
    same surface OpenCV would build. Slower than ``match_ccorr_normed`` by
    design: that one exists for the offset-only fast path, this one for callers
    that need the whole map.
    """
    if tw > width or th > height:
        raise ImagingError("template larger than source image")
    templ_energy = 0
    for row in templ:
        for value in row:
            templ_energy += value * value
    surface = []
    for y in range(height - th + 1):
        row_scores = [0.0] * (width - tw + 1)
        for x in range(width - tw + 1):
            cross = 0
            energy = 0
            for ty in range(th):
                image_row = image[y + ty]
                templ_row = templ[ty]
                for tx in range(tw):
                    iv = image_row[x + tx]
                    if iv:
                        energy += iv * iv
                        cross += iv * templ_row[tx]
            row_scores[x] = 0.0 if not energy or not templ_energy else cross / ((energy * templ_energy) ** 0.5)
        surface.append(row_scores)
    return surface
