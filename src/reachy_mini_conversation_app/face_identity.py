"""Recognize enrolled household members on camera, entirely on this computer.

Faces are found with the SDK's YuNet detector, aligned from the eyes and nose, and turned
into 128-number fingerprints by OpenCV's SFace model (Apache-2.0, downloaded once from the
Hugging Face Hub). Enrolled people are stored next to the household memory: fingerprints in
``faces.json`` and a few small face photos for the settings page. Nothing leaves the machine
except, when enabled, the recognized person's name in the conversation context.
"""

from __future__ import annotations
import os
import re
import json
import time
import uuid
import logging
import tempfile
import threading
import unicodedata
from typing import Any, Callable
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import field, dataclass

import numpy as np
from numpy.typing import NDArray


logger = logging.getLogger(__name__)

SFACE_REPO = "opencv/face_recognition_sface"
SFACE_FILE = "face_recognition_sface_2021dec.onnx"
SFACE_REVISION = "3d7082438a6e4551e840c9b2bb60b71e8da4b524"
MODEL_ID = "sface_2021dec"

# Cosine similarity between unit fingerprints; OpenCV's SFace match threshold is 0.363.
MATCH_THRESHOLD = 0.40
# Acting on identity (crediting memories, telling the model who is in front) needs a surer match:
# at 0.41-0.43 Eduardo was taken for his toddler, while his own matches scored 0.51-0.62.
CONFIDENT_MATCH_SCORE = 0.50
# The best person must beat the runner-up by this much, else the face is ambiguous.
MATCH_MARGIN = 0.05
# A weaker match (MATCH_THRESHOLD up to CONFIDENT_MATCH_SCORE) counts once it repeats: the same person
# on top this many passes in a row (about a second apart), or the person already recognized in front.
# Adriana, enrolled from phone photos, scored 0.40-0.49 for minutes at a time; the toddler mix-up
# alternated with Eduardo's own confident matches, so it never repeats.
WEAK_MATCH_STREAK = 3
# Smallest face (bounding-box width in camera pixels) worth recognizing / enrolling.
MIN_RECOGNIZE_FACE_PX = 48
MIN_ENROLL_FACE_PX = 80
DETECT_DOWNSCALE = 2
MAX_SAMPLES_PER_PERSON = 12
PHOTO_SIZE = 160
MAX_NAME_CHARS = 60
MAX_UPLOAD_PHOTOS = 5
# Photos of one person agree at least this much even across poses and light; strangers rarely do.
SAME_PERSON_MIN_SIMILARITY = 0.30

# ArcFace/SFace 112x112 alignment template: right eye (image left), left eye, nose tip.
_ALIGN_SIZE = 112
_ALIGN_TEMPLATE = np.array([[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366]], dtype=np.float64)

FACES_FILE = "faces.json"
PHOTOS_DIR = "photos"
STORE_VERSION = 1


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


def similarity_transform(src: NDArray[np.float64], dst: NDArray[np.float64]) -> NDArray[np.float64]:
    """Return the 2x3 rotation + uniform scale + translation mapping ``src`` points onto ``dst`` (Umeyama)."""
    src = np.asarray(src, dtype=np.float64)
    dst = np.asarray(dst, dtype=np.float64)
    src_mean, dst_mean = src.mean(axis=0), dst.mean(axis=0)
    src_c, dst_c = src - src_mean, dst - dst_mean
    covariance = dst_c.T @ src_c / len(src)
    u, singular, vt = np.linalg.svd(covariance)
    sign = np.ones(2)
    if np.linalg.det(u @ vt) < 0:
        sign[-1] = -1.0
    rotation = u @ np.diag(sign) @ vt
    variance = src_c.var(axis=0).sum()
    scale = float((singular * sign).sum() / variance) if variance > 0 else 1.0
    matrix = np.zeros((2, 3))
    matrix[:, :2] = scale * rotation
    matrix[:, 2] = dst_mean - scale * rotation @ src_mean
    return matrix


