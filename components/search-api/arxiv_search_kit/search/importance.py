"""Importance scoring — blends citation count, venue prestige, and influential citations."""

from __future__ import annotations

import math
import re

from arxiv_search_kit.models import Paper

# ---------------------------------------------------------------------------
# Venue prestige tiers (3 = top, 2 = strong, 1 = decent, 0 = unknown/arxiv)
# ---------------------------------------------------------------------------

VENUE_TIERS: dict[str, int] = {}

_TIER_1 = [
    # ML / AI
    "NeurIPS", "Neural Information Processing Systems",
    "ICML", "International Conference on Machine Learning",
    "ICLR", "International Conference on Learning Representations",
    # NLP
    "ACL", "Association for Computational Linguistics",
    "EMNLP", "Empirical Methods in Natural Language Processing",
    "NAACL", "North American Chapter",
    # Vision
    "CVPR", "Computer Vision and Pattern Recognition",
    "ICCV", "International Conference on Computer Vision",
    "ECCV", "European Conference on Computer Vision",
    # General AI
    "AAAI", "IJCAI",
    # Data mining / IR
    "KDD", "Knowledge Discovery and Data Mining",
    "SIGIR", "WWW", "World Wide Web",
    # Systems
    "OSDI", "SOSP",
    # Security
    "CCS", "S&P", "USENIX Security",
    # Theory
    "STOC", "FOCS",
    # HCI / DB / Robotics
    "CHI", "Human Factors in Computing",
    "SIGMOD", "VLDB", "RSS",
    # Journals
    "Nature", "Science", "JMLR", "TPAMI", "IJCV",
    "Transactions on Pattern Analysis and Machine Intelligence",
    "Journal of Machine Learning Research",
]

_TIER_2 = [
    "WACV", "COLING", "EACL", "AAMAS",
    "CoRL", "ICRA", "IROS",
    "WSDM", "CIKM", "RecSys",
    "ICASSP", "InterSpeech", "Interspeech",
    "UIST", "CSCW", "EuroSys", "NSDI",
    "ICSE", "FSE", "ASE", "SIGGRAPH", "NDSS",
    "AISTATS", "UAI", "COLT", "MICCAI", "MIDL",
    "ACM Computing Surveys",
    "Findings of the Association for Computational Linguistics",
]

_TIER_3 = [
    "BMVC", "ACCV", "SemEval", "CoNLL",
    "IALP", "ECAI", "PRICAI", "ACMMM",
]

for _v in _TIER_1: VENUE_TIERS[_v.lower()] = 3
for _v in _TIER_2: VENUE_TIERS[_v.lower()] = 2
for _v in _TIER_3: VENUE_TIERS[_v.lower()] = 1


def get_venue_tier(venue: str | None) -> int:
    """Return prestige tier (0-3) for a venue string.

    Uses word-boundary matching to handle S2's verbose venue names
    (e.g. "Annual Meeting of the Association for Computational Linguistics").
    When multiple known venues match, returns the highest tier.
    """
    if not venue:
        return 0
    v = venue.lower()
    if v in VENUE_TIERS:
        return VENUE_TIERS[v]
    best = 0
    for known, tier in VENUE_TIERS.items():
        if re.search(r'\b' + re.escape(known) + r'\b', v):
            best = max(best, tier)
        elif re.search(r'\b' + re.escape(v) + r'\b', known):
            best = max(best, tier)
    return best


# ---------------------------------------------------------------------------
# Importance score
# ---------------------------------------------------------------------------

# Weights for the importance components (sum to 1.0)
W_CITATION = 0.55
W_VENUE = 0.30
W_INFLUENTIAL = 0.15


def compute_importance_score(paper: Paper) -> float:
    """Compute a [0, 1] importance score for a single paper.

    Components:
        - citation_score: log-scaled citation count, capped at 10k.
        - venue_score: tier / 3.0.
        - influential_score: ratio of influential to total citations.
    """
    cites = paper.citation_count or 0
    citation_score = min(math.log1p(cites) / math.log1p(10_000), 1.0)
    venue_score = get_venue_tier(paper.venue) / 3.0
    influential = paper.influential_citation_count or 0
    influential_score = min(influential / cites, 1.0) if cites > 0 else 0.0

    return W_CITATION * citation_score + W_VENUE * venue_score + W_INFLUENTIAL * influential_score


def rerank_by_importance(
    papers: list[Paper],
    relevance_weight: float = 0.6,
    importance_weight: float = 0.4,
) -> list[Paper]:
    """Re-sort papers by blending relevance with importance.

    Args:
        papers: Papers with similarity_score and enrichment fields set.
        relevance_weight: Weight for the original similarity score.
        importance_weight: Weight for the computed importance score.

    Returns:
        Papers sorted by blended score. Mutates similarity_score in-place.
    """
    if not papers:
        return papers

    scored = []
    for paper in papers:
        relevance = paper.similarity_score or 0.0
        importance = compute_importance_score(paper)
        scored.append((paper, relevance_weight * relevance + importance_weight * importance))

    scored.sort(key=lambda x: x[1], reverse=True)
    for paper, score in scored:
        paper.similarity_score = score
    return [paper for paper, _ in scored]
