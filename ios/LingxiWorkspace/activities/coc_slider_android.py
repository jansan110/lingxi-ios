"""Use Android's packaged OpenCV image primitives from the Python workflow."""
import base64
from java import jarray, jbyte, jclass

from .coc_client import CocError


def slide_position(ground, patch):
    loader=jclass('org.opencv.android.OpenCVLoader')
    if not loader.initLocal():
        raise CocError('本机图像组件未能启动，请重新打开客户端','SLIDER_RECOGNITION')
    Mat,MatOfByte=jclass('org.opencv.core.Mat'),jclass('org.opencv.core.MatOfByte')
    Size,Core=jclass('org.opencv.core.Size'),jclass('org.opencv.core.Core')
    codecs,proc=jclass('org.opencv.imgcodecs.Imgcodecs'),jclass('org.opencv.imgproc.Imgproc')
    matrices=[]
    try:
        edges=[]
        for value in (ground,patch):
            data=base64.b64decode(value,validate=True)
            encoded=MatOfByte(jarray(jbyte)([byte if byte<128 else byte-256 for byte in data]))
            matrices.append(encoded)
            image=codecs.imdecode(encoded,codecs.IMREAD_COLOR)
            matrices.append(image)
            if image.empty():raise ValueError('Image decode failed')
            gray,blur,edge=Mat(),Mat(),Mat()
            matrices.extend((gray,blur,edge))
            proc.cvtColor(image,gray,proc.COLOR_BGR2GRAY)
            proc.GaussianBlur(gray,blur,Size(5,5),0)
            proc.Canny(blur,edge,30,100)
            edges.append(edge)
        result=Mat()
        matrices.append(result)
        proc.matchTemplate(edges[0],edges[1],result,proc.TM_CCORR_NORMED)
        return int(Core.minMaxLoc(result).maxLoc.x)
    except (ValueError,jclass('org.opencv.core.CvException')) as error:
        raise CocError('官方图形暂时无法自动识别，请重新获取验证码','SLIDER_RECOGNITION') from error
    finally:
        for matrix in matrices:matrix.release()
