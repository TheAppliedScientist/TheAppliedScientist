"""Render untrusted LaTeX in isolation and expose only its first 12 pages.

The source tree exists only during compilation. The Reviewer receives page text,
page images, and a trimmed PDF, never the uncapped source or compiled output.
"""

from __future__ import annotations

import io
import os
import resource
import shutil
import stat
import subprocess
import zipfile
from pathlib import Path, PurePosixPath

from pypdf import PdfReader, PdfWriter


MAX_ARCHIVE_BYTES = 20 * 1024 * 1024
MAX_EXTRACTED_BYTES = 60 * 1024 * 1024
MAX_FILES = 200
MAX_PAGES = 12
SOURCE_SUFFIXES = {".tex", ".bib", ".bbl", ".sty", ".cls", ".bst",
                   ".bbx", ".cbx", ".pdf", ".png", ".jpg", ".jpeg",
                   ".webp", ".svg", ".eps"}


class SourceUploadError(ValueError):
    """The source cannot be safely rendered and reviewed."""


def _valid_member(name: str) -> PurePosixPath:
    if not name or "\\" in name or "\x00" in name or len(name) > 240:
        raise SourceUploadError("The zip contains an unsafe file path")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in name.split("/") or len(path.parts) > 10:
        raise SourceUploadError("The zip contains an unsafe file path")
    return path


def _main_tex(files: dict[PurePosixPath, bytes]) -> PurePosixPath:
    tex_files = [path for path in files if path.suffix.lower() == ".tex"]
    if not tex_files:
        raise SourceUploadError("The zip has no .tex manuscript")
    with_documentclass = [path for path in tex_files
                          if b"\\documentclass" in files[path][:200_000]]
    candidates = with_documentclass or tex_files
    preferred = {"main.tex": 0, "paper.tex": 1, "manuscript.tex": 2,
                 "template.tex": 3}
    return min(candidates, key=lambda path: (preferred.get(path.name.lower(), 4),
                                             len(path.parts), str(path)))


def _extract_source(source_dir: Path, filename: str, data: bytes) -> PurePosixPath:
    """Write only regular, relevant files beneath a new temporary directory."""
    if not data or len(data) > MAX_ARCHIVE_BYTES:
        raise SourceUploadError("Source upload must be between 1 byte and 20 MB")
    source_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(filename).suffix.lower()
    if suffix == ".tex":
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise SourceUploadError("TeX source must be UTF-8") from exc
        if len(text.strip()) < 200:
            raise SourceUploadError("TeX source is too short to review")
        (source_dir / "main.tex").write_text(text, encoding="utf-8")
        return PurePosixPath("main.tex")
    if suffix != ".zip":
        raise SourceUploadError("Upload a .tex file or LaTeX .zip project")

    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (OSError, zipfile.BadZipFile) as exc:
        raise SourceUploadError("The uploaded zip could not be read") from exc
    with archive:
        members = [item for item in archive.infolist() if not item.is_dir()]
        if not members or len(members) > MAX_FILES:
            raise SourceUploadError("The zip must contain 1 to 200 files")
        if sum(item.file_size for item in members) > MAX_EXTRACTED_BYTES:
            raise SourceUploadError("The extracted zip is larger than 60 MB")
        files: dict[PurePosixPath, bytes] = {}
        for item in members:
            path = _valid_member(item.filename)
            mode = item.external_attr >> 16
            if stat.S_ISLNK(mode) or item.flag_bits & 0x1:
                raise SourceUploadError("Symlinks and encrypted zip entries are not accepted")
            if path in files:
                raise SourceUploadError("The zip contains duplicate file paths")
            if item.file_size > MAX_ARCHIVE_BYTES:
                raise SourceUploadError("A file in the zip is larger than 20 MB")
            if path.suffix.lower() not in SOURCE_SUFFIXES:
                continue
            try:
                with archive.open(item) as member:
                    content = member.read(MAX_ARCHIVE_BYTES + 1)
            except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                raise SourceUploadError("A file in the zip could not be read") from exc
            if len(content) > MAX_ARCHIVE_BYTES:
                raise SourceUploadError("A file in the zip is larger than 20 MB")
            files[path] = content
        main = _main_tex(files)
        for path, content in files.items():
            target = source_dir.joinpath(*path.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
    return main


def _limit_compiler() -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (120, 120))
    resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
    resource.setrlimit(resource.RLIMIT_FSIZE, (100 * 1024**2, 100 * 1024**2))
    resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))


