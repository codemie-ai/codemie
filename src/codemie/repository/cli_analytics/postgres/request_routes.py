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

"""SQL of the LiteLLM route each OTel api_request of a session takes from the transcript request the cost rollup
pairs it with; binds ``$sid``."""

from __future__ import annotations

from codemie.repository.cli_analytics.postgres.rollups import KEY_WINDOWS, request_pairing_ctes
from codemie.repository.cli_analytics.postgres.route_identity import routed_predicate
from codemie.repository.cli_analytics.vocabulary import EventKind

_API_REQUEST = int(EventKind.API_REQUEST)


# The route of each OTel api_request of session $sid that the cost rollup pairs with a transcript request run on a
# route of its own model (`routed_predicate`: the route normalises to the request's model_raw up to a trailing date), as
# `okey` (the record's row identity) and `route`. The pairing is the rollup's own, per UTC day of the session with its
# time windows, so a request takes the name cost_daily keys its tokens by; a record without a row here is priced by its
# own name. Nothing is paired unless a transcript row of the session carries a route.
REQUEST_ROUTES = f"""WITH RECURSIVE rk AS (
                SELECT k.day, k.session_id, {KEY_WINDOWS}
                FROM (SELECT DISTINCT (x.ts AT TIME ZONE 'UTC')::date AS day, $sid::text AS session_id
                      FROM (SELECT u.ts FROM usage_requests u WHERE u.session_id = $sid::text
                            UNION ALL
                            SELECT l.ts FROM log_events l
                            WHERE l.session_id = $sid::text AND l.event_kind = {_API_REQUEST}) x
                      WHERE EXISTS (SELECT 1 FROM usage_requests r
                                    WHERE r.session_id = $sid::text
                                      AND {routed_predicate("r.model", "r.model_raw")})) k
            ),
{request_pairing_ctes(hashed_cut=True)},
            -- tid and oid number each day's requests from 1, so a day's arrays are indexed by them: a join of `p`
            -- with `t` and `o` is planned as a nested loop over the CTE scans, quadratic in the session's requests
            ta AS (
                SELECT day, array_agg(CASE WHEN t_day = day AND {routed_predicate("model", "model_raw")} THEN model END
                                      ORDER BY tid) AS routes
                FROM t GROUP BY day
            ), oa AS (
                SELECT day, array_agg(okey ORDER BY oid) AS okeys FROM o GROUP BY day
            )
            SELECT DISTINCT ON (okey) okey, route
            FROM (SELECT p.day, oa.okeys[p.oid] AS okey, ta.routes[p.tid] AS route
                  FROM p JOIN ta ON ta.day = p.day JOIN oa ON oa.day = p.day) x
            WHERE route IS NOT NULL
            ORDER BY okey, day"""
# The CTE `request_routes`: REQUEST_ROUTES as one jsonb object okey -> route, read by `request_route` in O(log n)
# per record, where a join of the records with the routes is planned as a nested loop.
REQUEST_ROUTES_CTE = f"""request_routes AS MATERIALIZED (
            SELECT coalesce(jsonb_object_agg(r.okey, r.route), '{{}}'::jsonb) AS routes FROM ({REQUEST_ROUTES}) r
        )"""


def request_route(okey: str) -> str:
    """The route of the OTel record with row identity `okey` (see REQUEST_ROUTES), NULL when it has none."""
    return f"((SELECT rr.routes FROM request_routes rr) ->> ({okey}))"
