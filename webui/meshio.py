"""메쉬 로더 — 카탈로그 자산을 웹 프리뷰용 삼각형 목록으로 읽는다.

FBX 는 trimesh/assimp 로 못 읽는다(trimesh: "file_type 'fbx' not supported",
pyassimp 미설치). 그래서 바이너리 FBX 를 직접 판다. 우리에게 필요한 건
`Geometry` 노드의 `Vertices`(f64 배열)와 `PolygonVertexIndex`(i32 배열) 둘뿐이라
전체 포맷을 구현할 필요가 없다.

바이너리 FBX 구조 (버전 7400 = 32비트 오프셋; 7500+ 는 64비트):
    헤더 27 B : "Kaydara FBX Binary  \\x00" + u16 + u32(version)
    노드 레코드:
        EndOffset u32 | NumProperties u32 | PropertyListLen u32
        NameLen u8 | Name
        properties...
        (EndOffset 까지 남으면) 자식 노드들 + 13 B NULL 레코드
    프로퍼티 타입 코드:
        Y i16 | C bool | I i32 | F f32 | D f64 | L i64
        f i d l b : 배열 = ArrayLen u32, Encoding u32, CompressedLen u32, data
                    (Encoding==1 이면 zlib)
        S R : 길이 u32 + 바이트열
"""
import pathlib
import struct
import zlib

import numpy as np

                                                         
                                                                 
                                                   
                                           
_ARRAY = {"f": ("f", 4), "d": ("d", 8), "l": ("q", 8), "i": ("i", 4), "b": ("b", 1),
          "c": ("b", 1)}
_SCALAR = {"Y": ("h", 2), "C": ("?", 1), "I": ("i", 4), "F": ("f", 4),
           "D": ("d", 8), "L": ("q", 8)}


class _Reader:
    def __init__(self, buf, wide):
        self.b = buf
        self.p = 0
        self.wide = wide                                 

    def u8(self):
        v = self.b[self.p]; self.p += 1; return v

    def u32(self):
        v = struct.unpack_from("<I", self.b, self.p)[0]; self.p += 4; return v

    def off(self):
        if self.wide:
            v = struct.unpack_from("<Q", self.b, self.p)[0]; self.p += 8
        else:
            v = self.u32()
        return v

    def raw(self, n):
        v = self.b[self.p:self.p + n]; self.p += n; return v


def _read_prop(r):
    t = chr(r.u8())
    if t in _SCALAR:
        f, n = _SCALAR[t]
        v = struct.unpack_from("<" + f, r.b, r.p)[0]; r.p += n
        return v
    if t in _ARRAY:
        f, isz = _ARRAY[t]
        n = r.u32(); enc = r.u32(); clen = r.u32()
        data = r.raw(clen)
        if enc == 1:
            data = zlib.decompress(data)
        return np.frombuffer(data, dtype="<" + f, count=n)
    if t in ("S", "R"):
        n = r.u32()
        return r.raw(n)
    raise ValueError(f"unknown FBX property type {t!r}")


def _walk(r, end, want, out):
    """want 에 있는 이름의 노드를 만나면 그 프로퍼티를 out 에 모은다."""
    while r.p < end - 13:
        node_end = r.off()
        nprops = r.off()
        r.off()                                                           
        name = r.raw(r.u8()).decode("ascii", "replace")
        if node_end == 0:
            return
        props = [_read_prop(r) for _ in range(nprops)]
        if name in want:
            out.setdefault(name, []).append(props)
        if r.p < node_end - 13:                                
            _walk(r, node_end, want, out)
        r.p = node_end
    r.p = end


def load_fbx(path):
    """(vertices[N,3], faces[M,3]) 반환. 여러 Geometry 가 있으면 전부 합친다."""
    buf = pathlib.Path(path).read_bytes()
    if not buf.startswith(b"Kaydara FBX Binary"):
        raise ValueError("ASCII FBX 는 지원하지 않는다")
    ver = struct.unpack_from("<I", buf, 23)[0]
    r = _Reader(buf, wide=ver >= 7500)
    r.p = 27
    out = {}
    _walk(r, len(buf), {"Vertices", "PolygonVertexIndex"}, out)

    V, F = [], []
    vs = out.get("Vertices", [])
    ps = out.get("PolygonVertexIndex", [])
    base = 0
    for i in range(min(len(vs), len(ps))):
        v = np.asarray(vs[i][-1], dtype=np.float64).reshape(-1, 3)
        idx = np.asarray(ps[i][-1], dtype=np.int64)
        V.append(v)
                                                       
        poly = []
        for k in idx:
            if k < 0:
                poly.append(~k)
                for j in range(1, len(poly) - 1):
                    F.append([poly[0] + base, poly[j] + base, poly[j + 1] + base])
                poly = []
            else:
                poly.append(k)
        base += len(v)
    if not V:
        raise ValueError("Geometry 를 찾지 못함")
    return np.concatenate(V), np.asarray(F, dtype=np.int64).reshape(-1, 3)


def load_obj(path):
    v, f = [], []
    for line in pathlib.Path(path).read_text(errors="replace").splitlines():
        if line.startswith("v "):
            v.append([float(x) for x in line.split()[1:4]])
        elif line.startswith("f "):
            idx = [int(t.split("/")[0]) for t in line.split()[1:]]
            idx = [i - 1 if i > 0 else len(v) + i for i in idx]
            for j in range(1, len(idx) - 1):
                f.append([idx[0], idx[j], idx[j + 1]])
    return np.asarray(v, dtype=np.float64), np.asarray(f, dtype=np.int64).reshape(-1, 3)


def load_mesh(path):
    p = str(path).lower()
    if p.endswith(".fbx"):
        return load_fbx(path)
    if p.endswith(".obj"):
        return load_obj(path)
    raise ValueError("지원하지 않는 형식: " + path)


def decimate(V, F, max_faces=2500):
    """프리뷰용 삼각형 수 축소.

    정식 감쇠(edge collapse)는 무겁고 여기서는 '생김새'만 알면 되므로,
    면적이 큰 삼각형부터 남긴다. 형상의 큰 덩어리가 우선 보존된다.
    """
    if len(F) <= max_faces:
        return V, F
    a = V[F[:, 1]] - V[F[:, 0]]
    b = V[F[:, 2]] - V[F[:, 0]]
    area = np.linalg.norm(np.cross(a, b), axis=1)
    keep = np.argpartition(-area, max_faces)[:max_faces]
    F2 = F[keep]
    used, inv = np.unique(F2, return_inverse=True)
    return V[used], inv.reshape(-1, 3)


def preview(path, max_faces=15000, target_size=None):
    """웹 프리뷰용 dict. 원점 중심 + (선택) 실물 크기[m] 로 스케일."""
    V, F = load_mesh(path)
    V, F = decimate(V, F, max_faces)
    lo, hi = V.min(axis=0), V.max(axis=0)
    size = hi - lo
    maxdim = float(size.max()) or 1.0
    V = V - (lo + hi) / 2.0
    scale = 1.0
    if target_size:
        scale = float(target_size) / maxdim
        V = V * scale
    return {
        "verts": [round(float(x), 4) for x in V.ravel()],
        "faces": [int(i) for i in F.ravel()],
        "bbox_m": [round(float(s * scale), 3) for s in size],
        "n_faces": int(len(F)),
        "source_max_m": round(maxdim, 3),
    }