def _compile_pdf(source_dir: Path, build_dir: Path, main: PurePosixPath) -> Path:
    """Compile without network, shell escape, host home, or service credentials."""
    bwrap = os.environ.get("TAS_BWRAP_BIN") or shutil.which("bwrap")
    pdflatex = shutil.which("pdflatex")
    if not bwrap or not pdflatex:
        raise SourceUploadError("TeX review is not configured on this server")
    build_dir.mkdir(parents=True, exist_ok=True)
    command = [bwrap, "--unshare-user", "--unshare-pid", "--unshare-net",
               "--unshare-ipc", "--unshare-uts", "--die-with-parent",
               "--new-session", "--ro-bind", "/usr", "/usr", "--ro-bind",
               "/bin", "/bin", "--ro-bind", "/lib", "/lib", "--ro-bind",
               "/lib64", "/lib64", "--dev", "/dev", "--proc", "/proc",
               "--tmpfs", "/tmp", "--dir", "/work",
               "--ro-bind", str(source_dir), "/work/source",
               "--bind", str(build_dir), "/work/out"]
    for path in ("/etc/texmf", "/etc/fonts", "/var/lib/texmf"):
        if Path(path).exists():
            command += ["--ro-bind", path, path]
    relative_dir = str(main.parent)
    sandbox_cwd = "/work/source" if relative_dir == "." else f"/work/source/{relative_dir}"
    command += ["--chdir", sandbox_cwd,
                "--setenv", "HOME", "/work/out",
                "--setenv", "TEXMFVAR", "/work/out/texmf-var",
                "--setenv", "TEXMFCONFIG", "/work/out/texmf-config",
                "--", pdflatex, "-interaction=nonstopmode", "-halt-on-error",
                "-file-line-error", "-no-shell-escape", "-jobname=review-paper",
                "-output-directory=/work/out", main.name]
    # A minimal environment means uploaded TeX cannot read API keys via \input.
    env = {"PATH": "/usr/bin:/bin", "HOME": "/work/out", "LANG": "C.UTF-8"}
    for _ in range(2):
        try:
            completed = subprocess.run(command, env=env, capture_output=True,
                                       timeout=150, preexec_fn=_limit_compiler, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SourceUploadError("LaTeX compilation timed out or could not start") from exc
        if completed.returncode != 0:
            raise SourceUploadError(
                "LaTeX compilation failed in the isolated runner; upload a PDF "
                "if this project needs unsupported packages or external files"
            )
    pdf = build_dir / "review-paper.pdf"
    if not pdf.is_file() or pdf.stat().st_size == 0:
        raise SourceUploadError("LaTeX compilation produced no PDF")
    return pdf


def _expose_first_pages(pdf: Path, latex_dir: Path) -> int:
    try:
        reader = PdfReader(pdf, strict=False)
        count = len(reader.pages)
        if count < 1:
            raise SourceUploadError("LaTeX compilation produced an empty PDF")
        pages = min(count, MAX_PAGES)
        writer = PdfWriter()
        for page in reader.pages[:pages]:
            writer.add_page(page)
        limited_pdf = latex_dir / "paper-first12.pdf"
        with limited_pdf.open("wb") as target:
            writer.write(target)
    except SourceUploadError:
        raise
    except Exception as exc:
        raise SourceUploadError("The compiled PDF could not be page-limited") from exc

    try:
        text = subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8",
                               str(limited_pdf), "-"], capture_output=True,
                              timeout=45, check=True).stdout.decode("utf-8", errors="replace")
        page_text = text.split("\f")
        if len(page_text) < pages:
            raise SourceUploadError("Could not extract all compiled pages")
        rendered = "".join(f"{{{index}}}------------------------------------------------\n{page_text[index]}\n"
                           for index in range(pages))
        if len(rendered.strip()) < 200:
            raise SourceUploadError("Compiled pages contain too little readable text")
        (latex_dir / "template.tex").write_text(rendered, encoding="utf-8")
        image_dir = latex_dir / "pages"
        image_dir.mkdir(exist_ok=True)
        subprocess.run(["pdftoppm", "-f", "1", "-l", str(pages), "-scale-to",
                        "1400", "-png", str(limited_pdf), str(image_dir / "page")],
                       capture_output=True, timeout=90, check=True)
        if len(list(image_dir.glob("page-*.png"))) != pages:
            raise SourceUploadError("Could not render all compiled pages")
    except SourceUploadError:
        raise
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise SourceUploadError("Could not prepare the first 12 compiled pages") from exc
    return count


def install_source(latex_dir: Path, filename: str, data: bytes) -> None:
    """Compile TeX or a source zip, then discard every page after page 12."""
    source_dir = latex_dir / ".source-build"
    build_dir = latex_dir / ".pdf-build"
    latex_dir.mkdir(parents=True, exist_ok=True)
    try:
        main = _extract_source(source_dir, filename, data)
        pdf = _compile_pdf(source_dir, build_dir, main)
        total_pages = _expose_first_pages(pdf, latex_dir)
        (latex_dir.parent / "source_note.txt").write_text(
            f"Only rendered pages 1-{min(total_pages, MAX_PAGES)} of {total_pages} are available at /app/latex/paper-first12.pdf and /app/latex/pages.\n"
            "Their extracted text is /app/latex/template.tex; inspect page images for equations and figures.\n",
            encoding="utf-8",
        )
    finally:
        shutil.rmtree(source_dir, ignore_errors=True)
        shutil.rmtree(build_dir, ignore_errors=True)
