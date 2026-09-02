"""CLI script to build the LanceDB index from ArXiv metadata.

Two-step workflow:
  Step 1 (cheap machine): Download metadata from ArXiv OAI-PMH → save to JSONL
  Step 2 (GPU machine):   Read JSONL → embed with SPECTER2 → build LanceDB index

Usage:
    # Step 1: Download metadata (run on any machine, network-bound)
    python -m arxiv_search_kit.scripts.build_index download \
        --output ./arxiv_metadata.jsonl

    # Step 2: Build index (run on GPU machine)
    python -m arxiv_search_kit.scripts.build_index build \
        --metadata-path ./arxiv_metadata.jsonl \
        --output-dir ./arxiv_index \
        --device cuda --batch-size 1024

    # One-shot: Download + build in one step (if you have GPU + network)
    python -m arxiv_search_kit.scripts.build_index all \
        --output-dir ./arxiv_index \
        --device cuda --batch-size 1024
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

from arxiv_search_kit.categories import ALL_CATEGORIES

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def cmd_download(args: argparse.Namespace) -> None:
    """Download metadata from ArXiv OAI-PMH, HuggingFace, or Kaggle."""
    categories = args.categories if args.categories else None

    # HuggingFace source — just download the pre-harvested JSONL
    if args.source == "huggingface":
        from arxiv_search_kit.hub import download_metadata
        logger.info("Downloading pre-harvested metadata from HuggingFace...")
        t_start = time.time()
        path = download_metadata(output_path=args.output)
        elapsed = time.time() - t_start
        logger.info(f"Downloaded to {path} in {elapsed:.0f}s")
        return

    from arxiv_search_kit.index.download import save_metadata_to_jsonl

    logger.info("=" * 60)
    logger.info("Step 1: Download ArXiv Metadata")
    logger.info("=" * 60)
    logger.info(f"Source: {args.source}")
    logger.info(f"Output: {args.output}")
    if args.date_from:
        logger.info(f"From:   {args.date_from}")
    if args.date_to:
        logger.info(f"To:     {args.date_to}")
    logger.info(f"Categories: {len(categories) if categories else len(ALL_CATEGORIES)}")
    logger.info("=" * 60)

    t_start = time.time()

    try:
        count = save_metadata_to_jsonl(
            output_path=args.output,
            source=args.source,
            metadata_path=args.kaggle_path,
            categories=categories,
            date_from=args.date_from,
            date_to=args.date_to,
        )
    except Exception as e:
        logger.error(f"Download failed: {e}")
        sys.exit(1)

    elapsed = time.time() - t_start
    logger.info(f"Downloaded {count:,} papers in {elapsed/60:.1f} minutes")
    logger.info(f"Saved to: {args.output}")


def cmd_build(args: argparse.Namespace) -> None:
    """Build LanceDB index from a JSONL metadata file."""
    from arxiv_search_kit.index.builder import build_index

    categories = args.categories if args.categories else None

    logger.info("=" * 60)
    logger.info("Step 2: Build LanceDB Index")
    logger.info("=" * 60)
    logger.info(f"Metadata: {args.metadata_path}")
    logger.info(f"Output:   {args.output_dir}")
    logger.info(f"Device:   {args.device}")
    logger.info(f"Batch:    {args.batch_size}")
    logger.info(f"Categories: {len(categories) if categories else 'all (from JSONL)'}")
    logger.info("=" * 60)

    t_start = time.time()

    try:
        build_index(
            metadata_path=args.metadata_path,
            output_dir=args.output_dir,
            device=args.device,
            batch_size=args.batch_size,
            categories=categories,
            num_partitions=args.num_partitions,
            num_sub_vectors=args.num_sub_vectors,
        )
    except Exception as e:
        logger.error(f"Index build failed: {e}")
        sys.exit(1)

    elapsed = time.time() - t_start
    logger.info(f"Total build time: {elapsed/60:.1f} minutes")
    logger.info("Done!")


def cmd_all(args: argparse.Namespace) -> None:
    """Download metadata + build index in one step."""
    from arxiv_search_kit.index.builder import build_index
    from pathlib import Path

    categories = args.categories if args.categories else None
    jsonl_path = str(Path(args.output_dir) / "arxiv_metadata.jsonl")

    logger.info("=" * 60)
    logger.info("Full Pipeline: Download + Build Index")
    logger.info("=" * 60)

    t_start = time.time()

    # Step 1: Download metadata
    if args.source == "huggingface":
        from arxiv_search_kit.hub import download_metadata
        logger.info("--- Step 1: Downloading metadata from HuggingFace ---")
        path = download_metadata(output_path=jsonl_path)
        jsonl_path = str(path)
    else:
        from arxiv_search_kit.index.download import save_metadata_to_jsonl
        logger.info(f"--- Step 1: Downloading metadata ({args.source}) ---")
        try:
            count = save_metadata_to_jsonl(
                output_path=jsonl_path,
                source=args.source,
                metadata_path=args.kaggle_path,
                categories=categories,
                date_from=args.date_from,
                date_to=args.date_to,
            )
        except Exception as e:
            logger.error(f"Download failed: {e}")
            sys.exit(1)
        logger.info(f"Downloaded {count:,} papers")

    # Step 2: Build index
    logger.info("--- Step 2: Building LanceDB index ---")
    try:
        build_index(
            metadata_path=jsonl_path,
            output_dir=args.output_dir,
            device=args.device,
            batch_size=args.batch_size,
            categories=categories,
            num_partitions=args.num_partitions,
            num_sub_vectors=args.num_sub_vectors,
        )
    except Exception as e:
        logger.error(f"Index build failed: {e}")
        sys.exit(1)

    elapsed = time.time() - t_start
    logger.info(f"Total pipeline time: {elapsed/60:.1f} minutes")
    logger.info("Done!")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build ArXiv search index",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # --- download subcommand ---
    dl_parser = subparsers.add_parser(
        "download",
        help="Download ArXiv metadata to JSONL (run on cheap machine)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Download ALL CS + stat.ML papers from ArXiv (fresh, up-to-date)
  python -m arxiv_search_kit.scripts.build_index download \\
      --output ./arxiv_metadata.jsonl

  # Download only papers from 2023 onwards
  python -m arxiv_search_kit.scripts.build_index download \\
      --output ./arxiv_metadata.jsonl --date-from 2023-01-01

  # Download specific categories only
  python -m arxiv_search_kit.scripts.build_index download \\
      --output ./arxiv_metadata.jsonl --categories cs.LG cs.CV cs.CL stat.ML

  # Use existing Kaggle snapshot instead of OAI-PMH
  python -m arxiv_search_kit.scripts.build_index download \\
      --output ./arxiv_metadata.jsonl --source kaggle \\
      --kaggle-path ./arxiv-metadata-oai-snapshot.json
        """,
    )
    dl_parser.add_argument("--output", type=str, required=True, help="Output JSONL path")
    dl_parser.add_argument("--source", type=str, default="huggingface",
                           choices=["huggingface", "oai-pmh", "kaggle"],
                           help="Metadata source (default: huggingface for instant download)")
    dl_parser.add_argument("--kaggle-path", type=str, default=None,
                           help="Path to Kaggle JSON (only if --source kaggle)")
    dl_parser.add_argument("--date-from", type=str, default=None, help="Start date (YYYY-MM-DD)")
    dl_parser.add_argument("--date-to", type=str, default=None, help="End date (YYYY-MM-DD)")
    dl_parser.add_argument("--categories", type=str, nargs="*", default=None,
                           help="ArXiv categories to fetch")
    dl_parser.set_defaults(func=cmd_download)

    # --- build subcommand ---
    build_parser = subparsers.add_parser(
        "build",
        help="Build LanceDB index from JSONL metadata (run on GPU machine)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Build on H200 GPU
  python -m arxiv_search_kit.scripts.build_index build \\
      --metadata-path ./arxiv_metadata.jsonl \\
      --output-dir ./arxiv_index \\
      --device cuda --batch-size 1024

  # Build on CPU (slower)
  python -m arxiv_search_kit.scripts.build_index build \\
      --metadata-path ./arxiv_metadata.jsonl \\
      --output-dir ./arxiv_index \\
      --device cpu --batch-size 64
        """,
    )
    build_parser.add_argument("--metadata-path", type=str, required=True,
                              help="Path to JSONL metadata file")
    build_parser.add_argument("--output-dir", type=str, required=True,
                              help="Directory for LanceDB index")
    build_parser.add_argument("--device", type=str, default="cuda", choices=["cuda", "cpu"],
                              help="Torch device (default: cuda)")
    build_parser.add_argument("--batch-size", type=int, default=256,
                              help="Embedding batch size (default: 256)")
    build_parser.add_argument("--categories", type=str, nargs="*", default=None,
                              help="Filter categories (default: all in JSONL)")
    build_parser.add_argument("--num-partitions", type=int, default=256,
                              help="IVF-PQ partitions (default: 256)")
    build_parser.add_argument("--num-sub-vectors", type=int, default=96,
                              help="PQ sub-vectors (default: 96)")
    build_parser.set_defaults(func=cmd_build)

    # --- all subcommand ---
    all_parser = subparsers.add_parser(
        "all",
        help="Download + build in one step (needs GPU + network)",
    )
    all_parser.add_argument("--output-dir", type=str, required=True,
                            help="Directory for LanceDB index")
    all_parser.add_argument("--source", type=str, default="huggingface",
                            choices=["huggingface", "oai-pmh", "kaggle"])
    all_parser.add_argument("--kaggle-path", type=str, default=None)
    all_parser.add_argument("--date-from", type=str, default=None)
    all_parser.add_argument("--date-to", type=str, default=None)
    all_parser.add_argument("--device", type=str, default="cuda", choices=["cuda", "cpu"])
    all_parser.add_argument("--batch-size", type=int, default=256)
    all_parser.add_argument("--categories", type=str, nargs="*", default=None)
    all_parser.add_argument("--num-partitions", type=int, default=256)
    all_parser.add_argument("--num-sub-vectors", type=int, default=96)
    all_parser.set_defaults(func=cmd_all)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()