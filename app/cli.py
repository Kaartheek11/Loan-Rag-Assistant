"""Command line interface.

python -m app.cli migrate
python -m app.cli seed-users [--users config/users_seed.yaml]
                             [--realm docker/keycloak/realm-loanrag.json]
python -m app.cli ingest --source ./corpus --acl-config ./config/acl_seed.yaml
python -m app.cli rebuild-index
python -m app.cli gen-test-keys [--out ./keys]
python -m app.cli mint-token --user u_alice [--minutes 15]
python -m app.cli eval [args passed to eval/run_eval.py]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import Engine

from app.config import get_settings


def _engine() -> Engine:
    from app.db.session import make_engine

    s = get_settings()
    return make_engine(s.database_url, environment=s.environment)


def cmd_migrate(args: argparse.Namespace) -> int:
    from app.db.seed import run_migrations

    run_migrations(get_settings().migrations_url)
    print("migrations applied")
    return 0


def cmd_seed_users(args: argparse.Namespace) -> int:
    from app.db.seed import SeedError, seed_users

    try:
        n = seed_users(_engine(), Path(args.users), Path(args.realm))
    except SeedError as exc:
        print(f"seed-users failed: {exc}", file=sys.stderr)
        return 1
    print(f"seeded {n} users")
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    from app.ingestion.acl_source import YamlAclBackend, configure
    from app.ingestion.pipeline import EmbeddingModelMismatch, IngestionPipeline
    from app.retrieval.embedder import Embedder
    from app.retrieval.index_manager import WriterLockHeld
    from app.security.redaction import configure_logging

    s = get_settings()
    configure_logging(s.secret_values())
    backend = YamlAclBackend(Path(args.acl_config))
    configure(backend)
    pipeline = IngestionPipeline(
        _engine(),
        Embedder(s.embedding_model),
        index_path=Path(s.faiss_index_path),
        document_store=Path(s.document_store_path),
        acl_backend=backend,
    )
    try:
        summary = pipeline.run(Path(args.source))
    except WriterLockHeld:
        print(
            "Another ingestion, deletion, or rebuild holds the index writer lock. "
            "Try again when it finishes.",
            file=sys.stderr,
        )
        return 2
    except EmbeddingModelMismatch as exc:
        print(str(exc), file=sys.stderr)
        return 3
    print(summary.render())
    return 0


def cmd_rebuild_index(args: argparse.Namespace) -> int:
    from app.ingestion.pipeline import rebuild_index
    from app.retrieval.embedder import Embedder
    from app.retrieval.index_manager import WriterLockHeld

    s = get_settings()
    try:
        count, version = rebuild_index(
            _engine(), Embedder(s.embedding_model), Path(s.faiss_index_path)
        )
    except WriterLockHeld:
        print("The index writer lock is held; try again later.", file=sys.stderr)
        return 2
    print(f"rebuilt index: {count} vectors, version {version}")
    return 0


def cmd_gen_test_keys(args: argparse.Namespace) -> int:
    from app.security.auth import generate_rsa_keypair

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    private_pem, public_pem = generate_rsa_keypair()
    (out / "test_private.pem").write_text(private_pem)
    (out / "test_private.pem").chmod(0o600)
    (out / "test_public.pem").write_text(public_pem)
    print(f"wrote {out}/test_private.pem and {out}/test_public.pem (git-ignored)")
    return 0


def cmd_mint_token(args: argparse.Namespace) -> int:
    from app.security.auth import mint_token

    s = get_settings()
    if s.environment == "production":
        print("mint-token is for development and tests only", file=sys.stderr)
        return 1
    pem = Path(args.private_key or s.jwt_private_key_path).read_text()
    print(
        mint_token(
            pem,
            sub=args.user,
            issuer=s.jwt_issuer,
            audience=s.jwt_audience,
            lifetime_seconds=int(args.minutes * 60),
        )
    )
    return 0


def cmd_eval(args: argparse.Namespace, rest: list[str]) -> int:
    from eval.run_eval import main as eval_main

    return int(eval_main(rest))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="apply Alembic migrations (owner role)")
    p = sub.add_parser("seed-users", help="load users/groups/roles; must match the realm")
    p.add_argument("--users", default="config/users_seed.yaml")
    p.add_argument("--realm", default="docker/keycloak/realm-loanrag.json")
    p = sub.add_parser("ingest", help="ingest PDFs into Postgres and FAISS")
    p.add_argument("--source", required=True)
    p.add_argument("--acl-config", required=True)
    sub.add_parser("rebuild-index", help="regenerate FAISS from the chunks table")
    p = sub.add_parser("gen-test-keys", help="create a local RSA key pair for test tokens")
    p.add_argument("--out", default="keys")
    p = sub.add_parser("mint-token", help="sign a test JWT with the local private key")
    p.add_argument("--user", required=True)
    p.add_argument("--minutes", type=float, default=15)
    p.add_argument("--private-key", default=None)
    sub.add_parser(
        "eval", help="run the evaluation harness (see eval/run_eval.py --help)", add_help=False
    )

    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "eval":
        return cmd_eval(argparse.Namespace(), argv[1:])
    args = parser.parse_args(argv)
    handlers = {
        "migrate": cmd_migrate,
        "seed-users": cmd_seed_users,
        "ingest": cmd_ingest,
        "rebuild-index": cmd_rebuild_index,
        "gen-test-keys": cmd_gen_test_keys,
        "mint-token": cmd_mint_token,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
