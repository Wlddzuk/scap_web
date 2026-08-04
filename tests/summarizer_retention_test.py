"""Focused regressions for Part A retention-script metadata."""

import ast
from pathlib import Path

from models import Article


ROOT = Path(__file__).resolve().parents[1]
RETENTION_FIELDS = {
    "cover_line",
    "cta_question",
    "search_caption",
    "series_lane",
}
COLOR_INTENSITY_FIELD = "color_intensity"


def _assigned_article_fields(path: Path, function_name: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == function_name
    )
    fields = set()
    for node in ast.walk(function):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "article"
            ):
                fields.add(target.attr)
    return fields


def test_article_model_exposes_all_retention_fields():
    columns = set(Article.__table__.columns.keys())
    assert RETENTION_FIELDS <= columns


def test_article_model_exposes_color_intensity_and_legacy_rows_default_to_vivid():
    column = Article.__table__.columns[COLOR_INTENSITY_FIELD]
    assert column.type.length == 16

    article = Article(
        url="https://example.test/color-intensity-model",
        title="Color intensity model",
        content="Body",
    )
    assert article.to_dict()[COLOR_INTENSITY_FIELD] == "vivid"
    article.color_intensity = "electric"
    assert article.to_dict()[COLOR_INTENSITY_FIELD] == "electric"


def test_additive_migration_lists_all_retention_fields():
    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    migration = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_migrate_schema"
    )
    migrated_fields = set()
    for node in ast.walk(migration):
        if not isinstance(node, (ast.List, ast.Tuple)):
            continue
        for element in node.elts:
            if (
                isinstance(element, ast.Tuple)
                and element.elts
                and isinstance(element.elts[0], ast.Constant)
                and isinstance(element.elts[0].value, str)
            ):
                migrated_fields.add(element.elts[0].value)

    assert RETENTION_FIELDS <= migrated_fields


def test_additive_migration_lists_color_intensity_field():
    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    migration = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_migrate_schema"
    )
    migration_source = ast.unparse(migration)

    assert "color_intensity" in migration_source
    assert "VARCHAR(16)" in migration_source


def test_all_three_summarize_call_sites_persist_retention_fields():
    call_sites = (
        ("app.py", "run_summarize_in_background"),
        ("discord_bot.py", "process_article_url"),
        ("story_finder.py", "_process_candidate"),
    )

    for filename, function_name in call_sites:
        assigned = _assigned_article_fields(ROOT / filename, function_name)
        assert RETENTION_FIELDS <= assigned, (
            f"{filename}:{function_name} is missing "
            f"{sorted(RETENTION_FIELDS - assigned)}"
        )


def test_resummarizing_clears_attribution_to_the_replaced_hook_list():
    call_sites = (
        ("app.py", "run_summarize_in_background"),
        ("discord_bot.py", "process_article_url"),
        ("story_finder.py", "_process_candidate"),
    )

    for filename, function_name in call_sites:
        tree = ast.parse((ROOT / filename).read_text(encoding="utf-8"))
        function = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == function_name
        )
        parents = {}
        for parent in ast.walk(function):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent

        resets = []
        for node in ast.walk(function):
            if not isinstance(node, ast.Assign):
                continue
            if not (
                isinstance(node.value, ast.Constant)
                and node.value.value is None
            ):
                continue
            if any(
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "article"
                and target.attr == "hook_index_used"
                for target in node.targets
            ):
                resets.append(node)

        assert resets, f"{filename}:{function_name} does not clear attribution"
        for reset in resets:
            ancestor = parents.get(reset)
            while ancestor is not None and ancestor is not function:
                if isinstance(ancestor, ast.If):
                    assert "video_path" not in ast.unparse(ancestor.test)
                ancestor = parents.get(ancestor)
