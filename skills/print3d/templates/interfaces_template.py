# Shared values: every dimension that more than one part depends on is defined here, once.
# Base values only (plain numbers or strings, mm). Derived values (inner = outer - 2 * wall) are computed in a
# part's build(), never stored here, so they follow an override.
# Parts take them with:  PARAMETERS = {**shared("wall", "pitch"), "own_param": 1.0}  (same name in the part).
# `print3d assemble <assembly.py> --set wall=2.4` changes the value in every part that uses it.
INTERFACES = {
    # "wall": 2.0,  # mm, box wall (used by base and lid)
}
