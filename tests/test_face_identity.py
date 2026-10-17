import json
import stat
from types import SimpleNamespace
from pathlib import Path

import numpy as np
import pytest

from reachy_mini_conversation_app.face_identity import (
    MODEL_ID,
    MATCH_THRESHOLD,
    MAX_UPLOAD_PHOTOS,
    MAX_SAMPLES_PER_PERSON,
    FaceAnalyzer,
    FaceRegistry,
    FaceStoreError,
    FaceObservation,
    FaceRecognitionService,
    warp_affine,
    person_id_for,
    similarity_transform,
)


def _unit(*values: float) -> np.ndarray:
    vector = np.zeros(128, dtype=np.float32)
    vector[: len(values)] = values
    return vector / np.linalg.norm(vector)


ANA = _unit(1.0, 0.0)
BEN = _unit(0.0, 1.0)


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class _FakeAnalyzer:
    """Returns queued face lists, one per frame."""

    def __init__(self, frames: list[list[np.ndarray]]) -> None:
        self.frames = list(frames)
        self.loaded = False

    def load(self) -> None:
        self.loaded = True

    def analyze(
        self, frame: np.ndarray, min_face_px: float = 0, whole_faces_only: bool = False
    ) -> list[FaceObservation]:
        embeddings = self.frames.pop(0) if self.frames else []
        return [
            FaceObservation(bbox=(0.0, 0.0, 200.0 - i, 200.0), embedding=e, photo=np.zeros((4, 4, 3), np.uint8))
            for i, e in enumerate(embeddings)
        ]


def _service(tmp_path: Path, frames: list[list[np.ndarray]], clock: _Clock | None = None) -> FaceRecognitionService:
    clock = clock or _Clock()
    return FaceRecognitionService(
        lambda: np.zeros((8, 8, 3), np.uint8),
        FaceRegistry(tmp_path / "faces"),
        _FakeAnalyzer(frames),  # type: ignore[arg-type]
        encode_jpeg=lambda image: b"\xff\xd8jpeg",
        # Photos are "decodable" unless their bytes say otherwise.
        decode_image=lambda data: None if data == b"broken" else np.zeros((8, 8, 3), np.uint8),
        clock=clock,
        sleep=clock.sleep,
    )


# --- geometry -----------------------------------------------------------------


def test_similarity_transform_recovers_rotation_scale_and_shift() -> None:
    """Landmarks moved by a known similarity map back exactly onto the template."""
    template = np.array([[38.3, 51.7], [73.5, 51.5], [56.0, 71.7]])
    angle, scale, shift = np.radians(20.0), 2.5, np.array([300.0, 120.0])
    rotation = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
    observed = (template @ rotation.T) * scale + shift

    matrix = similarity_transform(observed, template)

    np.testing.assert_allclose(observed @ matrix[:, :2].T + matrix[:, 2], template, atol=1e-9)


def test_warp_affine_identity_and_shift() -> None:
    """The identity copies pixels; a shift moves them."""
    image = np.arange(10 * 10 * 3, dtype=np.uint8).reshape(10, 10, 3)
    np.testing.assert_array_equal(warp_affine(image, np.array([[1.0, 0, 0], [0, 1.0, 0]]), 8), image[:8, :8])
    shifted = warp_affine(image, np.array([[1.0, 0, -2], [0, 1.0, -1]]), 6)
    np.testing.assert_array_equal(shifted, image[1:7, 2:8])


def test_analyzer_aligns_detected_faces_and_normalizes_fingerprints() -> None:
    """Detection runs on a half-size frame; boxes come back in camera pixels; small faces are dropped."""
    seen_shapes = []

    class Detector:
        def detect(self, frame: np.ndarray) -> list[SimpleNamespace]:
            seen_shapes.append(frame.shape)
            return [
                SimpleNamespace(bbox=(10, 10, 60, 70), right_eye=(25, 30), left_eye=(55, 30), nose=(40, 45)),
                SimpleNamespace(bbox=(200, 10, 10, 12), right_eye=(203, 14), left_eye=(207, 14), nose=(205, 17)),
            ]

    blobs = []

    def embedder() -> object:
        def embed(blob: np.ndarray) -> np.ndarray:
            blobs.append(blob)
            return np.full(128, 3.0, dtype=np.float32)

        return embed

    analyzer = FaceAnalyzer(detector_factory=Detector, embedder_factory=embedder)  # type: ignore[arg-type]
    faces = analyzer.analyze(np.full((480, 640, 3), 128, np.uint8))

    assert seen_shapes == [(240, 320, 3)]
    assert len(faces) == 1
    assert faces[0].bbox == (20.0, 20.0, 120.0, 140.0)
    assert blobs[0].shape == (1, 3, 112, 112)
    assert np.linalg.norm(faces[0].embedding) == pytest.approx(1.0)
    assert faces[0].photo.shape == (160, 160, 3)


