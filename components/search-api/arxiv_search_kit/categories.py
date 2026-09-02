"""ArXiv category taxonomy and conference-to-category mapping."""

from __future__ import annotations

# All CS categories
CS_CATEGORIES: list[str] = [
    "cs.AI",  # Artificial Intelligence
    "cs.AR",  # Hardware Architecture
    "cs.CC",  # Computational Complexity
    "cs.CE",  # Computational Engineering, Finance, and Science
    "cs.CG",  # Computational Geometry
    "cs.CL",  # Computation and Language (NLP)
    "cs.CR",  # Cryptography and Security
    "cs.CV",  # Computer Vision and Pattern Recognition
    "cs.CY",  # Computers and Society
    "cs.DB",  # Databases
    "cs.DC",  # Distributed, Parallel, and Cluster Computing
    "cs.DL",  # Digital Libraries
    "cs.DM",  # Discrete Mathematics
    "cs.DS",  # Data Structures and Algorithms
    "cs.ET",  # Emerging Technologies
    "cs.FL",  # Formal Languages and Automata Theory
    "cs.GL",  # General Literature
    "cs.GR",  # Graphics
    "cs.GT",  # Computer Science and Game Theory
    "cs.HC",  # Human-Computer Interaction
    "cs.IR",  # Information Retrieval
    "cs.IT",  # Information Theory
    "cs.LG",  # Machine Learning
    "cs.LO",  # Logic in Computer Science
    "cs.MA",  # Multiagent Systems
    "cs.MM",  # Multimedia
    "cs.MS",  # Mathematical Software
    "cs.NA",  # Numerical Analysis
    "cs.NE",  # Neural and Evolutionary Computing
    "cs.NI",  # Networking and Internet Architecture
    "cs.OH",  # Other Computer Science
    "cs.OS",  # Operating Systems
    "cs.PF",  # Performance
    "cs.PL",  # Programming Languages
    "cs.RO",  # Robotics
    "cs.SC",  # Symbolic Computation
    "cs.SD",  # Sound
    "cs.SE",  # Software Engineering
    "cs.SI",  # Social and Information Networks
    "cs.SY",  # Systems and Control
]

# Statistics categories relevant to ML/AI research
STAT_CATEGORIES: list[str] = [
    "stat.ML",  # Machine Learning (Statistics)
]

# Electrical Engineering categories that overlap with CS/ML
EESS_CATEGORIES: list[str] = [
    "eess.AS",  # Audio and Speech Processing
    "eess.IV",  # Image and Video Processing
    "eess.SP",  # Signal Processing
]

# All target categories for indexing
ALL_CATEGORIES: list[str] = CS_CATEGORIES + STAT_CATEGORIES + EESS_CATEGORIES

# Category descriptions (short)
CATEGORY_DESCRIPTIONS: dict[str, str] = {
    "cs.AI": "Artificial Intelligence",
    "cs.AR": "Hardware Architecture",
    "cs.CC": "Computational Complexity",
    "cs.CE": "Computational Engineering",
    "cs.CG": "Computational Geometry",
    "cs.CL": "Computation and Language (NLP)",
    "cs.CR": "Cryptography and Security",
    "cs.CV": "Computer Vision",
    "cs.CY": "Computers and Society",
    "cs.DB": "Databases",
    "cs.DC": "Distributed Computing",
    "cs.DL": "Digital Libraries",
    "cs.DM": "Discrete Mathematics",
    "cs.DS": "Data Structures and Algorithms",
    "cs.ET": "Emerging Technologies",
    "cs.FL": "Formal Languages",
    "cs.GL": "General Literature",
    "cs.GR": "Graphics",
    "cs.GT": "Game Theory",
    "cs.HC": "Human-Computer Interaction",
    "cs.IR": "Information Retrieval",
    "cs.IT": "Information Theory",
    "cs.LG": "Machine Learning",
    "cs.LO": "Logic in Computer Science",
    "cs.MA": "Multiagent Systems",
    "cs.MM": "Multimedia",
    "cs.MS": "Mathematical Software",
    "cs.NA": "Numerical Analysis",
    "cs.NE": "Neural and Evolutionary Computing",
    "cs.NI": "Networking",
    "cs.OH": "Other CS",
    "cs.OS": "Operating Systems",
    "cs.PF": "Performance",
    "cs.PL": "Programming Languages",
    "cs.RO": "Robotics",
    "cs.SC": "Symbolic Computation",
    "cs.SD": "Sound",
    "cs.SE": "Software Engineering",
    "cs.SI": "Social and Information Networks",
    "cs.SY": "Systems and Control",
    "stat.ML": "Machine Learning (Statistics)",
    "eess.AS": "Audio and Speech Processing",
    "eess.IV": "Image and Video Processing",
    "eess.SP": "Signal Processing",
}

