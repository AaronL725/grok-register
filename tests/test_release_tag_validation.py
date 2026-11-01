"""Tests for the Docker-compatible SemVer release tag validator."""

import unittest

from scripts.validate_release_tag import is_valid_release_tag


class ReleaseTagValidationTests(unittest.TestCase):
    def test_accepts_stable_and_valid_prerelease_tags(self):
        for tag in (
            "v0.0.0",
            "v1.2.3",
            "v1.2.3-alpha",
            "v1.2.3-alpha.1",
            "v1.2.3-rc.2",
            "v1.2.3-0",
            "v1.2.3-a-1",
        ):
            with self.subTest(tag=tag):
                self.assertTrue(is_valid_release_tag(tag))

    def test_rejects_invalid_core_versions(self):
        for tag in (
            "1.2.3",
            "v01.2.3",
            "v1.02.3",
            "v1.2.03",
            "v1.2",
            "v1.2.3.4",
            "v1.x.3",
            "v1.2.3+build.1",
        ):
            with self.subTest(tag=tag):
                self.assertFalse(is_valid_release_tag(tag))

    def test_rejects_invalid_prerelease_identifiers(self):
        for tag in (
            "v1.2.3-",
            "v1.2.3-.alpha",
            "v1.2.3-alpha.",
            "v1.2.3-alpha..1",
            "v1.2.3-01",
            "v1.2.3-alpha.01",
            "v1.2.3-alpha_beta",
        ):
            with self.subTest(tag=tag):
                self.assertFalse(is_valid_release_tag(tag))

    def test_rejects_non_string_values(self):
        for value in (None, 123, b"v1.2.3"):
            with self.subTest(value=value):
                self.assertFalse(is_valid_release_tag(value))


if __name__ == "__main__":
    unittest.main()