# --- store ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [("  Ana  María ", "ana maria"), ("BEN", "ben"), ("João", "joao"), ("Niño", "nino"), ("Hélène", "helene")],
)
def test_person_ids_are_lower_case_single_spaced_and_accent_free(name: str, expected: str) -> None:
    """IDs are the trimmed, single-spaced, lower-case name without accents, so transcriptions agree."""
    assert person_id_for(name) == expected


def test_a_name_heard_without_its_accent_finds_the_same_person(tmp_path: Path) -> None:
    """Enrolled as "María", forgotten as "maria": same person, and the display name keeps the accent."""
    registry = FaceRegistry(tmp_path / "faces")
    registry.enroll("María", [(ANA, None)])
    registry.enroll("Maria", [(ANA, None)])

    assert [(p.person_id, p.name, p.sample_count) for p in registry.people()] == [("maria", "María", 2)]
    removed = registry.forget("MARÍA")
    assert removed is not None and registry.people() == []


@pytest.mark.parametrize("name", ["", "   ", "x" * 61, "ana\x07ben"])
def test_invalid_names_are_rejected(name: str) -> None:
    """Empty, overlong, and control-character names cannot be enrolled."""
    with pytest.raises(FaceStoreError):
        person_id_for(name)


def test_enroll_match_persist_and_forget(tmp_path: Path) -> None:
    """Enrolled people are recognized, survive a reload, and forgetting deletes fingerprints and photos."""
    registry = FaceRegistry(tmp_path / "faces")
    ana = registry.enroll("Ana", [(ANA, b"jpeg-1"), (_unit(1.0, 0.1), b"jpeg-2")])
    registry.enroll("Ben", [(BEN, None)])

    assert ana.person_id == "ana" and ana.sample_count == 2 and len(ana.photos) == 2
    match = registry.match(_unit(1.0, 0.05))
    assert match is not None and match.name == "Ana" and match.score > MATCH_THRESHOLD

    reloaded = FaceRegistry(tmp_path / "faces")
    assert [p.name for p in reloaded.people()] == ["Ana", "Ben"]
    photo = reloaded.photo_path("ana", ana.photos[0])
    assert photo is not None and photo.read_bytes() == b"jpeg-1"
    assert reloaded.photo_path("ben", ana.photos[0]) is None

    removed = reloaded.forget("ANA")
    assert removed is not None and removed.name == "Ana"
    assert not photo.exists()
    assert reloaded.match(ANA) is None
    assert reloaded.forget("ana") is None


def test_store_is_private_and_tagged_with_the_model(tmp_path: Path) -> None:
    """Fingerprints and photos are readable only by this user; the file names the model that made them."""
    registry = FaceRegistry(tmp_path / "faces")
    person = registry.enroll("Ana", [(ANA, b"jpeg")])

    faces_file = tmp_path / "faces" / "faces.json"
    assert stat.S_IMODE(faces_file.stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "faces").stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "faces" / "photos" / person.photos[0]).stat().st_mode) == 0o600
    assert json.loads(faces_file.read_text())["model"] == MODEL_ID


def test_fingerprints_from_another_model_are_ignored(tmp_path: Path) -> None:
    """A store written by a different recognition model is not trusted for matching."""
    (tmp_path / "faces").mkdir()
    (tmp_path / "faces" / "faces.json").write_text(
        json.dumps({"version": 1, "model": "other", "people": [{"person_id": "ana", "name": "Ana", "samples": []}]})
    )
    assert FaceRegistry(tmp_path / "faces").people() == []