def warp_affine(image: NDArray[np.uint8], matrix: NDArray[np.float64], size: int) -> NDArray[np.uint8]:
    """Return a ``size`` x ``size`` image where output pixel p samples ``image`` at ``matrix^-1 p`` (bilinear)."""
    inverse = np.linalg.inv(np.vstack([matrix, [0.0, 0.0, 1.0]]))
    ys, xs = np.mgrid[0:size, 0:size]
    points = inverse @ np.stack([xs.ravel(), ys.ravel(), np.ones(size * size)])
    x, y = points[0], points[1]
    height, width = image.shape[:2]
    x0 = np.clip(np.floor(x).astype(int), 0, width - 2)
    y0 = np.clip(np.floor(y).astype(int), 0, height - 2)
    fx = np.clip(x - x0, 0.0, 1.0)[:, None]
    fy = np.clip(y - y0, 0.0, 1.0)[:, None]
    pixels = image.astype(np.float32)
    sampled = (
        pixels[y0, x0] * (1 - fx) * (1 - fy)
        + pixels[y0, x0 + 1] * fx * (1 - fy)
        + pixels[y0 + 1, x0] * (1 - fx) * fy
        + pixels[y0 + 1, x0 + 1] * fx * fy
    )
    result: NDArray[np.uint8] = np.clip(sampled, 0, 255).astype(np.uint8).reshape(size, size, image.shape[2])
    return result


def face_photo(
    frame: NDArray[np.uint8], bbox: tuple[float, float, float, float], size: int = PHOTO_SIZE
) -> NDArray[np.uint8]:
    """Return a square crop around a face box, with some margin, resized to ``size``."""
    x, y, w, h = bbox
    side = max(w, h) * 1.5
    cx, cy = x + w / 2, y + h / 2
    scale = size / side
    matrix = np.array([[scale, 0.0, size / 2 - scale * cx], [0.0, scale, size / 2 - scale * cy]])
    return warp_affine(frame, matrix, size)


# ---------------------------------------------------------------------------
# Detection and fingerprints
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FaceObservation:
    """One face seen in a frame: box in camera pixels, unit fingerprint, and a small photo."""

    bbox: tuple[float, float, float, float]
    embedding: NDArray[np.float32]
    photo: NDArray[np.uint8]

    @property
    def width(self) -> float:
        """Face box width in camera pixels."""
        return self.bbox[2]


class FaceAnalyzer:
    """Find faces in BGR frames and compute their fingerprints; models load on first use."""

    def __init__(
        self,
        detector_factory: Callable[[], Any] | None = None,
        embedder_factory: Callable[[], Callable[[NDArray[np.float32]], NDArray[np.float32]]] | None = None,
    ) -> None:
        """Use the SDK detector and SFace unless factories are given (tests)."""
        self._detector_factory = detector_factory or _default_detector
        self._embedder_factory = embedder_factory or _default_embedder
        self._detector: Any = None
        self._embed: Callable[[NDArray[np.float32]], NDArray[np.float32]] | None = None
        self._lock = threading.Lock()

    def load(self) -> None:
        """Load (and on first run download) the models; safe to call repeatedly."""
        with self._lock:
            self._load_locked()

    def _load_locked(self) -> None:
        if self._detector is None:
            self._detector = self._detector_factory()
        if self._embed is None:
            self._embed = self._embedder_factory()

    def analyze(
        self, frame: NDArray[np.uint8], min_face_px: float = MIN_RECOGNIZE_FACE_PX, whole_faces_only: bool = False
    ) -> list[FaceObservation]:
        """Return the faces at least ``min_face_px`` wide, largest first.

        ``whole_faces_only`` drops faces cut off by the image edge, whose fingerprints are unreliable.
        """
        height, width = frame.shape[:2]
        with self._lock:
            self._load_locked()
            assert self._embed is not None
            small = np.ascontiguousarray(frame[::DETECT_DOWNSCALE, ::DETECT_DOWNSCALE])
            observations = []
            for face in self._detector.detect(small):
                bbox = tuple(float(v) * DETECT_DOWNSCALE for v in face.bbox)
                if bbox[2] < min_face_px:
                    continue
                if whole_faces_only and (
                    bbox[0] < 0 or bbox[1] < 0 or bbox[0] + bbox[2] > width or bbox[1] + bbox[3] > height
                ):
                    continue
                landmarks = np.array([face.right_eye, face.left_eye, face.nose], dtype=np.float64) * DETECT_DOWNSCALE
                aligned = warp_affine(frame, similarity_transform(landmarks, _ALIGN_TEMPLATE), _ALIGN_SIZE)
                # SFace takes RGB, NCHW, 0..255.
                blob = aligned[:, :, ::-1].transpose(2, 0, 1)[np.newaxis].astype(np.float32)
                embedding = np.asarray(self._embed(blob), dtype=np.float32).reshape(-1)
                norm = float(np.linalg.norm(embedding))
                if norm <= 0:
                    continue
                observations.append(
                    FaceObservation(
                        bbox=(bbox[0], bbox[1], bbox[2], bbox[3]),
                        embedding=embedding / norm,
                        photo=face_photo(frame, (bbox[0], bbox[1], bbox[2], bbox[3])),
                    )
                )
        observations.sort(key=lambda obs: obs.bbox[2] * obs.bbox[3], reverse=True)
        return observations


