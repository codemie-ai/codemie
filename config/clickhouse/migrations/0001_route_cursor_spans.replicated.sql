-- Copyright 2026 EPAM Systems, Inc. ("EPAM")
--
-- Licensed under the Apache License, Version 2.0 (the "License");
-- you may not use this file except in compliance with the License.
-- You may obtain a copy of the License at
--
--     http://www.apache.org/licenses/LICENSE-2.0
--
-- Unless required by applicable law or agreed to in writing, software
-- distributed under the License is distributed on an "AS IS" BASIS,
-- WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
-- See the License for the specific language governing permissions and
-- limitations under the License.

-- Migration: route Cursor metrics/spans into the existing coding-agent rollups,
-- alongside Claude Code, matching the updated view bodies in ../schema.replicated.sql.
--
-- Same rationale as 0001_route_cursor_spans.sql: the replicated schema file is
-- also only applied once, on initial cluster bootstrap, and the
-- `CREATE MATERIALIZED VIEW IF NOT EXISTS` statements are a no-op against
-- views that already exist. Use ALTER TABLE ... ON CLUSTER ... MODIFY QUERY
-- to update the view in place across the shared/replicated cluster.
--
-- Safe to run more than once. Apply against the shared/replicated cluster
-- (staging, production). For a plain single-node instance, use
-- 0001_route_cursor_spans.sql instead.
--
-- No backfill: MODIFY QUERY only affects future inserts, not rows already in
-- coding_agent_metrics_sum / coding_agent_traces. This is deployed before
-- Cursor ingestion begins, so there is no pre-existing cursor.* data to miss.
--
-- Usage:
--   clickhouse-client --host <host> --user <user> --password <password> \
--     --multiquery < 0001_route_cursor_spans.replicated.sql

ALTER TABLE codemie_analytics.mv_lines_daily ON CLUSTER default MODIFY QUERY
SELECT
    toDate(TimeUnix)                                       AS day,
    Attributes['session.id']                               AS session_id,
    Attributes['user.email']                                AS user_email,
    Attributes['model']                                     AS model_name,
    if(Attributes['type'] = 'added',   toUInt64(Value), 0)  AS lines_added,
    if(Attributes['type'] = 'removed', toUInt64(Value), 0)  AS lines_removed
FROM codemie_analytics.coding_agent_metrics_sum
WHERE MetricName IN ('claude_code.lines_of_code.count', 'cursor.lines_of_code.count');


ALTER TABLE codemie_analytics.mv_turns_daily ON CLUSTER default MODIFY QUERY
SELECT
    toDate(Timestamp) AS day,
    session_id,
    count()           AS turns
FROM codemie_analytics.coding_agent_traces
WHERE SpanName IN ('claude_code.interaction', 'cursor.interaction')
  AND session_id != ''
GROUP BY day, session_id;


ALTER TABLE codemie_analytics.mv_file_facts_daily ON CLUSTER default MODIFY QUERY
SELECT
    toDate(Timestamp)                                                              AS day,
    session_id,
    countIf(tool_name != '')                                                       AS tool_calls,
    countIf(subagent_type != '')                                                   AS agent_count,
    countIf(span_skill_name != '')                                                 AS skill_count,
    uniqExactStateIf(file_path, file_path != '')                                   AS files_changed,
    uniqExactStateIf(file_path, file_path != '' AND tool_name = 'Write')           AS files_written,
    uniqExactStateIf(
        file_path,
        file_path != '' AND tool_name IN ('Edit', 'MultiEdit', 'NotebookEdit')
    )                                                                              AS files_edited
FROM codemie_analytics.coding_agent_traces
WHERE SpanName IN ('claude_code.tool', 'cursor.tool')
  AND session_id != ''
GROUP BY day, session_id;


ALTER TABLE codemie_analytics.mv_active_time_daily ON CLUSTER default MODIFY QUERY
SELECT
    toDate(TimeUnix)                                     AS day,
    Attributes['session.id']                             AS session_id,
    Attributes['user.email']                              AS user_email,
    if(Attributes['type'] = 'user', toUInt64(Value * 1000), 0)   AS active_ms_user,
    if(Attributes['type'] = 'cli',  toUInt64(Value * 1000), 0)   AS active_ms_cli
FROM codemie_analytics.coding_agent_metrics_sum
WHERE MetricName IN ('claude_code.active_time.total', 'cursor.active_time.total');
