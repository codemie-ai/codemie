# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""SQL builders of the LiteLLM route identity of a transcript request: whether it ran on a route of its own model,
and the name cost_daily keys its tokens by."""

from __future__ import annotations

from codemie.repository.cli_analytics.postgres.cost_key_sql import normalized_model_name


def routed_predicate(model: str, model_raw: str) -> str:
    """A transcript request ran on a LiteLLM route of its own model: its `model` (the plugin's copy of
    `x-litellm-model-name`) differs from its `model_raw`, contains '/' and normalises to the same name as `model_raw`
    up to a trailing 8-digit date on either side (`vertex_ai/claude-sonnet-4-5` for `claude-sonnet-4-5-20250929`); a
    deployment of another model, or one the proxy names differently, is not its route. The CASE runs the two
    normalisers only where the cheap tests hold (an AND leaves the order to the planner); false where they fail."""
    return (
        f"CASE WHEN {model} <> {model_raw} AND strpos({model}, '/') > 0"
        f" THEN {_undated(normalized_model_name(model))} = {_undated(normalized_model_name(model_raw))}"
        " ELSE false END"
    )


def _undated(name: str) -> str:
    return f"regexp_replace({name}, '-\\d{{8}}$', '')"


def group_model(model: str) -> str:
    """The `model` of a transcript request group over rows of `model`: its one route (a name with '/') over a plain
    name, so whether a group is keyed by its route never rests on the provider prefix sorting after the plain name;
    a group naming more than one route has no route and takes the largest plain name, NULL when it has none; the
    largest plain name when no row names a route; NULL when no row has a model."""
    routes = f"FILTER (WHERE strpos({model}, '/') > 0)"
    return (
        f"CASE WHEN min({model}) {routes} <> max({model}) {routes}"
        f" THEN max({model}) FILTER (WHERE strpos({model}, '/') = 0)"
        f" ELSE coalesce(max({model}) {routes}, max({model})) END"
    )


def routed_model_name(otherwise: str) -> str:
    """The route of the transcript request `a` when it has one, else `otherwise`."""
    return f"CASE WHEN {routed_predicate('a.model', 'a.model_raw')} THEN a.model ELSE {otherwise} END"