# Conference to ArXiv category mapping
# Maps major CS/ML conferences to the ArXiv categories where their papers typically appear
CONFERENCE_CATEGORIES: dict[str, list[str]] = {
    # Vision conferences
    "CVPR": ["cs.CV", "cs.AI", "cs.LG"],
    "ECCV": ["cs.CV", "cs.AI", "cs.LG"],
    "ICCV": ["cs.CV", "cs.AI", "cs.LG"],
    "WACV": ["cs.CV"],
    # ML/AI conferences
    "ICLR": ["cs.LG", "cs.AI", "cs.CL", "cs.CV", "stat.ML", "cs.NE"],
    "NeurIPS": ["cs.LG", "cs.AI", "cs.CL", "cs.CV", "stat.ML", "cs.NE"],
    "ICML": ["cs.LG", "cs.AI", "stat.ML"],
    "AAAI": ["cs.AI", "cs.LG", "cs.CV", "cs.CL", "cs.MA"],
    "IJCAI": ["cs.AI", "cs.LG", "cs.MA"],
    # NLP conferences
    "ACL": ["cs.CL", "cs.AI"],
    "EMNLP": ["cs.CL", "cs.AI"],
    "NAACL": ["cs.CL", "cs.AI"],
    "COLING": ["cs.CL"],
    "EACL": ["cs.CL"],
    # HCI conferences
    "CHI": ["cs.HC", "cs.AI"],
    "UIST": ["cs.HC", "cs.GR"],
    "CSCW": ["cs.HC", "cs.SI"],
    # Data mining / IR
    "KDD": ["cs.LG", "cs.DB", "cs.IR", "cs.SI"],
    "WWW": ["cs.IR", "cs.SI", "cs.DB", "cs.CL"],
    "SIGIR": ["cs.IR", "cs.CL"],
    "WSDM": ["cs.IR", "cs.SI"],
    "CIKM": ["cs.IR", "cs.DB"],
    "RecSys": ["cs.IR", "cs.LG"],
    # Robotics
    "RSS": ["cs.RO", "cs.AI"],
    "CoRL": ["cs.RO", "cs.LG", "cs.AI"],
    "ICRA": ["cs.RO", "cs.AI", "cs.CV"],
    "IROS": ["cs.RO"],
    # Multiagent
    "AAMAS": ["cs.MA", "cs.AI", "cs.GT"],
    # Speech/Audio
    "ISCA": ["cs.SD", "eess.AS"],
    "InterSpeech": ["cs.SD", "cs.CL", "eess.AS"],
    "ICASSP": ["eess.AS", "cs.SD", "eess.SP"],
    # Security
    "CCS": ["cs.CR"],
    "USENIX Security": ["cs.CR"],
    "S&P": ["cs.CR"],
    "NDSS": ["cs.CR"],
    # Systems
    "OSDI": ["cs.OS", "cs.DC"],
    "SOSP": ["cs.OS", "cs.DC"],
    "EuroSys": ["cs.OS", "cs.DC"],
    "NSDI": ["cs.NI", "cs.DC"],
    # Theory
    "STOC": ["cs.CC", "cs.DS"],
    "FOCS": ["cs.CC", "cs.DS"],
    # Software Engineering
    "ICSE": ["cs.SE"],
    "FSE": ["cs.SE"],
    "ASE": ["cs.SE"],
    # Graphics
    "SIGGRAPH": ["cs.GR", "cs.CV"],
    # Databases
    "SIGMOD": ["cs.DB"],
    "VLDB": ["cs.DB"],
}


def get_categories_for_conference(conference: str) -> list[str]:
    """Get ArXiv categories for a conference name (case-insensitive)."""
    conference_upper = conference.upper().replace(" ", "")
    for name, cats in CONFERENCE_CATEGORIES.items():
        if name.upper().replace(" ", "") == conference_upper:
            return cats
    return []


def is_valid_category(category: str) -> bool:
    """Check if a category string is a valid ArXiv category."""
    return category in ALL_CATEGORIES


def get_category_description(category: str) -> str:
    """Get human-readable description for a category."""
    return CATEGORY_DESCRIPTIONS.get(category, category)