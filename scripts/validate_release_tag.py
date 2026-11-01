"""Validate the SemVer subset supported by Docker image tags."""

import re
import sys


_IDENTIFIER = re.compile(r"[0-9A-Za-z-]+\Z")
_NUMERIC = re.compile(r"[0-9]+\Z")


def is_valid_release_tag(tag):
    if not isinstance(tag, str) or not tag.startswith("v") or "+" in tag:
        return False

    version = tag[1:]
    core, separator, prerelease = version.partition("-")
    components = core.split(".")
    if len(components) != 3:
        return False
    if any(
        not _NUMERIC.fullmatch(component)
        or (len(component) > 1 and component.startswith("0"))
        for component in components
    ):
        return False

    if not separator:
        return True
    if not prerelease:
        return False

    identifiers = prerelease.split(".")
    return all(
        _IDENTIFIER.fullmatch(identifier)
        and not (
            _NUMERIC.fullmatch(identifier)
            and len(identifier) > 1
            and identifier.startswith("0")
        )
        for identifier in identifiers
    )


def main():
    if len(sys.argv) != 2 or not is_valid_release_tag(sys.argv[1]):
        print(
            "Invalid release tag. Use vMAJOR.MINOR.PATCH with valid SemVer "
            "prerelease identifiers; build metadata is not supported.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