def _default_detector() -> Any:
    from reachy_mini.vision.face_detector import FaceDetector

    return FaceDetector()


def _default_embedder() -> Callable[[NDArray[np.float32]], NDArray[np.float32]]:
    import onnxruntime as ort
    from huggingface_hub import hf_hub_download

    model_path = hf_hub_download(SFACE_REPO, SFACE_FILE, revision=SFACE_REVISION)
    options = ort.SessionOptions()
    # One thread, like the SDK detector, so recognition never starves the motion loop.
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    options.log_severity_level = 3  # the model's constant-input warnings are harmless
    session = ort.InferenceSession(model_path, options, providers=["CPUExecutionProvider"])
    input_name = session.get_inputs()[0].name

    def embed(blob: NDArray[np.float32]) -> NDArray[np.float32]:
        return np.asarray(session.run(None, {input_name: blob})[0][0], dtype=np.float32)

    return embed


# ---------------------------------------------------------------------------
# Enrolled people
# ---------------------------------------------------------------------------


class FaceStoreError(ValueError):
    """An invalid name or a store that cannot be read or written."""


def person_id_for(name: str) -> str:
    """Return the household person ID for a spoken or typed name: trimmed, lower-case, single-spaced, no accents.

    Accents are dropped so "María" and a transcribed "Maria" are the same person; the display name keeps them.
    """
    cleaned = " ".join(str(name).split())
    if not cleaned:
        raise FaceStoreError("name must not be empty")
    if len(cleaned) > MAX_NAME_CHARS:
        raise FaceStoreError(f"name must be at most {MAX_NAME_CHARS} characters")
    if any(ord(ch) < 32 for ch in cleaned):
        raise FaceStoreError("name must not contain control characters")
    decomposed = unicodedata.normalize("NFKD", cleaned.casefold())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


@dataclass(frozen=True)
class FaceMatch:
    """A recognized enrolled person and how closely the face matched (cosine similarity)."""

    person_id: str
    name: str
    score: float


@dataclass
class _Sample:
    embedding: NDArray[np.float32]
    photo: str | None


@dataclass
class _Person:
    person_id: str
    name: str
    created_at: str
    updated_at: str
    samples: list[_Sample] = field(default_factory=list)


