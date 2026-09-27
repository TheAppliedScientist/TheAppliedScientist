"""Page-cap and conversion-result checks that do not call Datalab."""

import io
import unittest

from pypdf import PdfReader, PdfWriter

from documents import DocumentError, _result_markdown, first_twelve_pdf_pages, paper_title, safe_filename


class DocumentTests(unittest.TestCase):
    def test_pdf_is_physically_trimmed_before_ocr(self) -> None:
        writer = PdfWriter()
        for _ in range(15):
            writer.add_blank_page(width=612, height=792)
        source = io.BytesIO()
        writer.write(source)
        trimmed, page_count = first_twelve_pdf_pages(source.getvalue())
        self.assertEqual(page_count, 12)
        self.assertEqual(len(PdfReader(io.BytesIO(trimmed)).pages), 12)

    def test_conversion_must_confirm_page_limit_and_complete_text(self) -> None:
        good = {"status": "complete", "success": True, "page_count": 12,
                "markdown": "paper text " * 30, "metadata": {"failed_pages": []}}
        self.assertEqual(_result_markdown(good), good["markdown"])
        with self.assertRaises(DocumentError):
            _result_markdown({**good, "page_count": 13})
        with self.assertRaises(DocumentError):
            _result_markdown({**good, "metadata": {"failed_pages": [2]}})

    def test_title_from_converted_first_page(self) -> None:
        converted = "{0}---\n\n# A Real Paper Title\n\nAuthors\n\n## Abstract"
        self.assertEqual(paper_title(converted), "A Real Paper Title")
        self.assertIsNone(paper_title("## Abstract\nOnly abstract text"))

    def test_source_formats_are_accepted_without_ocr(self) -> None:
        self.assertEqual(safe_filename("paper.tex"), "paper.tex")
        self.assertEqual(safe_filename("project.zip"), "project.zip")


if __name__ == "__main__":
    unittest.main()
