"""Fixture: build() returns something that is not a shape."""
PARAMETERS = {"size": 10.0}


def build(p):
    return 42