@dataclass(frozen=True)
class EnrolledPerson:
    """An enrolled person as shown to tools and the settings page."""

    person_id: str
    name: str
    sample_count: int
    photos: tuple[str, ...]
    created_at: str
    updated_at: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class FaceRegistry:
    """Enrolled faces on disk: ``faces.json`` (fingerprints) plus ``photos/*.jpg``, owner-only permissions."""

    def __init__(self, directory: Path) -> None:
        """Use ``directory`` (created on first write) for the store."""
        self._directory = Path(directory)
        self._lock = threading.RLock()
        self._people: dict[str, _Person] | None = None

    @property
    def directory(self) -> Path:
        """Folder holding ``faces.json`` and ``photos/``."""
        return self._directory

    def people(self) -> list[EnrolledPerson]:
        """Return everyone enrolled, by name."""
        with self._lock:
            people = self._load()
            return sorted((self._public(p) for p in people.values()), key=lambda p: p.name.casefold())

    def photo_path(self, person_id: str, photo: str) -> Path | None:
        """Return the file for one of a person's photos, or None if it is not theirs."""
        with self._lock:
            person = self._load().get(person_id)
            if person is None or photo not in {s.photo for s in person.samples if s.photo}:
                return None
            path = self._directory / PHOTOS_DIR / photo
            return path if path.is_file() else None

    def enroll(self, name: str, samples: list[tuple[NDArray[np.float32], bytes | None]]) -> EnrolledPerson:
        """Add fingerprints (and optional JPEG photos) for ``name``, keeping the newest samples."""
        if not samples:
            raise FaceStoreError("no face samples to enroll")
        person_id = person_id_for(name)
        display_name = " ".join(str(name).split())
        with self._lock:
            people = self._load()
            now = _now_iso()
            # A later enrollment heard without accents ("Maria") keeps the first spelling ("María").
            person = people.get(person_id) or _Person(person_id, display_name, now, now)
            person.updated_at = now
            photos_dir = self._directory / PHOTOS_DIR
            for embedding, jpeg in samples:
                photo_name = None
                if jpeg:
                    photos_dir.mkdir(parents=True, exist_ok=True)
                    os.chmod(self._directory, 0o700)
                    os.chmod(photos_dir, 0o700)
                    photo_name = f"{uuid.uuid4().hex}.jpg"
                    _write_private(photos_dir / photo_name, jpeg)
                vector = np.asarray(embedding, dtype=np.float32).reshape(-1)
                person.samples.append(_Sample(vector / max(float(np.linalg.norm(vector)), 1e-9), photo_name))
            removed = person.samples[:-MAX_SAMPLES_PER_PERSON]
            person.samples = person.samples[-MAX_SAMPLES_PER_PERSON:]
            people[person_id] = person
            self._save(people)
            self._delete_photos(removed)
            return self._public(person)

    def forget(self, person_id_or_name: str) -> EnrolledPerson | None:
        """Delete a person's fingerprints and photos; return who was removed, or None."""
        person_id = person_id_for(person_id_or_name)
        with self._lock:
            people = self._load()
            person = people.pop(person_id, None)
            if person is None:
                return None
            self._save(people)
            self._delete_photos(person.samples)
            return self._public(person)

    def forget_photo(self, person_id: str, photo: str) -> tuple[bool, EnrolledPerson | None]:
        """Delete one enrolled sample (fingerprint and photo); return (found, the person afterwards).

        Removing a person's last sample removes the person, so the second value is then None.
        """
        with self._lock:
            people = self._load()
            person = people.get(person_id)
            if person is None:
                return False, None
            removed = [sample for sample in person.samples if sample.photo == photo]
            if not removed:
                return False, self._public(person)
            person.samples = [sample for sample in person.samples if sample.photo != photo]
            if person.samples:
                person.updated_at = _now_iso()
            else:
                people.pop(person_id)
            self._save(people)
            self._delete_photos(removed)
            return True, self._public(person) if person.samples else None

    def match(self, embedding: NDArray[np.float32]) -> FaceMatch | None:
        """Return the enrolled person this fingerprint belongs to, or None when unknown or ambiguous."""
        with self._lock:
            people = list(self._load().values())
        scored = []
        for person in people:
            if person.samples:
                matrix = np.stack([s.embedding for s in person.samples])
                scored.append((float(np.max(matrix @ embedding)), person))
        if not scored:
            return None
        scored.sort(key=lambda item: item[0], reverse=True)
        best_score, best = scored[0]
        runner_up = scored[1][0] if len(scored) > 1 else -1.0
        if best_score < MATCH_THRESHOLD or best_score - runner_up < MATCH_MARGIN:
            return None
        return FaceMatch(best.person_id, best.name, best_score)

    def _public(self, person: _Person) -> EnrolledPerson:
        return EnrolledPerson(
            person_id=person.person_id,
            name=person.name,
            sample_count=len(person.samples),
            photos=tuple(s.photo for s in person.samples if s.photo),
            created_at=person.created_at,
            updated_at=person.updated_at,
        )

    def _load(self) -> dict[str, _Person]:
        if self._people is not None:
            return self._people
        path = self._directory / FACES_FILE
        people: dict[str, _Person] = {}
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as error:
                raise FaceStoreError(f"cannot read {path.name}: {type(error).__name__}") from error
            if payload.get("model") != MODEL_ID:
                logger.warning("Enrolled faces use another recognition model; re-enrollment is needed")
            else:
                for raw in payload.get("people", []):
                    person = _Person(
                        person_id=str(raw["person_id"]),
                        name=str(raw["name"]),
                        created_at=str(raw.get("created_at", "")),
                        updated_at=str(raw.get("updated_at", "")),
                        samples=[
                            _Sample(np.asarray(s["embedding"], dtype=np.float32), s.get("photo"))
                            for s in raw.get("samples", [])
                        ],
                    )
                    people[person.person_id] = person
        self._people = people
        return people

    def _save(self, people: dict[str, _Person]) -> None:
        payload = {
            "version": STORE_VERSION,
            "model": MODEL_ID,
            "people": [
                {
                    "person_id": p.person_id,
                    "name": p.name,
                    "created_at": p.created_at,
                    "updated_at": p.updated_at,
                    "samples": [
                        {"embedding": [round(float(v), 6) for v in s.embedding], "photo": s.photo} for s in p.samples
                    ],
                }
                for p in people.values()
            ],
        }
        self._directory.mkdir(parents=True, exist_ok=True)
        os.chmod(self._directory, 0o700)
        _write_private(self._directory / FACES_FILE, json.dumps(payload, indent=1).encode("utf-8"))
        self._people = people

    def _delete_photos(self, samples: list[_Sample]) -> None:
        for sample in samples:
            if sample.photo and re.fullmatch(r"[0-9a-f]{32}\.jpg", sample.photo):
                try:
                    (self._directory / PHOTOS_DIR / sample.photo).unlink(missing_ok=True)
                except OSError as error:
                    logger.warning("Could not delete a face photo: %s", type(error).__name__)


