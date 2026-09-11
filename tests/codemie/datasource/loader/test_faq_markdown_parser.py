# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an “AS IS” BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from pathlib import Path

import pytest

from codemie.datasource.loader.faq_markdown_parser import (
    FaqArticle,
    FaqParseError,
    parse_faq_markdown,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "faq"


def read_fixture(rel_path: str) -> str:
    return (FIXTURES_DIR / rel_path).read_text(encoding="utf-8")


def parse_fixture(rel_path: str) -> FaqArticle:
    return parse_faq_markdown(rel_path=rel_path, raw=read_fixture(rel_path))


class TestParseFaqMarkdown:
    def test_basic_markdown_title_instructions_and_reference(self):
        article = parse_fixture("getting-started/installation.md")

        assert article.title == "How do I install the Codemie backend locally?"
        assert (
            article.instructions
            == "Walk the user through the install script first; only suggest manual setup if the script fails. "
            "Mention the pinned ES version."
        )
        assert article.reference == "getting-started/installation"
        assert article.references == []
        assert "Clone the repository and start the stack:" in article.content
        assert "# How do I install" not in article.content
        assert "Prompt Instruction:" not in article.content

    def test_full_frontmatter_overrides_all_fields(self):
        article = parse_fixture("getting-started/requirements.md")

        assert article.title == "What are the prerequisites?"
        assert article.instructions == "List prerequisites in order; link the access-request guide for credentials."
        assert article.reference == "1.1."
        assert article.references == ["https://codemie.lab.epam.com/#/guides/access"]
        assert article.content.startswith("You need: Docker, Python 3.12")

    def test_partial_frontmatter_reference_from_path(self):
        article = parse_fixture("troubleshooting/es-oom.md")

        assert article.title == "Elasticsearch keeps restarting (exit 137)"
        assert article.instructions == ""
        assert article.reference == "troubleshooting/es-oom"
        assert article.references == []

    def test_title_falls_back_to_humanized_filename(self):
        article = parse_fixture("troubleshooting/no-heading.md")

        assert article.title == "No Heading"
        assert article.reference == "troubleshooting/no-heading"
        assert article.content.startswith("docker compose ps shows healthy")

    def test_deep_nested_reference(self):
        article = parse_fixture("troubleshooting/advanced/deep-article.md")

        assert article.reference == "troubleshooting/advanced/deep-article"

    def test_horizontal_rule_is_not_frontmatter(self):
        article = parse_fixture("getting-started/hr-not-frontmatter.md")

        assert article.title == "How do I reset my local environment?"
        assert "---" in article.content
        assert "## Full reset" in article.content
        assert "# How do I reset" not in article.content

    def test_first_h1_wins_and_later_h1s_stay_in_content(self):
        article = parse_fixture("troubleshooting/multi-h1.md")

        assert article.title.startswith("How do I retrieve test cases?")
        assert "# Tool Integration" in article.content
        assert "How do I retrieve test cases?" not in article.content

    def test_sources_section_stays_in_content(self):
        article = parse_fixture("troubleshooting/with-sources.md")

        assert article.title == "Where do I find the system requirements?"
        assert article.references == []
        assert "## Sources" in article.content
        assert "https://docs.codemie.ai/user-guide/requirements" in article.content

    def test_run_on_multi_question_title(self):
        article = parse_fixture("many-questions.md")

        assert article.title.startswith("How to index a new repository?")
        assert article.reference == "many-questions"

    def test_invalid_frontmatter_yaml_raises(self):
        with pytest.raises(FaqParseError):
            parse_fixture("broken/bad-frontmatter.md")

    def test_crlf_line_endings(self):
        raw = read_fixture("getting-started/installation.md").replace("\n", "\r\n")

        article = parse_faq_markdown(rel_path="getting-started/installation.md", raw=raw)

        assert article.title == "How do I install the Codemie backend locally?"
        assert article.instructions.startswith("Walk the user through")
        assert article.reference == "getting-started/installation"

    def test_frontmatter_with_h1_still_strips_h1_from_content(self):
        raw = "---\ntitle: Overridden\n---\n# Heading To Strip\nBody text.\n"

        article = parse_faq_markdown(rel_path="faq/x.md", raw=raw)

        assert article.title == "Overridden"
        assert "# Heading To Strip" not in article.content
        assert "Body text." in article.content

    def test_reference_frontmatter_used_verbatim(self):
        raw = "---\nreference: 1.2.3.\n---\nBody only.\n"

        article = parse_faq_markdown(rel_path="faq/deep/file.md", raw=raw)

        assert article.reference == "1.2.3."


class TestReviewHardening:
    """Regression tests for the adversarial-review findings."""

    def test_hash_line_inside_code_fence_is_not_a_title(self):
        raw = "# Real Title\n\n```bash\n# install deps\npip install x\n```\n"
        article = parse_faq_markdown(rel_path="faq/a.md", raw=raw)

        assert article.title == "Real Title"
        # the fenced comment must survive untouched in the content
        assert "# install deps" in article.content
        assert article.content.count("```") == 2

    def test_fenced_fence_toggles_are_tracked_for_first_heading(self):
        raw = "```python\n# comment\n```\n\n# Actual\nbody"
        article = parse_faq_markdown(rel_path="faq/a.md", raw=raw)

        assert article.title == "Actual"

    def test_bom_does_not_defeat_frontmatter(self):
        raw = "\ufeff---\nreference: bom-ref\n---\n# T\nbody\n"
        article = parse_faq_markdown(rel_path="faq/a.md", raw=raw)

        assert article.reference == "bom-ref"
        assert article.title == "T"
        assert article.content == "body"

    def test_yaml_float_reference_falls_back_to_path(self):
        raw = "---\nreference: 1.10\n---\n# T\nbody\n"
        article = parse_faq_markdown(rel_path="faq/x.md", raw=raw)

        # must NOT become "1.1" — falls through to the path-derived reference
        assert article.reference == "faq/x"

    def test_non_scalar_title_falls_back_to_heading(self):
        raw = "---\ntitle: [a, b]\n---\n# Real\nbody\n"
        article = parse_faq_markdown(rel_path="faq/x.md", raw=raw)

        assert article.title == "Real"

    def test_marker_inside_code_fence_is_ignored(self):
        raw = "# T\n\n```yaml\nPrompt Instruction: fenced\n```\n\nreal body\n"
        article = parse_faq_markdown(rel_path="faq/x.md", raw=raw)

        assert article.instructions == ""
        assert "Prompt Instruction: fenced" in article.content

    def test_last_marker_wins_and_body_before_first_marker_stays_content(self):
        raw = "# T\n\nfirst body\n\nPrompt Instruction: one\n\nmiddle\n\nPrompt Instruction: two\n"
        article = parse_faq_markdown(rel_path="faq/x.md", raw=raw)

        assert "first body" in article.content
        assert "middle" in article.instructions
        assert "two" in article.instructions

    def test_indented_first_content_line_is_preserved(self):
        raw = "# T\n    indented code line\n"
        article = parse_faq_markdown(rel_path="faq/x.md", raw=raw)

        assert article.content.startswith("    indented code line")

    def test_frontmatter_closing_inside_block_scalar_is_not_closing(self):
        raw = "---\ninstructions: |\n  ---\n  keep\n---\n# T\nbody\n"
        article = parse_faq_markdown(rel_path="faq/x.md", raw=raw)

        assert article.instructions == "---\nkeep"
        assert article.title == "T"
        assert article.content == "body"

    def test_root_mode_reference_keeps_full_path(self):
        article = parse_faq_markdown(rel_path="docs/guide/install.md", raw="# T\nbody\n")

        assert article.reference == "docs/guide/install"

    def test_root_mode_readme_at_repo_root(self):
        article = parse_faq_markdown(rel_path="README.md", raw="# Project README\nbody\n")

        assert article.reference == "README"  # case preserved

    def test_humanize_preserves_inner_caps(self):
        raw = "body only, no heading"
        article = parse_faq_markdown(rel_path="faq/es-API-notes.md", raw=raw)

        assert article.title == "Es API Notes"

    def test_unterminated_frontmatter_falls_back_to_body(self):
        raw = "---\ntitle: T\nbody never closed\n"
        article = parse_faq_markdown(rel_path="faq/x.md", raw=raw)

        assert article.title == "X"
        assert "body never closed" in article.content
