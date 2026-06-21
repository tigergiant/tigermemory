from __future__ import annotations

from pathlib import Path

import tigermemory_cli
import tigermemory_core
import tigermemory_publish


PUBLIC_TAXONOMY = (
    "projects",
    "areas",
    "resources",
    "decisions",
    "journal",
    "systems",
    "archive",
)

OLD_DOGFOOD_PARTITIONS = {
    "brand",
    "investment",
    "operations",
    "production",
    "self-evolution",
}


def test_public_admin_partitions_are_beginner_taxonomy() -> None:
    assert tuple(tigermemory_core.WIKI_ADMIN_PUBLIC_PARTITIONS) == PUBLIC_TAXONOMY


def test_cli_admin_choices_match_public_partitions() -> None:
    assert tigermemory_cli.ADMIN_PARTITIONS == PUBLIC_TAXONOMY


def test_public_modules_seed_only_public_taxonomy() -> None:
    assert tuple(tigermemory_publish.PUBLIC_STARTER_WIKI_PARTITIONS) == PUBLIC_TAXONOMY
    assert tuple(tigermemory_publish.WIKI_PUBLISH_PARTITIONS) == PUBLIC_TAXONOMY


def test_old_dogfood_partitions_not_in_public_seed() -> None:
    public_partitions = set(tigermemory_publish.PUBLIC_STARTER_WIKI_PARTITIONS)
    assert OLD_DOGFOOD_PARTITIONS.isdisjoint(public_partitions)
    assert "systems" in public_partitions


def test_public_seed_templates_cover_each_partition_once() -> None:
    destinations = [dst for _src, dst in tigermemory_publish.PUBLIC_STARTER_WIKI_TEMPLATE_FILES]
    seeded_partitions = tuple(dst.split("/", 2)[1] for dst in destinations)

    assert seeded_partitions == PUBLIC_TAXONOMY


def test_provider_docs_do_not_overclaim_anthropic_native_support() -> None:
    template_root = Path("packages/tigermemory-publish/src/tigermemory_publish/templates")
    texts = "\n".join(
        [
            (template_root / "README.md").read_text(encoding="utf-8"),
            (template_root / "docs" / "provider-compatibility.md").read_text(encoding="utf-8"),
        ]
    ).lower()

    forbidden_claims = [
        "anthropic-compatible",
        "anthropic compatible",
        "anthropic-native supported",
        "anthropic native supported",
    ]
    assert all(claim not in texts for claim in forbidden_claims)
    assert "anthropic-native apis are not claimed" in texts