def _write_private(path: Path, data: bytes) -> None:
    """Atomically write ``data`` to ``path`` readable only by this user."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Live recognition
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SeenPerson:
    """Someone recognized on camera and when."""

    match: FaceMatch
    seen_at: float


@dataclass(frozen=True)
class EnrollResult:
    """Outcome of enrolling from the camera."""

    person: EnrolledPerson | None
    error: str | None = None
    # Uploaded photos that were not used, and why.
    skipped: tuple[str, ...] = ()


class FaceRecognitionService:
    """Recognize the person in front of the camera about once a second while a conversation is open."""

    def __init__(
        self,
        capture_frame: Callable[[], NDArray[np.uint8] | None],
        registry: FaceRegistry,
        analyzer: FaceAnalyzer | None = None,
        *,
        encode_jpeg: Callable[[NDArray[np.uint8]], bytes | None] | None = None,
        decode_image: Callable[[bytes], NDArray[np.uint8] | None] | None = None,
        interval_s: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Read frames with ``capture_frame`` and match them against ``registry``."""
        self._capture_frame = capture_frame
        self._registry = registry
        self._analyzer = analyzer or FaceAnalyzer()
        self._encode_jpeg = encode_jpeg
        self._decode_image = decode_image
        self._interval_s = interval_s
        self._clock = clock
        self._sleep = sleep
        self._active = threading.Event()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._state_lock = threading.Lock()
        self._in_front: SeenPerson | None = None
        self._weak_streak: tuple[str | None, int] = (None, 0)
        self._ready = threading.Event()

    @property
    def registry(self) -> FaceRegistry:
        """The enrolled-people store."""
        return self._registry

    @property
    def ready(self) -> bool:
        """Whether the models have loaded."""
        return self._ready.is_set()

    def start(self) -> None:
        """Start the background thread (models load there, off the startup path)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="face-recognition", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop recognizing and wait for the thread."""
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

    def set_active(self, active: bool) -> None:
        """Recognize continuously while ``active`` (a conversation is open); idle otherwise."""
        if active:
            self._active.set()
            self._wake.set()
        else:
            self._active.clear()
            with self._state_lock:
                self._in_front = None

    def person_in_front(self, max_age_s: float = 10.0) -> SeenPerson | None:
        """Return who was confidently recognized in front of the camera within ``max_age_s`` seconds."""
        with self._state_lock:
            seen = self._in_front
        if seen is None or self._clock() - seen.seen_at > max_age_s:
            return None
        return seen

    def identity_hint(self) -> str | None:
        """Return one line for the conversation context naming the recognized person, or None."""
        seen = self.person_in_front()
        if seen is None:
            return None
        return (
            f"The camera recognizes the person in front of you as {seen.match.name} (an enrolled household member). "
            "Use their name naturally; if someone else may be speaking, check with the who_is_here tool."
        )

    def identify_now(self) -> list[FaceMatch | None]:
        """Recognize every face in the current frame (largest first; None = not enrolled); blocking."""
        frame = self._capture_frame()
        if frame is None:
            raise RuntimeError("camera frame unavailable")
        observations = self._analyzer.analyze(frame)
        matches = [self._registry.match(obs.embedding) for obs in observations]
        self._record(matches)
        return matches

    def enroll_from_camera(
        self, name: str, duration_s: float = 3.0, max_samples: int = 6, *, trust_name: bool = False
    ) -> EnrollResult:
        """Capture the single face in view for ``duration_s`` and enroll it as ``name``; blocking.

        ``trust_name`` (the settings page, where someone typed the name) allows a face that resembles
        another enrolled person, such as a sibling; the voice tool keeps refusing it.
        """
        try:
            person_id_for(name)
        except FaceStoreError as error:
            return EnrollResult(None, str(error))
        samples: list[tuple[NDArray[np.float32], bytes | None]] = []
        crowded = False
        deadline = self._clock() + duration_s
        step = duration_s / max(max_samples, 1)
        while self._clock() < deadline and len(samples) < max_samples:
            frame = self._capture_frame()
            if frame is not None:
                faces = self._analyzer.analyze(frame, min_face_px=MIN_ENROLL_FACE_PX, whole_faces_only=True)
                if len(faces) == 1:
                    jpeg = self._encode_jpeg(faces[0].photo) if self._encode_jpeg is not None else None
                    samples.append((faces[0].embedding, jpeg))
                elif len(faces) > 1:
                    crowded = True
            self._sleep(step)
        if len(samples) < 2:
            if crowded:
                return EnrollResult(None, "more than one face was in view; only the person enrolling should be")
            return EnrollResult(None, "no clear face in view; face the robot from about an arm's length to 2 m away")
        return self._save_samples(name, samples, trust_name=trust_name)

    def enroll_from_photos(self, name: str, photos: list[bytes], *, trust_name: bool = False) -> EnrollResult:
        """Enroll ``name`` from uploaded JPEG/PNG photos, each showing only that person; blocking.

        Photos without exactly one clear face are skipped (and reported); all usable photos must show
        the same person. ``trust_name`` is as for :meth:`enroll_from_camera`.
        """
        try:
            person_id_for(name)
        except FaceStoreError as error:
            return EnrollResult(None, str(error))
        if self._decode_image is None:
            return EnrollResult(None, "photo upload is unavailable")
        if not photos or len(photos) > MAX_UPLOAD_PHOTOS:
            return EnrollResult(None, f"upload between 1 and {MAX_UPLOAD_PHOTOS} photos")
        candidates: list[tuple[int, NDArray[np.float32], bytes | None]] = []
        skipped: list[str] = []
        for number, data in enumerate(photos, start=1):
            image = self._decode_image(data)
            if image is None:
                skipped.append(f"photo {number} could not be read")
                continue
            faces = self._analyzer.analyze(image, min_face_px=MIN_ENROLL_FACE_PX, whole_faces_only=True)
            if len(faces) != 1:
                reason = "no clear, uncropped face" if not faces else "more than one face"
                skipped.append(f"photo {number} has {reason}")
                continue
            jpeg = self._encode_jpeg(faces[0].photo) if self._encode_jpeg is not None else None
            candidates.append((number, faces[0].embedding, jpeg))
        if not candidates:
            return EnrollResult(None, "; ".join(skipped) or "no usable photos", skipped=tuple(skipped))
        # Keep the largest group of photos that look like the same person; an odd one out is skipped.
        agreeing = [
            [other for other in candidates if float(other[1] @ candidate[1]) >= SAME_PERSON_MIN_SIMILARITY]
            for candidate in candidates
        ]
        group = max(agreeing, key=len)
        if len(group) * 2 <= len(candidates) and len(candidates) > 1:
            return EnrollResult(None, "the photos seem to show different people", skipped=tuple(skipped))
        kept = {number for number, _, _ in group}
        skipped.extend(
            f"photo {number} does not look like the same person" for number, _, _ in candidates if number not in kept
        )
        skipped.sort(key=lambda reason: int(reason.split()[1]))
        result = self._save_samples(name, [(embedding, jpeg) for _, embedding, jpeg in group], trust_name=trust_name)
        return EnrollResult(result.person, result.error, skipped=tuple(skipped))

    def _save_samples(
        self, name: str, samples: list[tuple[NDArray[np.float32], bytes | None]], *, trust_name: bool = False
    ) -> EnrollResult:
        existing = None if trust_name else self._registry.match(samples[0][0])
        if existing is not None and existing.person_id != person_id_for(name):
            return EnrollResult(None, f"this face is already enrolled as {existing.name}")
        return EnrollResult(self._registry.enroll(name, samples))

    def _record(self, matches: list[FaceMatch | None]) -> None:
        # The largest face is the one the robot is looking at.
        top = matches[0] if matches else None
        current = self.person_in_front()
        with self._state_lock:
            if top is None:
                self._weak_streak = (None, 0)
                return
            last_id, count = self._weak_streak
            streak = count + 1 if last_id == top.person_id else 1
            self._weak_streak = (top.person_id, streak)
        already_in_front = current is not None and current.match.person_id == top.person_id
        if top.score < CONFIDENT_MATCH_SCORE and streak < WEAK_MATCH_STREAK and not already_in_front:
            logger.info("Unsure match: possibly %s (score %.2f); not acting on it", top.name, top.score)
            return
        with self._state_lock:
            previous = self._in_front
            self._in_front = SeenPerson(top, self._clock())
        if previous is None or previous.match.person_id != top.person_id:
            how = "" if top.score >= CONFIDENT_MATCH_SCORE else f", {streak} looks in a row"
            logger.info("Recognized %s in front of the camera (score %.2f%s)", top.name, top.score, how)

    def _run(self) -> None:
        try:
            self._analyzer.load()
            self._ready.set()
            logger.info("Face recognition ready")
        except Exception as error:
            logger.warning("Face recognition unavailable: %s: %s", type(error).__name__, error)
            return
        while not self._stop.is_set():
            if not self._active.is_set():
                self._wake.wait()
                self._wake.clear()
                continue
            started = self._clock()
            if self._registry.people():
                try:
                    self.identify_now()
                except Exception as error:
                    logger.debug("Face recognition pass failed: %s", type(error).__name__)
            self._wake.wait(max(0.0, self._interval_s - (self._clock() - started)))
            self._wake.clear()