def test_unknown_and_ambiguous_faces_do_not_match(tmp_path: Path) -> None:
    """Faces below the threshold, or close to two people, stay unrecognized."""
    registry = FaceRegistry(tmp_path / "faces")
    registry.enroll("Ana", [(ANA, None)])
    registry.enroll("Ben", [(BEN, None)])

    assert registry.match(_unit(0.0, 0.0, 1.0)) is None
    assert registry.match(_unit(1.0, 1.0)) is None


def test_only_the_newest_samples_are_kept(tmp_path: Path) -> None:
    """Re-enrolling adds samples up to a cap, deleting the oldest photos."""
    registry = FaceRegistry(tmp_path / "faces")
    first = registry.enroll("Ana", [(ANA, b"old")])
    person = registry.enroll("Ana", [(ANA, b"new")] * MAX_SAMPLES_PER_PERSON)

    assert person.sample_count == MAX_SAMPLES_PER_PERSON
    assert first.photos[0] not in person.photos
    assert not (tmp_path / "faces" / "photos" / first.photos[0]).exists()


# --- live service -----------------------------------------------------------------


def test_enroll_from_camera_needs_one_clear_face(tmp_path: Path) -> None:
    """Enrollment keeps single-face frames; crowds and empty views fail with a reason."""
    ok = _service(tmp_path / "a", [[ANA], [], [ANA, BEN], [_unit(1.0, 0.1)]])
    result = ok.enroll_from_camera("Ana", duration_s=4.0, max_samples=4)
    assert result.person is not None and result.person.sample_count == 2

    crowded = _service(tmp_path / "b", [[ANA, BEN]] * 6)
    assert "more than one face" in (crowded.enroll_from_camera("Ana").error or "")

    empty = _service(tmp_path / "c", [[]] * 6)
    assert "no clear face" in (empty.enroll_from_camera("Ana").error or "")


def test_enrolling_a_face_already_known_under_another_name_is_refused(tmp_path: Path) -> None:
    """One person cannot be enrolled again as someone else."""
    service = _service(tmp_path, [[ANA], [ANA]])
    service.registry.enroll("Ana", [(ANA, None)])

    result = service.enroll_from_camera("Ben", duration_s=2.0, max_samples=2)

    assert result.person is None and "already enrolled as Ana" in (result.error or "")


def test_identify_now_reports_the_person_in_front_and_expires(tmp_path: Path) -> None:
    """The largest face is who the robot faces; the recognition expires after ten seconds."""
    clock = _Clock()
    service = _service(tmp_path, [[ANA, _unit(0.0, 0.0, 1.0)]], clock)
    service.registry.enroll("Ana", [(ANA, None)])

    matches = service.identify_now()

    assert [m.name if m else None for m in matches] == ["Ana", None]
    seen = service.person_in_front()
    assert seen is not None and seen.match.person_id == "ana"
    assert "Ana" in (service.identity_hint() or "")
    clock.now += 11.0
    assert service.person_in_front() is None and service.identity_hint() is None


def test_ending_the_conversation_forgets_who_was_in_front(tmp_path: Path) -> None:
    """Recognition state does not outlive the conversation."""
    service = _service(tmp_path, [[ANA]])
    service.registry.enroll("Ana", [(ANA, None)])
    service.identify_now()

    service.set_active(False)

    assert service.person_in_front() is None


def test_background_loop_recognizes_while_active(tmp_path: Path) -> None:
    """The thread loads the models, then recognizes only while a conversation is active."""
    service = FaceRecognitionService(
        lambda: np.zeros((8, 8, 3), np.uint8),
        FaceRegistry(tmp_path / "faces"),
        _FakeAnalyzer([[ANA]] * 1000),  # type: ignore[arg-type]
        interval_s=0.01,
    )
    service.registry.enroll("Ana", [(ANA, None)])
    service.start()
    try:
        assert _wait(lambda: service.ready)
        assert service.person_in_front() is None
        service.set_active(True)
        assert _wait(lambda: service.person_in_front() is not None)
    finally:
        service.stop()


