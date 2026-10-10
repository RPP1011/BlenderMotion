"""Minimal .npy / .npz reader and writer (stdlib only).

The orchestrator ranks Kimodo takes from the output `.npz` without needing
numpy in its own interpreter. Supports little-endian float32/float64/int32/
int64 arrays in C order, which is what numpy.savez writes for Kimodo's
`posed_joints` and `root_positions`. Arrays come back as `NDArray`: a flat
Python list plus a shape tuple.
"""

import ast
import struct
import zipfile
from array import array

_CODES = {"<f4": "f", "<f8": "d", "<i4": "i", "<i8": "q", "|u1": "B", "|i1": "b", "<u4": "I"}


class NDArray:
    __slots__ = ("data", "shape")

    def __init__(self, data, shape):
        self.data = data
        self.shape = tuple(shape)

    def __len__(self):
        return self.shape[0] if self.shape else 1

    def row_size(self):
        n = 1
        for s in self.shape[1:]:
            n *= s
        return n

    def frame(self, i):
        """Flat values of the i-th entry along axis 0."""
        k = self.row_size()
        return self.data[i * k:(i + 1) * k]

    def split0(self):
        """Split along the first axis into NDArrays of shape[1:]."""
        return [NDArray(list(self.frame(i)), self.shape[1:]) for i in range(self.shape[0])]


def parse_npy(raw):
    if raw[:6] != b"\x93NUMPY":
        raise ValueError("not a .npy file")
    major = raw[6]
    if major == 1:
        hlen = struct.unpack("<H", raw[8:10])[0]
        start = 10
    else:
        hlen = struct.unpack("<I", raw[8:12])[0]
        start = 12
    header = ast.literal_eval(raw[start:start + hlen].decode("latin1"))
    descr = header["descr"]
    if header.get("fortran_order"):
        raise ValueError("fortran-order arrays are not supported")
    code = _CODES.get(descr)
    if code is None:
        raise ValueError(f"unsupported dtype {descr}")
    arr = array(code)
    arr.frombytes(raw[start + hlen:])
    shape = header["shape"]
    n = 1
    for s in shape:
        n *= s
    if len(arr) < n:
        raise ValueError(f"truncated array: {len(arr)} < {n}")
    return NDArray(arr[:n].tolist(), shape)


def load_npz(path):
    """{name: NDArray} for every array in an .npz (unsupported dtypes skipped)."""
    out = {}
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if not name.endswith(".npy"):
                continue
            try:
                out[name[:-4]] = parse_npy(z.read(name))
            except ValueError:
                continue
    return out


def _npy_bytes(values, shape, code="f"):
    descr = {"f": "<f4", "d": "<f8", "i": "<i4", "q": "<i8"}[code]
    shape_s = "(" + ", ".join(str(s) for s in shape) + ("," if len(shape) == 1 else "") + ")"
    header = "{'descr': '%s', 'fortran_order': False, 'shape': %s, }" % (descr, shape_s)
    pad = 64 - (10 + len(header) + 1) % 64
    header = header + " " * pad + "\n"
    return b"\x93NUMPY\x01\x00" + struct.pack("<H", len(header)) + header.encode("latin1") + \
        array(code, values).tobytes()


def save_npz(path, arrays):
    """arrays: {name: (flat_values, shape)} written as float32."""
    with zipfile.ZipFile(path, "w") as z:
        for name, (values, shape) in arrays.items():
            z.writestr(name + ".npy", _npy_bytes(values, shape))
