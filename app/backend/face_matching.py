"""
Hub-side face enrollment and matching using InsightFace + ONNX Runtime (no TensorFlow).

Uses the ``buffalo_l`` model pack (downloaded on first run under ~/.insightface/models).
Works on Windows CPUs where TensorFlow fails (e.g. missing AVX, VC++ redist issues).
"""

from __future__ import annotations

import json
import re
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np

from hub_config import DATA_DIR

FACE_PROFILES_ROOT = DATA_DIR / "face_profiles"

# Cosine similarity on L2-normalized embeddings (InsightFace ArcFace ~512-D).
DEFAULT_MATCH_THRESHOLD = 0.35

_face_app = None
_face_app_lock = threading.Lock()
_face_app_error: Optional[str] = None


def _get_face_app():
    """Lazy-init FaceAnalysis (CPU); thread-safe."""
    global _face_app, _face_app_error
    if _face_app_error is not None:
        return None
    if _face_app is not None:
        return _face_app
    with _face_app_lock:
        if _face_app is not None:
            return _face_app
        try:
            from insightface.app import FaceAnalysis
        except ImportError as e:
            _face_app_error = str(e)
            print(
                "[face_matching] insightface not installed; "
                "pip install insightface onnxruntime"
            )
            return None
        try:
            # buffalo_l: good accuracy; CPU-only providers avoid CUDA requirement.
            app = FaceAnalysis(
                name="buffalo_l",
                providers=["CPUExecutionProvider"],
            )
            # ctx_id=-1 CPU; smaller det_size = faster, still OK for enrollment snapshots
            app.prepare(ctx_id=-1, det_size=(320, 320))
            _face_app = app
            print("[face_matching] InsightFace buffalo_l ready (ONNX Runtime CPU).")
        except Exception as e:
            _face_app_error = str(e)
            print(f"[face_matching] InsightFace init failed: {e}")
            return None
    return _face_app


def _safe_segment(s: str) -> str:
    t = re.sub(r"[^a-zA-Z0-9._-]+", "_", (s or "").strip())
    return t[:120] if t else "unknown"


def device_dir(device_id: str) -> Path:
    did = _safe_segment(device_id or "default_bot")
    return FACE_PROFILES_ROOT / did


def profiles_json_path(device_id: str) -> Path:
    return device_dir(device_id) / "profiles.json"


def profile_dir(device_id: str, profile_id: str) -> Path:
    return device_dir(device_id) / _safe_segment(profile_id)


def _ensure_dirs(device_id: str) -> None:
    device_dir(device_id).mkdir(parents=True, exist_ok=True)


def load_profiles_index(device_id: str) -> list[dict[str, Any]]:
    p = profiles_json_path(device_id)
    if not p.is_file():
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"[face_matching] load_profiles_index: {e}")
        return []


def _save_profiles_index(device_id: str, rows: list[dict[str, Any]]) -> None:
    _ensure_dirs(device_id)
    p = profiles_json_path(device_id)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)


def _represent_image_bgr(image_bgr: np.ndarray) -> Optional[np.ndarray]:
    """Face embedding from BGR image; L2-normalized."""
    app = _get_face_app()
    if app is None:
        return None
    try:
        with _face_app_lock:
            faces = app.get(image_bgr)
        if not faces:
            return None
        face = max(
            faces,
            key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]),
        )
        emb = np.asarray(face.embedding, dtype=np.float64)
        n = np.linalg.norm(emb)
        if n > 1e-9:
            emb = emb / n
        return emb
    except Exception as e:
        print(f"[face_matching] represent failed: {e}")
        return None


def embedding_from_jpeg_bytes(jpeg_bytes: bytes) -> Optional[np.ndarray]:
    arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return None
    return _represent_image_bgr(img)


def _avg_embeddings(vectors: list[np.ndarray]) -> Optional[np.ndarray]:
    if not vectors:
        return None
    s = np.zeros_like(vectors[0], dtype=np.float64)
    for v in vectors:
        s += v
    s /= len(vectors)
    n = np.linalg.norm(s)
    if n > 1e-9:
        s = s / n
    return s