def _wait(predicate: object, timeout: float = 2.0) -> bool:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():  # type: ignore[operator]
            return True
        time.sleep(0.01)
    return False


# --- photo upload -----------------------------------------------------------------


def test_enroll_from_photos_uses_single_face_photos_and_reports_the_rest(tmp_path: Path) -> None:
    """Good photos enroll; unreadable, empty and group photos are skipped with a reason."""
    service = _service(tmp_path, [[ANA], [], [ANA, BEN], [_unit(1.0, 0.1)]])

    result = service.enroll_from_photos("Ana", [b"p1", b"broken", b"p3", b"p4", b"p5"])

    assert result.person is not None and result.person.sample_count == 2
    assert result.skipped == (
        "photo 2 could not be read",
        "photo 3 has no clear, uncropped face",
        "photo 4 has more than one face",
    )
    assert service.registry.match(ANA) is not None


def test_enroll_from_photos_refuses_mixed_people_and_unusable_uploads(tmp_path: Path) -> None:
    """Photos of different people, no usable photo, or too many photos enroll nothing."""
    mixed = _service(tmp_path / "a", [[ANA], [BEN]])
    assert mixed.enroll_from_photos("Ana", [b"1", b"2"]).error == "the photos seem to show different people"

    unusable = _service(tmp_path / "b", [[]])
    result = unusable.enroll_from_photos("Ana", [b"broken", b"empty"])
    assert result.person is None and result.error == "photo 1 could not be read; photo 2 has no clear, uncropped face"

    too_many = _service(tmp_path / "c", [])
    assert "between 1 and" in (too_many.enroll_from_photos("Ana", [b"x"] * (MAX_UPLOAD_PHOTOS + 1)).error or "")
    assert "between 1 and" in (too_many.enroll_from_photos("Ana", []).error or "")
    assert mixed.registry.people() == unusable.registry.people() == []


def test_enroll_from_photos_refuses_a_face_known_under_another_name(tmp_path: Path) -> None:
    """A photo of an enrolled person cannot enroll them under a different name."""
    service = _service(tmp_path, [[ANA]])
    service.registry.enroll("Ana", [(ANA, None)])

    assert service.enroll_from_photos("Ben", [b"1"]).error == "this face is already enrolled as Ana"


def test_settings_enrollment_trusts_the_typed_name_for_look_alikes(tmp_path: Path) -> None:
    """Siblings can resemble each other (Enzo and Mara scored 0.54); a name typed in Settings is accepted."""
    service = _service(tmp_path, [[ANA], [ANA], [ANA]])
    service.registry.enroll("Ana", [(ANA, None)])

    by_photo = service.enroll_from_photos("Ben", [b"1"], trust_name=True)
    by_camera = service.enroll_from_camera("Cleo", duration_s=2.0, max_samples=2, trust_name=True)

    assert by_photo.person is not None and by_photo.person.name == "Ben"
    assert by_camera.person is not None and by_camera.person.name == "Cleo"


def test_an_odd_photo_out_is_skipped_rather_than_failing_the_upload(tmp_path: Path) -> None:
    """Three photos of Ana and one of someone else enroll Ana and name the odd photo."""
    service = _service(tmp_path, [[ANA], [_unit(1.0, 0.2)], [BEN], [_unit(1.0, -0.2)]])

    result = service.enroll_from_photos("Ana", [b"1", b"2", b"3", b"4"])

    assert result.person is not None and result.person.sample_count == 3
    assert result.skipped == ("photo 3 does not look like the same person",)


def test_whole_faces_only_drops_faces_cut_off_by_the_edge() -> None:
    """A face running off the image gives an unreliable fingerprint, so enrollment ignores it."""

    class Detector:
        def detect(self, frame: np.ndarray) -> list[SimpleNamespace]:
            return [
                SimpleNamespace(bbox=(10, -5, 60, 70), right_eye=(25, 10), left_eye=(55, 10), nose=(40, 25)),
                SimpleNamespace(bbox=(150, 50, 60, 70), right_eye=(165, 70), left_eye=(195, 70), nose=(180, 85)),
            ]

    analyzer = FaceAnalyzer(detector_factory=Detector, embedder_factory=lambda: lambda blob: np.ones(128, np.float32))  # type: ignore[arg-type]
    frame = np.zeros((480, 640, 3), np.uint8)

    assert len(analyzer.analyze(frame)) == 2
    assert [face.bbox[0] for face in analyzer.analyze(frame, whole_faces_only=True)] == [300.0]


