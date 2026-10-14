def test_build123d_version():
    import build123d

    assert build123d.__version__ == "0.13.0"


def test_trimesh_can_read_3mf_dependencies():
    import lxml  # noqa: F401
    import networkx  # noqa: F401
    import trimesh  # noqa: F401