def create_profile(device_id: str, display_name: str) -> dict[str, Any]:
    _ensure_dirs(device_id)
    profile_id = uuid.uuid4().hex[:16]
    row = {
        "profile_id": profile_id,
        "display_name": (display_name or "").strip() or "Person",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    rows = load_profiles_index(device_id)
    rows.append(row)
    _save_profiles_index(device_id, rows)
    profile_dir(device_id, profile_id).mkdir(parents=True, exist_ok=True)
    return row


def delete_profile(device_id: str, profile_id: str) -> bool:
    rows = load_profiles_index(device_id)
    new_rows = [r for r in rows if r.get("profile_id") != profile_id]
    if len(new_rows) == len(rows):
        return False
    _save_profiles_index(device_id, new_rows)
    pd = profile_dir(device_id, profile_id)
    if pd.is_dir():
        shutil.rmtree(pd, ignore_errors=True)
    return True


def add_reference_jpeg(device_id: str, profile_id: str, jpeg_bytes: bytes, suffix: str = "jpg") -> Optional[str]:
    """Save JPEG and recompute mean embedding for profile. Returns saved filename or None."""
    emb = embedding_from_jpeg_bytes(jpeg_bytes)
    if emb is None:
        return None
    pd = profile_dir(device_id, profile_id)
    if not pd.is_dir():
        return None
    idx = 1
    while True:
        name = f"ref_{idx:03d}.{suffix}"
        if not (pd / name).is_file():
            break
        idx += 1
    out_path = pd / name
    with open(out_path, "wb") as f:
        f.write(jpeg_bytes)
    _rebuild_profile_embedding(device_id, profile_id)
    return name


def _rebuild_profile_embedding(device_id: str, profile_id: str) -> None:
    pd = profile_dir(device_id, profile_id)
    if not pd.is_dir():
        return
    vecs: list[np.ndarray] = []
    for p in sorted(pd.glob("ref_*.*")):
        if p.suffix.lower() not in (".jpg", ".jpeg", ".png", ".bmp"):
            continue
        data = p.read_bytes()
        arr = np.frombuffer(data, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            continue
        v = _represent_image_bgr(img)
        if v is not None:
            vecs.append(v)
    mean = _avg_embeddings(vecs)
    emb_path = pd / "embedding.json"
    if mean is None:
        if emb_path.is_file():
            emb_path.unlink()
        return
    emb_path.write_text(json.dumps({"values": mean.tolist()}), encoding="utf-8")


def load_profile_embedding(device_id: str, profile_id: str) -> Optional[np.ndarray]:
    emb_path = profile_dir(device_id, profile_id) / "embedding.json"
    if not emb_path.is_file():
        return None
    try:
        data = json.loads(emb_path.read_text(encoding="utf-8"))
        vals = data.get("values")
        if not vals:
            return None
        v = np.asarray(vals, dtype=np.float64)
        n = np.linalg.norm(v)
        if n > 1e-9:
            v = v / n
        return v
    except Exception:
        return None


def match_probe_jpeg(
    device_id: str,
    jpeg_bytes: bytes,
    threshold: float = DEFAULT_MATCH_THRESHOLD,
) -> Optional[tuple[str, str, float]]:
    """
    Returns (profile_id, display_name, cosine_similarity) for best match above threshold, else None.
    """
    probe = embedding_from_jpeg_bytes(jpeg_bytes)
    if probe is None:
        return None
    rows = load_profiles_index(device_id)
    best: Optional[tuple[str, str, float]] = None
    for row in rows:
        pid = row.get("profile_id")
        if not pid:
            continue
        ref = load_profile_embedding(device_id, pid)
        if ref is None:
            continue
        sim = float(np.dot(probe, ref))
        name = str(row.get("display_name") or "Person")
        if best is None or sim > best[2]:
            best = (pid, name, sim)
    if best is None:
        return None
    if best[2] < threshold:
        return None
    return best


def list_profiles(device_id: str) -> list[dict[str, Any]]:
    return list(load_profiles_index(device_id))