def test_unsure_matches_are_reported_but_not_acted_on(tmp_path: Path) -> None:
    """A barely-matching other face (Eduardo taken for his toddler at 0.41) neither names nor replaces who is in front."""
    unsure_ben = _unit(0.0, 0.45, (1 - 0.45**2) ** 0.5)
    service = _service(tmp_path, [[ANA], [unsure_ben]])
    service.registry.enroll("Ana", [(ANA, None)])
    service.registry.enroll("Ben", [(BEN, None)])

    service.identify_now()
    matches = service.identify_now()

    assert matches[0] is not None and matches[0].name == "Ben" and matches[0].score < 0.50
    seen = service.person_in_front()
    assert seen is not None and seen.match.name == "Ana" and seen.match.score == pytest.approx(1.0)


def test_a_repeated_weak_match_is_trusted_but_an_alternating_one_is_not(tmp_path: Path) -> None:
    """Three weak looks in a row at the same person count (Adriana from phone photos); flip-flopping never does."""
    weak_ana = _unit(0.45, 0.0, (1 - 0.45**2) ** 0.5)
    weak_ben = _unit(0.0, 0.45, (1 - 0.45**2) ** 0.5)
    steady = _service(tmp_path / "a", [[weak_ana]] * 3)
    flipping = _service(tmp_path / "b", [[weak_ana], [weak_ben], [weak_ana], [weak_ben], [weak_ana]])
    for service in (steady, flipping):
        service.registry.enroll("Ana", [(ANA, None)])
        service.registry.enroll("Ben", [(BEN, None)])

    for _ in range(2):
        steady.identify_now()
    assert steady.person_in_front() is None
    steady.identify_now()
    seen = steady.person_in_front()
    assert seen is not None and seen.match.name == "Ana"

    for _ in range(5):
        flipping.identify_now()
    assert flipping.person_in_front() is None


def test_weak_matches_keep_a_recognized_person_recognized(tmp_path: Path) -> None:
    """Once someone is recognized, weaker looks at the same face (turned away a little) keep the name current."""
    clock = _Clock()
    weak_ana = _unit(0.45, 0.0, (1 - 0.45**2) ** 0.5)
    service = _service(tmp_path, [[ANA], [weak_ana]], clock)
    service.registry.enroll("Ana", [(ANA, None)])
    service.registry.enroll("Ben", [(BEN, None)])

    service.identify_now()
    clock.now += 8.0
    service.identify_now()
    clock.now += 8.0

    seen = service.person_in_front()
    assert seen is not None and seen.match.name == "Ana"


def test_an_unsure_first_match_names_nobody(tmp_path: Path) -> None:
    """With only unsure matches, no speaker is credited and the model is not told a name."""
    service = _service(tmp_path, [[_unit(0.45, (1 - 0.45**2) ** 0.5)]])
    service.registry.enroll("Ana", [(ANA, None)])

    service.identify_now()

    assert service.person_in_front() is None and service.identity_hint() is None


def test_forget_photo_removes_one_sample_and_its_file(tmp_path: Path) -> None:
    """One bad photo can go without re-enrolling; the last photo takes the person with it."""
    registry = FaceRegistry(tmp_path / "faces")
    person = registry.enroll("Ana", [(ANA, b"one"), (_unit(1.0, 0.1), b"two")])
    first, second = person.photos
    photos_dir = tmp_path / "faces" / "photos"

    found, after = registry.forget_photo("ana", first)

    assert found and after is not None and after.photos == (second,) and after.sample_count == 1
    assert not (photos_dir / first).exists() and (photos_dir / second).exists()
    assert FaceRegistry(tmp_path / "faces").people()[0].photos == (second,)

    assert registry.forget_photo("ana", first) == (False, after)
    assert registry.forget_photo("ben", second) == (False, None)

    found, after = registry.forget_photo("ana", second)
    assert found and after is None
    assert registry.people() == [] and not (photos_dir / second).exists()
