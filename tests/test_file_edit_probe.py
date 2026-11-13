"""Offline checks for MB-27 file.edit probe scoring."""
from pathlib import Path

from tools.file_edit_probe import (
    EXPECTED_MAKE,
    EXPECTED_PY,
    ORIGINAL_MAKE,
    ORIGINAL_PY,
    choose_mode,
    failure_count,
    score_project,
)


def _write_fixture(path: Path, python_bytes: bytes, makefile_bytes: bytes) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / 'nested.py').write_bytes(python_bytes)
    (path / 'Makefile').write_bytes(makefile_bytes)


def test_seeded_state_fails_and_corrected_exact_bytes_pass(tmp_path):
    seeded = tmp_path / 'seeded'
    _write_fixture(seeded, ORIGINAL_PY, ORIGINAL_MAKE)
    assert score_project(seeded) == {
        'python_exact': False,
        'makefile_exact': False,
        'exact_bytes': False,
    }

    corrected = tmp_path / 'corrected'
    _write_fixture(corrected, EXPECTED_PY, EXPECTED_MAKE)
    assert score_project(corrected) == {
        'python_exact': True,
        'makefile_exact': True,
        'exact_bytes': True,
    }


def test_failure_count_includes_call_count_forbidden_tool_and_wrong_bytes():
    result = {
        'failed_edit_calls': 1,
        'edit_calls': 2,
        'unexpected_tools': ['file.write'],
        'settled': True,
        'exact_bytes': False,
    }
    assert failure_count(result) == 4


def test_description_mode_choice_prefers_fewer_failures_and_exact_on_tie():
    assert choose_mode(exact_failures=2, line_failures=1) == 'line'
    assert choose_mode(exact_failures=1, line_failures=2) == 'exact'
    assert choose_mode(exact_failures=1, line_failures=1) == 'exact'
