"""Strict page cap and archive handling for source manuscripts."""

import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from pypdf import PdfReader

from source_upload import SourceUploadError, _extract_source, install_source


def make_zip(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()


def paper_tex(pages: int) -> bytes:
    parts = [r"\documentclass{article}", r"\begin{document}"]
    for index in range(1, pages + 1):
        if index > 1:
            parts.append(r"\newpage")
        parts.append(f"Page {index}. " + ("An independently checked result. " * 15))
    parts.append(r"\end{document}")
    return "\n".join(parts).encode()


class SourceUploadTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.latex = Path(temporary.name) / "task" / "latex"

    def test_tex_over_twelve_pages_exposes_only_first_twelve(self) -> None:
        install_source(self.latex, "paper.tex", paper_tex(15))
        self.assertEqual(len(PdfReader(self.latex / "paper-first12.pdf").pages), 12)
        text = (self.latex / "template.tex").read_text()
        self.assertIn("Page 12.", text)
        self.assertNotIn("Page 13.", text)
        self.assertEqual(len(list((self.latex / "pages").glob("page-*.png"))), 12)
        self.assertFalse((self.latex / ".source-build").exists())
        self.assertFalse((self.latex / ".pdf-build").exists())
        self.assertIn("1-12 of 15", (self.latex.parent / "source_note.txt").read_text())

    def test_zip_compiles_included_tex_without_exposing_source(self) -> None:
        main = (r"\documentclass{article}" "\n" r"\begin{document}" "\n"
                r"\input{sections/method}" "\n" r"\end{document}").encode()
        included = ("Our method has a baseline and an ablation. " * 20).encode()
        archive = make_zip({"main.tex": main, "sections/method.tex": included,
                            "figures/plot.png": b"image-is-not-referenced"})
        install_source(self.latex, "paper.zip", archive)
        self.assertEqual(len(PdfReader(self.latex / "paper-first12.pdf").pages), 1)
        self.assertIn("Our method has a baseline", (self.latex / "template.tex").read_text())
        self.assertFalse((self.latex / ".source-build").exists())
        self.assertEqual(len(list((self.latex / "pages").glob("page-*.png"))), 1)

    def test_zip_path_traversal_is_rejected(self) -> None:
        archive = make_zip({"../outside.tex": b"\\documentclass{article}"})
        with self.assertRaises(SourceUploadError):
            _extract_source(self.latex / ".source-build", "paper.zip", archive)
        self.assertFalse((self.latex.parent.parent / "outside.tex").exists())

    def test_zip_without_tex_is_rejected(self) -> None:
        with self.assertRaises(SourceUploadError):
            _extract_source(self.latex / ".source-build", "paper.zip",
                            make_zip({"figure.png": b"image"}))


if __name__ == "__main__":
    unittest.main()
