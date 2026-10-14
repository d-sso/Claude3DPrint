import pytest

from run_model import RunError, coerce, merge_parameters, parse_overrides

DEFAULTS = {"length": 40.0, "copies": 1, "solid": True, "label": "box"}


def test_parse_overrides_splits_on_first_equals():
    assert parse_overrides(["length=60", "label=a=b"]) == {"length": "60", "label": "a=b"}


@pytest.mark.parametrize("item", ["length", "=5", ""])
def test_parse_overrides_rejects_malformed(item):
    with pytest.raises(RunError) as err:
        parse_overrides([item])
    assert err.value.stage == "usage"


@pytest.mark.parametrize(
    "text,default,expected",
    [
        ("60", 40.0, 60.0),
        ("2.5", 40.0, 2.5),
        ("-5", 40.0, -5.0),
        ("3", 1, 3),
        ("False", True, False),
        ("lid", "box", "lid"),
        ("'lid'", "box", "lid"),
        ("True", "box", "True"),
    ],
)
def test_coerce_accepts(text, default, expected):
    value = coerce(text, default)
    assert value == expected
    assert type(value) is type(expected)


@pytest.mark.parametrize(
    "text,default",
    [("abc", 40.0), ("1e999", 40.0), ("-1e999", 40.0), ("1e999", 1), ("True", 40.0), ("2.5", 1), ("True", 1), ("1", True), ("[1]", 40.0)],
)
def test_coerce_rejects(text, default):
    with pytest.raises(ValueError):
        coerce(text, default)


def test_merge_unknown_key_lists_valid_keys():
    with pytest.raises(RunError) as err:
        merge_parameters(DEFAULTS, {"lenght": "60"})
    assert err.value.stage == "params"
    assert "lenght" in err.value.message
    assert "length (float, default 40.0)" in err.value.message


def test_merge_type_mismatch_is_params_error():
    with pytest.raises(RunError) as err:
        merge_parameters(DEFAULTS, {"copies": "2.5"})
    assert err.value.stage == "params"
    assert "copies" in err.value.message


def test_merge_returns_params_and_overrides_without_mutating_defaults():
    defaults = dict(DEFAULTS)
    params, overrides = merge_parameters(defaults, {"length": "60"})
    assert params == {**DEFAULTS, "length": 60.0}
    assert overrides == {"length": 60.0}
    assert defaults == DEFAULTS
