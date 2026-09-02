"""BibTeX generation from Paper metadata."""

from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from arxiv_search_kit.models import Paper


def _normalize_latex(text: str) -> str:
    """Normalize text for LaTeX/BibTeX compatibility."""
    replacements = {
        "&": r"\&",
        "%": r"\%",
        "#": r"\#",
        "_": r"\_",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    for char, replacement in replacements.items():
        text = text.replace(char, replacement)
    return text


def _generate_citation_key(paper: Paper) -> str:
    """Generate a citation key: {first_author_lastname}{year}{first_title_word}.

    Example: vaswani2017attention
    """
    # Extract first author's last name
    if paper.authors:
        name = paper.authors[0].name
        # Handle "First Last" and "Last, First" formats
        if "," in name:
            last_name = name.split(",")[0].strip()
        else:
            parts = name.split()
            last_name = parts[-1] if parts else "unknown"
    else:
        last_name = "unknown"

    # Clean last name: lowercase, ASCII only, no special chars
    last_name = unicodedata.normalize("NFKD", last_name)
    last_name = last_name.encode("ascii", "ignore").decode("ascii")
    last_name = re.sub(r"[^a-zA-Z]", "", last_name).lower()

    # Extract first meaningful word from title (skip articles/prepositions)
    skip_words = {"a", "an", "the", "on", "in", "of", "for", "to", "with", "and", "or", "is", "are"}
    title_words = re.findall(r"[a-zA-Z]+", paper.title.lower())
    first_word = "paper"
    for word in title_words:
        if word not in skip_words:
            first_word = word
            break

    year = paper.published.year

    return f"{last_name}{year}{first_word}"


def generate_bibtex(paper: Paper, style: str = "default") -> str:
    """Generate BibTeX entry for a paper.

    Args:
        paper: Paper metadata.
        style: BibTeX style. "default" for @article, "acl" for @inproceedings.

    Returns:
        BibTeX string.
    """
    key = _generate_citation_key(paper)
    authors_str = " and ".join(a.name for a in paper.authors)
    title = _normalize_latex(paper.title)

    if style == "acl":
        return _generate_acl_bibtex(key, title, authors_str, paper)
    else:
        return _generate_default_bibtex(key, title, authors_str, paper)


def _generate_default_bibtex(key: str, title: str, authors_str: str, paper: Paper) -> str:
    """Generate standard @article BibTeX."""
    lines = [
        f"@article{{{key},",
        f"  title     = {{{title}}},",
        f"  author    = {{{authors_str}}},",
        f"  year      = {{{paper.published.year}}},",
    ]

    if paper.journal_ref:
        lines.append(f"  journal   = {{{_normalize_latex(paper.journal_ref)}}},")

    lines.append(f"  eprint    = {{{paper.arxiv_id}}},")
    lines.append(f"  archivePrefix = {{arXiv}},")
    lines.append(f"  primaryClass  = {{{paper.primary_category}}},")

    if paper.doi:
        lines.append(f"  doi       = {{{paper.doi}}},")

    if paper.abstract:
        abstract_short = paper.abstract[:200].replace("\n", " ").strip()
        if len(paper.abstract) > 200:
            abstract_short += "..."
        lines.append(f"  abstract  = {{{_normalize_latex(abstract_short)}}},")

    lines.append(f"  url       = {{{paper.abs_url}}},")
    lines.append("}")

    return "\n".join(lines)


def _generate_acl_bibtex(key: str, title: str, authors_str: str, paper: Paper) -> str:
    """Generate ACL-style @inproceedings BibTeX."""
    lines = [
        f"@inproceedings{{{key},",
        f"  title     = {{{title}}},",
        f"  author    = {{{authors_str}}},",
        f"  year      = {{{paper.published.year}}},",
    ]

    if paper.journal_ref:
        lines.append(f"  booktitle = {{{_normalize_latex(paper.journal_ref)}}},")

    lines.append(f"  eprint    = {{{paper.arxiv_id}}},")
    lines.append(f"  archivePrefix = {{arXiv}},")
    lines.append(f"  primaryClass  = {{{paper.primary_category}}},")

    if paper.doi:
        lines.append(f"  doi       = {{{paper.doi}}},")

    lines.append(f"  url       = {{{paper.abs_url}}},")
    lines.append("}")

    return "\n".join(lines)
