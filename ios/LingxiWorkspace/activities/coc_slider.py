"""Recognize the official COC image offset in Python."""
import base64
import os

if os.environ.get('LINGXI_ANDROID') != '1':
    import cv2
    import numpy as np

from .coc_client import CocError


def slide_position(ground, patch):
    if os.environ.get('LINGXI_ANDROID') == '1':
        from .coc_slider_android import slide_position as android_position
        return android_position(ground,patch)
    try:
        images=[cv2.imdecode(np.frombuffer(base64.b64decode(value,validate=True),np.uint8),
                             cv2.IMREAD_COLOR) for value in (ground,patch)]
        if any(image is None for image in images):
            raise ValueError('Image decode failed')
        edges=[cv2.Canny(cv2.GaussianBlur(cv2.cvtColor(image,cv2.COLOR_BGR2GRAY),(5,5),0),30,100)
               for image in images]
        result=cv2.matchTemplate(edges[0],edges[1],cv2.TM_CCORR_NORMED)
        return cv2.minMaxLoc(result)[3][0]
    except (ValueError,cv2.error) as error:
        raise CocError('官方图形暂时无法自动识别，请重新获取验证码', 'SLIDER_RECOGNITION') from error
