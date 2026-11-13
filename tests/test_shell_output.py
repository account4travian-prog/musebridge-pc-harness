import json

import pytest

from musebridge.shell_output import shape_output


def json_size(text):
    return len(json.dumps(text, ensure_ascii=True, separators=(",", ":")).encode("utf-8"))


def test_large_output_keeps_unique_head_and_tail_within_json_byte_budget():
    head = "HEAD-UNIQUE\n" + "h" * 1_000_000
    tail = "t" * 1_000_000 + "\nTAIL-UNIQUE"
    result = shape_output(head, tail, omitted_chars=17, max_bytes=20000)

    assert result["truncated"] is True
    assert result["omitted_chars"] > 17
    assert "HEAD-UNIQUE" in result["output"]
    assert "TAIL-UNIQUE" in result["output"]
    assert "Middle output omitted" in result["output"]
    assert "inspect retained log with shell.job" in result["output"]
    assert json_size(result["output"]) <= 20000


def test_contiguous_log_still_keeps_its_true_beginning_when_shaped():
    raw = 'TRUE-BEGINNING\n' + 'middle\n' * 10000 + 'TRUE-END'
    result = shape_output('', raw, max_bytes=20000)
    assert result['truncated']
    assert result['output'].startswith('TRUE-BEGINNING\n')
    assert result['output'].endswith('TRUE-END')
    assert json_size(result['output']) <= 20000


def test_strips_ansi_collapses_carriage_return_progress_and_keeps_unicode():
    raw = "\x1b]0;private title\x07\x1b[31mfirst\x1b[0m\rprogress 1\rprogress 2\nCafé 🌎"
    result = shape_output("", raw)

    assert result == {"output": "progress 2\nCafé 🌎", "truncated": False, "omitted_chars": 0}


def test_empty_output_has_explicit_success_notice():
    assert shape_output("", "") == {
        "output": "Command produced no output (exit 0).",
        "truncated": False,
        "omitted_chars": 0,
    }


def test_small_output_is_returned_whole_without_a_gap_marker():
    whole = "α first line\nsecond line 🌿"
    result = shape_output("", whole)

    assert result == {"output": whole, "truncated": False, "omitted_chars": 0}
    assert "Middle output omitted" not in result["output"]


def test_driver_gap_and_additional_clipping_are_counted_without_overlapping_edges():
    result = shape_output("HEAD-EDGE", "TAIL-EDGE", omitted_chars=12, max_bytes=160)

    assert result["truncated"] is True
    assert result["omitted_chars"] == 18
    assert result["output"].startswith("HEA")
    assert result["output"].count("TAIL-EDGE") == 1
    assert result["output"].count("Middle output omitted") == 1
    assert json_size(result["output"]) <= 160


def test_max_bytes_validation_rejects_nonpositive_or_boolean_budget():
    with pytest.raises(ValueError):
        shape_output("", "x", max_bytes=0)
    with pytest.raises(ValueError):
        shape_output("", "x", max_bytes=True)
