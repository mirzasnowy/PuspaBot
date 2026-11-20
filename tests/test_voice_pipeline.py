"""Unit tests for the voice pipeline and the WER harness (P-03).

No database, no network: the ``unit`` marker keeps the Postgres
setup out of this module entirely.
"""

from pathlib import Path

import pytest

from puspa.voice.pipeline import (
    AudioValidationError,
    VoicePipeline,
    validate_audio,
)
from puspa.voice.transcriber import (
    MappingTranscriber,
    StubTranscriber,
    available_transcribers,
    resolve_transcriber,
)
from puspa.voice.wer import (
    REQUIRED_CASES,
    WerCase,
    load_cases,
    measure_wer,
    word_error_rate,
)

pytestmark = pytest.mark.unit

FAKE_AUDIO = b"fake-recording-bytes"
FAKE_MIME = "audio/webm"


# --- word error rate -------------------------------------------------------


def test_wer_identical_texts_score_zero():
    assert word_error_rate("halo puspa", "halo puspa") == 0.0


def test_wer_is_case_insensitive_and_ignores_extra_spaces():
    assert word_error_rate("Halo  Puspa", "halo puspa") == 0.0


def test_wer_one_substitution_in_four_words_is_a_quarter():
    assert word_error_rate("tolong cek harga beras", "tolong cek harga gula") == 0.25


def test_wer_deletion_counts_as_one_error():
    assert word_error_rate("tolong cek harga beras", "tolong harga beras") == 0.25


def test_wer_insertion_counts_as_one_error():
    assert word_error_rate("cek harga", "cek harga dong") == pytest.approx(0.5)


def test_wer_empty_reference_scores_zero_only_against_empty_hypothesis():
    assert word_error_rate("", "") == 0.0
    assert word_error_rate("", "halo") == 1.0


# --- measurement harness ---------------------------------------------------


def test_measure_wer_scores_each_case_and_totals_by_words(tmp_path):
    first = tmp_path / "a.webm"
    first.write_bytes(b"audio-one")
    second = tmp_path / "b.webm"
    second.write_bytes(b"audio-two")
    cases = [
        WerCase(first, "tolong cek harga beras", label="a"),
        WerCase(second, "tolong cek harga beras", label="b"),
    ]
    transcriber = MappingTranscriber(
        {b"audio-one": "tolong cek harga beras", b"audio-two": "tolong cek harga gula"}
    )
    report = measure_wer(cases, transcriber)
    assert report.cases_measured == 2
    assert report.per_case[0].wer == 0.0
    assert report.per_case[1].wer == 0.25
    # Overall is total errors over total reference words: 1 / 8.
    assert report.overall_wer == pytest.approx(0.125)
    assert report.selection_ready is False
    assert "too few" in report.verdict


def test_measure_wer_with_fifty_perfect_cases_is_selection_ready(tmp_path):
    cases = []
    for index in range(REQUIRED_CASES):
        audio_path = tmp_path / f"case-{index:02d}.webm"
        audio_path.write_bytes(b"same-audio")
        cases.append(WerCase(audio_path, "halo puspa"))
    report = measure_wer(cases, StubTranscriber("halo puspa"))
    assert report.cases_measured == REQUIRED_CASES
    assert report.overall_wer == 0.0
    assert report.selection_ready is True
    assert "large enough" in report.verdict


def test_load_cases_pairs_audio_with_reference_and_skips_unpaired(tmp_path):
    (tmp_path / "one.webm").write_bytes(b"audio")
    (tmp_path / "one.txt").write_text("halo puspa", encoding="utf-8")
    (tmp_path / "two.wav").write_bytes(b"audio")
    (tmp_path / "two.txt").write_text("cek harga", encoding="utf-8")
    (tmp_path / "lonely.webm").write_bytes(b"audio")  # no .txt: skipped
    (tmp_path / "orphan.txt").write_text("no audio", encoding="utf-8")
    cases = load_cases(tmp_path)
    assert [case.label for case in cases] == ["one", "two"]
    assert cases[0].reference_text == "halo puspa"


def test_load_cases_on_empty_directory_returns_empty_list(tmp_path):
    assert load_cases(tmp_path) == []


# --- transcriber seam ------------------------------------------------------


def test_resolve_transcriber_returns_the_stub_by_name():
    transcriber = resolve_transcriber("stub", text="halo")
    assert isinstance(transcriber, StubTranscriber)
    assert transcriber.transcribe(FAKE_AUDIO, mime_type=FAKE_MIME).text == "halo"


def test_resolve_transcriber_refuses_unknown_names_instead_of_guessing():
    with pytest.raises(ValueError, match="unknown transcriber"):
        resolve_transcriber("whisper-large-v3")
    assert "stub" in available_transcribers()


def test_mapping_transcriber_falls_back_to_empty_for_unknown_audio():
    transcriber = MappingTranscriber({b"known": "halo"})
    assert transcriber.transcribe(b"unknown", mime_type=FAKE_MIME).text == ""


# --- upload pipeline -------------------------------------------------------


def test_pipeline_transcribes_and_deletes_audio_by_default():
    pipeline = VoicePipeline(StubTranscriber("tolong cek harga beras"))
    result = pipeline.transcribe_upload(FAKE_AUDIO, FAKE_MIME)
    assert result.transcript == "tolong cek harga beras"
    assert result.language == "id"
    assert result.engine == "stub"
    assert result.audio_deleted is True
    assert result.audio_path is None
    assert result.size_bytes == len(FAKE_AUDIO)


def test_pipeline_keeps_audio_only_when_asked_and_only_under_a_random_name(tmp_path):
    pipeline = VoicePipeline(StubTranscriber("halo"))
    result = pipeline.transcribe_upload(FAKE_AUDIO, FAKE_MIME, keep_audio=True, keep_dir=tmp_path)
    assert result.audio_deleted is False
    assert result.audio_path is not None
    kept = Path(result.audio_path)
    assert kept.parent == tmp_path
    assert kept.name != "recording.webm"
    assert kept.read_bytes() == FAKE_AUDIO


def test_pipeline_keep_audio_without_a_directory_is_rejected():
    pipeline = VoicePipeline(StubTranscriber("halo"))
    with pytest.raises(AudioValidationError, match="keep_dir"):
        pipeline.transcribe_upload(FAKE_AUDIO, FAKE_MIME, keep_audio=True)


def test_validate_audio_rejects_wrong_type_empty_and_oversize():
    with pytest.raises(AudioValidationError, match="unsupported audio type"):
        validate_audio(FAKE_AUDIO, "video/mp4")
    with pytest.raises(AudioValidationError, match="empty audio"):
        validate_audio(b"", FAKE_MIME)
    with pytest.raises(AudioValidationError, match="over the"):
        validate_audio(b"x" * (10 * 1024 * 1024 + 1), FAKE_MIME)


def test_pipeline_rejects_a_bad_upload_without_transcribing():
    class CountingTranscriber(StubTranscriber):
        calls = 0

        def transcribe(self, audio, *, mime_type):
            CountingTranscriber.calls += 1
            return super().transcribe(audio, mime_type=mime_type)

    pipeline = VoicePipeline(CountingTranscriber("halo"))
    with pytest.raises(AudioValidationError):
        pipeline.transcribe_upload(FAKE_AUDIO, "video/mp4")
    assert CountingTranscriber.calls == 0
