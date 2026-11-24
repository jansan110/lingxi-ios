"""The primitive-array helpers Chaquopy exposes on Android."""

jbyte = int
jchar = str
jshort = int
jint = int
jlong = int
jfloat = float
jdouble = float


def jarray(kind):
    """Return a constructor for a Java primitive array of ``kind``.

    Android's ``jarray(jbyte)([...])`` builds a ``byte[]``. Python bytes are the
    same thing, so the constructor normalises and keeps the type stable.
    """
    def build(values):
        return bytes((value & 0xFF) for value in values)
    return build
