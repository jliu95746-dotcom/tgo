"""个人微信本机只读采集与回复草稿桥接。"""

import os

# Set process-local defaults before NumPy/OpenCV load their native worker pools.
# The bridge matches small desktop regions and must coexist with office apps.
for _variable in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "OPENCV_FOR_THREADS_NUM"):
    os.environ.setdefault(_variable, "1")
del _variable
