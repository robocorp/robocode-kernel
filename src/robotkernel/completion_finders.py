"""Completion implementations."""

from robot.libraries import STDLIBS
from typing import List


def complete_libraries(needle: str,) -> List[str]:
    """Complete library names."""
    matches = []

    for lib in list(STDLIBS):
        if lib.lower().startswith(needle):
            matches.append(lib)

    return matches
