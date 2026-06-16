"""测试 benchmark hashing utilities."""
import tempfile
from pathlib import Path

from autoad_researcher.benchmarks.hashing import canonical_sha256, sha256_file


class TestSha256File:
    def test_same_content_same_hash(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"hello")
        try:
            h1 = sha256_file(Path(f.name))
            h2 = sha256_file(Path(f.name))
            assert h1 == h2
            assert len(h1) == 64
        finally:
            Path(f.name).unlink()

    def test_different_content_different_hash(self):
        with tempfile.NamedTemporaryFile(delete=False) as f1:
            f1.write(b"hello")
        with tempfile.NamedTemporaryFile(delete=False) as f2:
            f2.write(b"world")
        try:
            assert sha256_file(Path(f1.name)) != sha256_file(Path(f2.name))
        finally:
            Path(f1.name).unlink()
            Path(f2.name).unlink()


class TestCanonicalSha256:
    def test_same_input_same_hash(self):
        assert canonical_sha256({"a": 1}) == canonical_sha256({"a": 1})

    def test_order_independent(self):
        assert canonical_sha256({"b": 2, "a": 1}) == canonical_sha256({"a": 1, "b": 2})

    def test_different_input_different_hash(self):
        assert canonical_sha256({"a": 1}) != canonical_sha256({"a": 2})
