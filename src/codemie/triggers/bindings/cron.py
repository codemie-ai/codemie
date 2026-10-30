# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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

"""Module for triggers core service"""

import asyncio
import platform
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Dict, Optional
from croniter import croniter
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MAX_INSTANCES, EVENT_JOB_MISSED
from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.executors.pool import ThreadPoolExecutor as APSThreadPoolExecutor
from apscheduler.jobstores.base import JobLookupError
from apscheduler.triggers.cron import CronTrigger
from apscheduler.schedulers.asyncio import AsyncIOScheduler


from codemie.configs import logger, config
from codemie.core.constants import CodeIndexType
from codemie.triggers.bindings.cron_helpers import normalize_day_of_week
from codemie.triggers.bindings.cron_stagger import RESOURCE_TYPE_DATASOURCE, stagger_cron
from codemie.core.models import GitRepo, SVNRepo
from codemie.rest_api.models.index import IndexInfo
from codemie_tools.base.models import CredentialTypes
from codemie.rest_api.models.settings import Settings
from codemie.service.constants import FullDatasourceTypes
from codemie.service.settings.base_settings import SearchFields
from codemie.triggers.actors.assistant import invoke_assistant
from codemie.datasource.datasources_config import STORAGE_CONFIG
from codemie.triggers.actors.datasource import (
    reindex_azure_devops_wiki,
    reindex_azure_devops_work_item,
    reindex_code,
    reindex_confluence,
    reindex_git_faq,
    reindex_google,
    reindex_jira,
    reindex_sharepoint,
    reindex_svn,
    reindex_xray,
    reindex_xwiki,
    resume_stale_datasource,
)
from codemie.triggers.actors.workflow import invoke_workflow
from codemie.triggers.bindings.cache_manager import CacheManager
from codemie.triggers.bindings.utils import resolve_trigger_user, validate_assistant, validate_datasource
from codemie.triggers.trigger_exceptions import DatasourceNotValidated, NotImplementedDatasource
from codemie.triggers.trigger_models import (
    AzureDevOpsWikiReindexTask,
    AzureDevOpsWorkItemReindexTask,
    XWikiReindexTask,
    CodeReindexTask,
    ConfluenceReindexTask,
    GitFaqReindexTask,
    GoogleReindexTask,
    JiraReindexTask,
    SharePointReindexTask,
    SVNReindexTask,
    XrayReindexTask,
)

# Constants
DEFAULT_TASK_PROMPT = "Do it"


async def _tracked_run(actor_fn, scheduler_id: str, **kwargs):
    """Wrap a scheduler actor with SchedulerRun persistence."""
    from codemie.repository.scheduler_run_repository import SchedulerRunRepository
    from codemie.rest_api.models.scheduler_run import SchedulerRun

    # CR-009: use timezone-aware UTC datetimes; datetime.utcnow() is deprecated in Python 3.12+
    started_at = datetime.now(timezone.utc)
    input_data: dict = {}
    if kwargs.get("task"):
        input_data["task"] = kwargs["task"]
    if kwargs.get("assistant_id"):
        input_data["assistantId"] = kwargs["assistant_id"]
    elif kwargs.get("workflow_id"):
        input_data["workflowId"] = kwargs["workflow_id"]
    run = SchedulerRun(
        scheduler_id=scheduler_id,
        status="running",
        trigger="scheduled",
        started_at=started_at,
        input_data=input_data or None,
    )
    repo = SchedulerRunRepository()
    run = repo.create(run)

    try:
        # CR-003: capture retval so conversation_id and resource_execution_id can be persisted
        retval = await actor_fn(**kwargs)
        finished_at = datetime.now(timezone.utc)
        duration_ms = int((finished_at - started_at).total_seconds() * 1000)
        update_fields: dict = {"status": "completed", "finished_at": finished_at, "duration_ms": duration_ms}
        if isinstance(retval, dict):
            if retval.get("conversation_id"):
                update_fields["conversation_id"] = retval["conversation_id"]
            if retval.get("resource_execution_id"):
                update_fields["resource_execution_id"] = retval["resource_execution_id"]
            if retval.get("metrics"):
                update_fields["metrics"] = retval["metrics"]
        repo.update(run.id, update_fields)
    except Exception as exc:
        finished_at = datetime.now(timezone.utc)
        duration_ms = int((finished_at - started_at).total_seconds() * 1000)
        repo.update(
            run.id,
            {
                "status": "failed",
                "finished_at": finished_at,
                "duration_ms": duration_ms,
                "error_data": {"message": str(exc), "type": type(exc).__name__},
            },
        )
        raise


def _extract_datasource_metrics(payload) -> dict | None:
    """Read per-run token/cost metrics from the IndexInfo attached to a datasource payload.

    DatasourceMonitoringCallback overwrites IndexInfo.tokens_usage after each run with the
    current run's data, so reloading from DB immediately after the actor returns gives us
    the run-scoped figures without needing a before/after delta.
    Returns None when the payload carries no index_info (e.g. SVN, code reindex tasks).
    """
    index_info = getattr(payload, "index_info", None)
    if index_info is None:
        return None
    try:
        from codemie.rest_api.models.index import IndexInfo

        fresh = IndexInfo.get_by_id(str(index_info.id))
        if fresh is None or fresh.tokens_usage is None:
            return None
        return {
            "inputTokens": fresh.tokens_usage.input_tokens,
            "outputTokens": fresh.tokens_usage.output_tokens,
            "cost": fresh.tokens_usage.money_spent,
        }
    except Exception:
        return None


def _tracked_run_sync(actor_fn, scheduler_id: str, resource_id: str | None = None, **kwargs):
    """Synchronous mirror of _tracked_run for datasource actors on the thread-pool executor."""
    from codemie.repository.scheduler_run_repository import SchedulerRunRepository
    from codemie.rest_api.models.scheduler_run import SchedulerRun

    started_at = datetime.now(timezone.utc)
    input_data = {"resourceId": resource_id} if resource_id else None
    run = SchedulerRun(
        scheduler_id=scheduler_id,
        status="running",
        trigger="scheduled",
        started_at=started_at,
        input_data=input_data,
    )
    repo = SchedulerRunRepository()
    run = repo.create(run)

    try:
        outcome = actor_fn(**kwargs)
        finished_at = datetime.now(timezone.utc)
        duration_ms = int((finished_at - started_at).total_seconds() * 1000)
        timing = {"finished_at": finished_at, "duration_ms": duration_ms}
        outcome_status = outcome.get("status") if isinstance(outcome, dict) else None
        if outcome_status == "skipped":
            repo.update(run.id, {"status": "skipped", **timing, "result_data": {"content": outcome.get("reason")}})
        elif outcome_status == "failed":
            error_data = {"message": outcome.get("reason"), "type": "ReindexNotStarted"}
            repo.update(run.id, {"status": "failed", **timing, "error_data": error_data})
        else:
            metrics = _extract_datasource_metrics(kwargs.get("payload"))
            repo.update(run.id, {"status": "completed", **timing, "metrics": metrics})
    except Exception as exc:
        finished_at = datetime.now(timezone.utc)
        duration_ms = int((finished_at - started_at).total_seconds() * 1000)
        repo.update(
            run.id,
            {
                "status": "failed",
                "finished_at": finished_at,
                "duration_ms": duration_ms,
                "error_data": {"message": str(exc), "type": type(exc).__name__},
            },
        )
        raise


def _to_index_type_str(index_type) -> str:
    return index_type.value if isinstance(index_type, CodeIndexType) else index_type


class Job:
    """Triggered job model"""

    id: str
    modified_at: datetime
    instance: AsyncIOScheduler

    def __init__(self, job_id, modified_at, instance):
        self.id = job_id
        self.modified_at = modified_at
        self.instance = instance


class Cron:
    """Core service for triggers"""

    scheduler: AsyncIOScheduler | None
    jobs: Dict[str, Job]
    cache: CacheManager

    def __init__(self):
        """Initialize Cron instance"""
        self.scheduler = None
        self.jobs = {}
        self.cache = CacheManager(cache_ttl=300)
        self._resuming_ids: set[str] = set()
        self._resuming_lock = threading.Lock()
        self._watchdog_executor = ThreadPoolExecutor(max_workers=5, thread_name_prefix="stale-watchdog")
        # APScheduler listeners run on the event loop thread, so the DB insert is handed off.
        self._dropped_run_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dropped-run-recorder")

    async def start_async(self):
        """Start the trigger engine asynchronously"""
        if self.scheduler is not None:
            logger.warning("Cron scheduler already running, skipping start")
            return

        self.scheduler = AsyncIOScheduler(
            executors={
                "default": APSThreadPoolExecutor(max_workers=config.CRON_SCHEDULER_MAX_WORKERS),
                "asyncio": AsyncIOExecutor(),
            },
            job_defaults={
                # APScheduler defaults misfire_grace_time to 1 second and evaluates it in
                # the worker thread when the job is picked up, so every job that waited for
                # a free thread was discarded instead of run late. None means never discard —
                # run late rather than skip.
                "misfire_grace_time": None,
                # Collapse a backlog into a single run rather than firing repeatedly, and
                # never run two instances of the same reindex concurrently.
                "coalesce": True,
                "max_instances": 1,
            },
        )
        self.scheduler.add_listener(
            self.__on_job_event,
            EVENT_JOB_MISSED | EVENT_JOB_ERROR | EVENT_JOB_MAX_INSTANCES,
        )
        self.scheduler.start()
        logger.info("Trigger Engine Cron binding started on %s", platform.uname().node)
        self.scheduler.add_job(self.__watch_settings, "interval", seconds=10, executor="asyncio")
        if config.STALE_INDEXING_WATCHDOG_ENABLED:
            self.scheduler.add_job(self.__watch_stale_indexing, "interval", seconds=60)

    def __record_dropped_run(self, event, reason: str):
        """Queue a ``skipped`` scheduler_run row for a run APScheduler dropped; never raises or blocks."""
        if event.job_id not in self.jobs:
            return
        try:
            self._dropped_run_executor.submit(self.__write_dropped_run, event, reason)
        except Exception as exc:  # e.g. executor already shut down
            logger.error("Failed to record dropped scheduled run: job_id=%s error=%s", event.job_id, exc)

    def __write_dropped_run(self, event, reason: str):
        try:
            from codemie.repository.scheduler_run_repository import SchedulerRunRepository
            from codemie.rest_api.models.scheduler_run import SchedulerRun

            started_at = getattr(event, "scheduled_run_time", None) or datetime.now(timezone.utc)
            SchedulerRunRepository().create(
                SchedulerRun(
                    scheduler_id=event.job_id,
                    status="skipped",
                    trigger="scheduled",
                    started_at=started_at,
                    finished_at=started_at,
                    duration_ms=0,
                    input_data={"resourceId": event.job_id},
                    result_data={"content": reason},
                )
            )
        except Exception as exc:
            logger.error("Failed to record dropped scheduled run: job_id=%s error=%s", event.job_id, exc)

    def __on_job_event(self, event):
        """Report scheduled runs that did not happen.

        Without this, a run dropped because the pool was busy looked exactly like the
        feature being broken: nothing was written to the datasource record, the UI, or any
        metric. Successful runs are deliberately not logged — they are the overwhelming
        majority and the actors already log them.
        """
        if event.code == EVENT_JOB_MISSED:
            logger.warning(
                "Scheduled job MISSED - dropped without running: job_id=%s scheduled_for=%s",
                event.job_id,
                event.scheduled_run_time,
            )
            self.__record_dropped_run(event, "Scheduled run missed: dropped without running")
        elif event.code == EVENT_JOB_MAX_INSTANCES:
            logger.warning(
                "Scheduled job SKIPPED - previous run still in progress: job_id=%s",
                event.job_id,
            )
            self.__record_dropped_run(event, "Skipped: previous run still in progress")
        elif event.code == EVENT_JOB_ERROR:
            # Deliberately no exc_info: LogFormatter replaces the message with the
            # traceback whenever it is set (configs/logger.py), which would hide the
            # job id — and this line exists to make the job id visible. The exception
            # text is interpolated instead; the actor logs its own traceback.
            logger.error(
                "Scheduled job FAILED: job_id=%s scheduled_for=%s error=%r",
                event.job_id,
                event.scheduled_run_time,
                event.exception,
            )

    def shutdown(self):
        """Shutdown the trigger engine and cleanup resources"""
        if self.scheduler is None:
            logger.debug("Cron scheduler not running, skipping shutdown")
            return

        logger.info("Shutting down Trigger Engine Cron binding on %s", platform.uname().node)

        # Remove all jobs
        for job_id in list(self.jobs.keys()):  # Safe iteration - copy keys before iteration
            try:
                self.scheduler.remove_job(job_id)
                logger.debug("Removed job during shutdown: %s", job_id)
            except Exception as e:
                logger.warning("Error removing job %s during shutdown: %s", job_id, e)

        self.jobs.clear()

        # Shutdown watchdog thread pool
        with self._resuming_lock:
            in_flight = set(self._resuming_ids)
        if in_flight:
            logger.warning(
                "Stale indexing watchdog: shutting down with %d resume(s) still in flight: %s",
                len(in_flight),
                in_flight,
            )
        self._watchdog_executor.shutdown(wait=False)
        self._dropped_run_executor.shutdown(wait=False)

        # Shutdown scheduler
        try:
            self.scheduler.shutdown(wait=False)
            logger.info("Scheduler shutdown completed")
        except Exception as e:
            logger.error("Error during scheduler shutdown: %s", e, exc_info=True)
        finally:
            self.scheduler = None

    async def __watch_settings(self):
        """Watch for changes in the settings"""
        # Clean expired cache entries once per watcher cycle (not per validation)
        self.cache.clean_expired()
        user_settings = await asyncio.to_thread(self.__get_settings)
        self.remove_jobs_for_deleted_settings(user_settings)
        self.__actualize_jobs(settings=user_settings)

    def __watch_stale_indexing(self):
        """Detect and resume datasource index jobs stuck in IN_PROGRESS."""
        stale = IndexInfo.get_stale_in_progress(
            STORAGE_CONFIG.stale_indexing_threshold_seconds,
            limit=STORAGE_CONFIG.stale_indexing_resume_batch_size,
        )
        if stale:
            logger.info(f"Stale indexing watchdog: detected {len(stale)} stuck job(s)")
        for index_info in stale:
            with self._resuming_lock:
                if index_info.id in self._resuming_ids:
                    logger.debug(f"Stale indexing watchdog: index_id={index_info.id} already resuming, skipping")
                    continue
                # Reserve the slot optimistically before the DB call so a second
                # watchdog tick on this pod cannot submit the same job concurrently.
                self._resuming_ids.add(index_info.id)

            # Atomic DB-level claim — no lock needed; the UPDATE is atomic at DB level.
            # Must run outside _resuming_lock to avoid blocking the scheduler thread
            # on a remote DB round-trip while the lock is held.
            try:
                claimed = IndexInfo.try_claim_for_resume(index_info.id, STORAGE_CONFIG.stale_indexing_threshold_seconds)
            except Exception as e:
                logger.error(
                    f"Stale indexing watchdog: DB claim failed for index_id={index_info.id}: {e}", exc_info=True
                )
                with self._resuming_lock:
                    self._resuming_ids.discard(index_info.id)
                continue

            if not claimed:
                logger.debug(
                    f"Stale indexing watchdog: index_id={index_info.id} already claimed by another pod, skipping"
                )
                with self._resuming_lock:
                    self._resuming_ids.discard(index_info.id)
                continue

            # Reload the record so __run_resume uses the post-claim update_date
            # (which is the correct incremental reindex watermark), not the stale snapshot.
            fresh_index_info = IndexInfo.find_by_id(index_info.id)
            if not fresh_index_info:
                logger.warning(f"Stale indexing watchdog: index_id={index_info.id} vanished after claim, skipping")
                with self._resuming_lock:
                    self._resuming_ids.discard(index_info.id)
                continue

            self._watchdog_executor.submit(self.__run_resume, fresh_index_info)

    def __run_resume(self, index_info: IndexInfo) -> None:
        """Run a stale resume in a thread pool worker, removing the in-flight marker on completion."""
        from codemie.rest_api.models.index import IndexDeletedException

        try:
            resume_stale_datasource(index_info)
        except IndexDeletedException:
            logger.info(f"Datasource {index_info.id} was deleted during resume, stopping gracefully")
        except Exception as e:
            logger.error(f"Stale indexing watchdog: failed to resume index_id={index_info.id}: {e}", exc_info=True)
        finally:
            with self._resuming_lock:
                self._resuming_ids.discard(index_info.id)

    def remove_jobs_for_deleted_settings(self, settings):
        """Remove jobs for deleted settings"""
        # Optimized: Use set for O(1) lookup instead of O(n) nested loop
        setting_ids = {setting.id for setting in settings}

        for job_id in list(self.jobs.keys()):
            if job_id not in setting_ids:
                logger.info("Removed scheduled job since no settings found: %s", job_id)
                self.__remove_job_safely(job_id)
                del self.jobs[job_id]

    def __actualize_jobs(self, settings):
        """Actualize triggers"""
        for setting in settings:
            # Scheduling must be inside the guard as well as validation: an exception from
            # __actualize_cron_job used to escape __watch_settings and kill the whole pass,
            # leaving every setting after this one unprocessed on every subsequent tick.
            try:
                valid_setting = self.__valid_setting(setting)
                if valid_setting:
                    self.__actualize_cron_job(
                        cron_expression=valid_setting.get("schedule"),
                        resource_id=valid_setting.get("resource_id"),
                        is_enabled=valid_setting.get("is_enabled"),
                        resource_type=valid_setting.get("resource_type"),
                        job_id=setting.id,
                        user_id=setting.user_id,
                        resource_name=valid_setting.get("resource_name"),
                        project_name=valid_setting.get("project_name"),
                        index_type=valid_setting.get("index_type"),
                        jql=valid_setting.get("jql"),
                        prompt=valid_setting.get("prompt"),
                        timezone=valid_setting.get("timezone"),
                    )
            except Exception as exc:
                # No exc_info here either: with it set, LogFormatter swaps the message
                # for the traceback and the setting id — the one thing needed to find
                # the offending row — never reaches the log.
                logger.error(
                    "Unexpected error processing setting id=%s, skipping: %r",
                    setting.id,
                    exc,
                )
                continue

    def __updated_setting(self, setting):
        """Check if setting has been updated"""
        # check if setting has been updated
        if setting.id in self.jobs:
            if setting.update_date > self.jobs[setting.id].modified_at:
                logger.info("Found updated Trigger setting: %s", setting.id)
                return True
            return False
        return True

    def __sanitize_prompt(self, prompt, setting_id):
        """Sanitize and validate prompt value."""
        if not prompt:
            return DEFAULT_TASK_PROMPT

        prompt = str(prompt).strip()
        if len(prompt) > config.SCHEDULER_PROMPT_SIZE_LIMIT:
            logger.warning("Prompt too long in setting %s, truncating", setting_id)
            prompt = prompt[: config.SCHEDULER_PROMPT_SIZE_LIMIT]

        return prompt if prompt else DEFAULT_TASK_PROMPT

    def __fetch_resource_details(self, resource_type_lower, resource_id, bad_resource_message):
        """Fetch and return resource detail dict for the given type, or None on failure."""
        if resource_type_lower == "assistant":
            assistant = validate_assistant(resource_id)
            if not assistant:
                logger.error(bad_resource_message)
                return None
            return {"resource_name": assistant.name, "project_name": "", "index_type": "", "jql": ""}

        if resource_type_lower == "datasource":
            try:
                ds_meta = validate_datasource(resource_id)
            except (DatasourceNotValidated, NotImplementedDatasource) as exc:
                logger.error("Datasource validation error for resource_id=%s: %s", resource_id, exc)
                ds_meta = None
            if not ds_meta:
                logger.error(bad_resource_message)
                return None
            return {
                "resource_name": ds_meta.repo_name,
                "project_name": ds_meta.project_name,
                "index_type": ds_meta.index_type,
                "jql": ds_meta.jira.jql if ds_meta.jira else "",
            }

        return {"resource_name": "", "project_name": "", "index_type": "", "jql": ""}

    def __validate_resource(self, resource_type, resource_id, bad_resource_message):
        """Validate resource based on type and return resource details with caching"""
        cache_key = f"{resource_type}:{resource_id}"

        if self.cache.is_valid(cache_key):
            return self.cache.get(cache_key)

        resource_type_lower = (resource_type or "").lower()
        result = self.__fetch_resource_details(resource_type_lower, resource_id, bad_resource_message)

        # Cache the result (even if None, to avoid repeated failed validations)
        self.cache.set(cache_key, result)

        return result

    def __valid_setting(self, setting):
        """Validate setting"""
        if not self.__updated_setting(setting):
            return False

        is_enabled = self.__get_cred_value(setting, "is_enabled")
        if is_enabled is None:
            # An absent key means the user left the toggle off: the Integration form omits
            # it in that case, and the integration list renders such rows as Disabled.
            # Treating absence as enabled fired schedules nobody enabled (EPMCDME-14128).
            is_enabled = False
        resource_type = self.__get_cred_value(setting, "resource_type")

        schedule = self.__get_cred_value(setting, "schedule")
        if not self.__valid_schedule(schedule):
            logger.error("Invalid schedule in setting: %s", setting.id)
            return False

        resource_id = self.__get_cred_value(setting, "resource_id")
        prompt = self.__sanitize_prompt(self.__get_cred_value(setting, "prompt"), setting.id)

        bad_resource_message = (
            "Resource ID %s from setting %s failed validation: %s",
            resource_id,
            setting.id,
        )

        resource_details = self.__validate_resource(resource_type, resource_id, bad_resource_message)
        if resource_details is None:
            return False

        timezone = self.__get_cred_value(setting, "timezone")

        return {
            "schedule": schedule,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "is_enabled": is_enabled,
            "prompt": prompt,
            "timezone": timezone,
            **resource_details,
        }

    def __actualize_cron_job(
        self,
        cron_expression,
        resource_id,
        resource_type,
        job_id,
        is_enabled,
        user_id,
        project_name=None,
        resource_name=None,
        index_type=None,
        jql=None,
        prompt=None,
        timezone: Optional[str] = None,
    ):
        """Parse cron expression"""
        if not is_enabled:
            self.__remove_disabled_job(job_id)
            return

        cron_trigger = self.__create_cron_trigger(cron_expression, resource_id, resource_type, timezone=timezone)
        instance = self.__schedule_job_by_type(
            resource_type,
            index_type,
            cron_trigger,
            job_id,
            resource_id,
            user_id,
            resource_name,
            project_name,
            jql,
            prompt,
        )

        if instance:
            self.jobs[job_id] = Job(job_id=job_id, modified_at=datetime.now(), instance=instance)

    def __remove_disabled_job(self, job_id):
        """Remove disabled job from scheduler"""
        if job_id in self.jobs:
            self.__remove_job_safely(job_id)
            del self.jobs[job_id]
            logger.info("Removed job: %s", job_id)

    def __remove_job_safely(self, job_id):
        """Remove a job, tolerating one that the scheduler no longer knows about.

        An unguarded remove_job raises JobLookupError, which would propagate out of the
        watcher pass and stop every remaining setting from being processed.
        """
        try:
            self.scheduler.remove_job(job_id)
        except JobLookupError:
            logger.debug("Job %s was already removed from the scheduler", job_id)

    @staticmethod
    def __create_cron_trigger(
        cron_expression, resource_id=None, resource_type: str = RESOURCE_TYPE_DATASOURCE, timezone: Optional[str] = None
    ):
        """Create cron trigger from expression

        Jobs fire at exactly the time the user asked for. Bursts of schedules sharing one
        expression are absorbed by the executor queue, not by spreading the fire times:
        CronTrigger's jitter picks the next slot after the previous jittered run, so a
        window wider than the schedule period silently skips runs.

        What *is* spread is datasources that all happen to share the same expression
        (e.g. everyone picking "Daily at midnight"): each gets a deterministic
        per-datasource minute/hour offset from ``cron_stagger`` so they don't all land
        in the reindex queue at the same instant. ``stagger_cron`` itself only staggers
        when ``resource_type`` is "datasource" -- assistant/workflow schedules fire at
        the exact time requested.
        """
        minute, hour, day_of_month, month, day_of_week = stagger_cron(
            resource_id, cron_expression, resource_type
        ).split()
        return CronTrigger(
            minute=minute,
            hour=hour,
            day=day_of_month,
            month=month,
            day_of_week=normalize_day_of_week(day_of_week),
            timezone=timezone or config.TIMEZONE,
        )

    def __schedule_job_by_type(
        self,
        resource_type,
        index_type,
        cron_trigger,
        job_id,
        resource_id,
        user_id,
        resource_name,
        project_name,
        jql,
        prompt,
    ):
        """Schedule job based on resource type"""
        resource_type_lower = (resource_type or "").lower()
        if resource_type_lower == "assistant":
            return self.__schedule_assistant_job(cron_trigger, job_id, resource_id, resource_name, user_id, prompt)
        elif resource_type_lower == "workflow":
            return self.__schedule_workflow_job(cron_trigger, job_id, resource_id, user_id, prompt)
        elif resource_type_lower == "datasource":
            return self.__schedule_datasource_job(
                index_type, cron_trigger, job_id, resource_id, user_id, project_name, resource_name, jql
            )
        else:
            logger.error("Resource type not supported: %s", resource_type)
            return None

    def __schedule_assistant_job(self, cron_trigger, job_id, resource_id, resource_name, user_id, prompt):
        """Schedule assistant job"""
        task_prompt = prompt or DEFAULT_TASK_PROMPT
        logger.info(
            "Scheduling assistant job %s (%s) with custom prompt: %s...", job_id, resource_name, task_prompt[:50]
        )
        return self.scheduler.add_job(
            _tracked_run,
            trigger=cron_trigger,
            id=job_id,
            replace_existing=True,
            executor="asyncio",
            kwargs={
                "actor_fn": invoke_assistant,
                "scheduler_id": job_id,
                "assistant_id": resource_id,
                "user_id": user_id,
                "job_id": job_id,
                "task": task_prompt,
                "trigger_source": "Scheduler",
            },
        )

    def __schedule_workflow_job(self, cron_trigger, job_id, resource_id, user_id, prompt):
        """Schedule workflow job"""
        task_prompt = prompt or DEFAULT_TASK_PROMPT
        logger.info("Scheduling workflow job %s with custom prompt: %s...", job_id, task_prompt[:50])
        return self.scheduler.add_job(
            _tracked_run,
            trigger=cron_trigger,
            id=job_id,
            replace_existing=True,
            executor="asyncio",
            kwargs={
                "actor_fn": invoke_workflow,
                "scheduler_id": job_id,
                "workflow_id": resource_id,
                "user_id": user_id,
                "job_id": job_id,
                "task": task_prompt,
            },
        )

    def __get_index_info_cached(self, resource_id: str):
        """Get index info with caching to avoid repeated DB queries"""
        return self.cache.fetch_with_cache(
            f"index_info:{resource_id}",
            lambda: IndexInfo.get_by_id(resource_id),
            f"Error fetching index_info {resource_id}",
        )

    def __schedule_datasource_job(
        self, index_type, cron_trigger, job_id, resource_id, user_id, project_name, resource_name, jql
    ):
        """Schedule datasource job based on index type"""
        user = resolve_trigger_user(user_id)
        if not user:
            logger.error("User not found: %s", user_id)
            return None

        index_info = self.__get_index_info_cached(resource_id)
        if not index_info:
            logger.error("IndexInfo not found for resource_id: %s", resource_id)
            return None

        # Build payload based on index type
        index_type_str = _to_index_type_str(index_type)

        if index_info.repo_type == "svn":
            svn_repos = SVNRepo.get_by_app_id(app_id=project_name)
            svn_repo = next((r for r in svn_repos if r.name == resource_name), None)
            if not svn_repo:
                logger.error("SVN repo '%s' not found in project '%s'", resource_name, project_name)
                return None
            payload = SVNReindexTask(
                project_name=project_name,
                resource_id=job_id,
                resource_name=resource_name,
                user=user,
                index_info=index_info,
                svn_repo_id=svn_repo.id,
            )
            return self.scheduler.add_job(
                _tracked_run_sync,
                trigger=cron_trigger,
                id=job_id,
                replace_existing=True,
                kwargs={"actor_fn": reindex_svn, "scheduler_id": job_id, "resource_id": job_id, "payload": payload},
            )
        elif index_type_str in (CodeIndexType.CODE, CodeIndexType.SUMMARY, CodeIndexType.CHUNK_SUMMARY):
            # Get repo_id from the Git repository
            repo_id = GitRepo.identifier_from_fields(
                app_id=project_name, name=resource_name, index_type=CodeIndexType(index_type_str)
            )
            payload = CodeReindexTask(
                project_name=project_name,
                resource_id=job_id,
                resource_name=resource_name,
                user=user,
                index_info=index_info,
                repo_id=repo_id,
            )
            return self.scheduler.add_job(
                _tracked_run_sync,
                trigger=cron_trigger,
                id=job_id,
                replace_existing=True,
                kwargs={"actor_fn": reindex_code, "scheduler_id": job_id, "resource_id": job_id, "payload": payload},
            )
        return self.__schedule_knowledge_base_job(
            index_type=index_type,
            index_type_str=index_type_str,
            cron_trigger=cron_trigger,
            job_id=job_id,
            user=user,
            index_info=index_info,
            project_name=project_name,
            resource_name=resource_name,
            jql=jql,
        )

    def __schedule_knowledge_base_job(
        self, index_type, index_type_str, cron_trigger, job_id, user, index_info, project_name, resource_name, jql
    ):
        """Schedule the knowledge-base half of the datasource dispatch.

        Split out of __schedule_datasource_job as-is: that chain had grown one branch past the
        cognitive-complexity gate. Branch bodies are unchanged.
        """
        if index_type_str == FullDatasourceTypes.JIRA.value:
            payload = JiraReindexTask(
                project_name=project_name,
                resource_id=job_id,
                resource_name=resource_name,
                user=user,
                index_info=index_info,
                jql=jql,
            )
            return self.scheduler.add_job(
                _tracked_run_sync,
                trigger=cron_trigger,
                id=job_id,
                replace_existing=True,
                kwargs={"actor_fn": reindex_jira, "scheduler_id": job_id, "resource_id": job_id, "payload": payload},
            )
        elif index_type_str == FullDatasourceTypes.CONFLUENCE.value:
            payload = ConfluenceReindexTask(
                project_name=project_name,
                resource_id=job_id,
                resource_name=resource_name,
                user=user,
                index_info=index_info,
                confluence_index_info=index_info.confluence,
            )
            return self.scheduler.add_job(
                _tracked_run_sync,
                trigger=cron_trigger,
                id=job_id,
                replace_existing=True,
                kwargs={
                    "actor_fn": reindex_confluence,
                    "scheduler_id": job_id,
                    "resource_id": job_id,
                    "payload": payload,
                },
            )
        elif index_type_str == FullDatasourceTypes.GIT_FAQ.value:
            payload = GitFaqReindexTask(
                project_name=project_name,
                resource_id=job_id,
                resource_name=resource_name,
                user=user,
                index_info=index_info,
            )
            return self.scheduler.add_job(
                reindex_git_faq,
                trigger=cron_trigger,
                id=job_id,
                replace_existing=True,
                kwargs={"payload": payload},
            )
        elif index_type_str == FullDatasourceTypes.GOOGLE.value:
            payload = GoogleReindexTask(
                project_name=project_name,
                resource_id=job_id,
                resource_name=resource_name,
                user=user,
                index_info=index_info,
                google_doc_link=index_info.google_doc_link,
            )
            return self.scheduler.add_job(
                _tracked_run_sync,
                trigger=cron_trigger,
                id=job_id,
                replace_existing=True,
                kwargs={"actor_fn": reindex_google, "scheduler_id": job_id, "resource_id": job_id, "payload": payload},
            )
        elif index_type_str == FullDatasourceTypes.AZURE_DEVOPS_WIKI.value:
            payload = AzureDevOpsWikiReindexTask(
                project_name=project_name,
                resource_id=job_id,
                resource_name=resource_name,
                user=user,
                index_info=index_info,
                azure_devops_wiki_index_info=index_info.azure_devops_wiki,
            )
            return self.scheduler.add_job(
                _tracked_run_sync,
                trigger=cron_trigger,
                id=job_id,
                replace_existing=True,
                kwargs={
                    "actor_fn": reindex_azure_devops_wiki,
                    "scheduler_id": job_id,
                    "resource_id": job_id,
                    "payload": payload,
                },
            )
        elif index_type_str == FullDatasourceTypes.AZURE_DEVOPS_WORK_ITEM.value:
            payload = AzureDevOpsWorkItemReindexTask(
                project_name=project_name,
                resource_id=job_id,
                resource_name=resource_name,
                user=user,
                index_info=index_info,
                azure_devops_work_item_index_info=index_info.azure_devops_work_item,
            )
            return self.scheduler.add_job(
                _tracked_run_sync,
                trigger=cron_trigger,
                id=job_id,
                replace_existing=True,
                kwargs={
                    "actor_fn": reindex_azure_devops_work_item,
                    "scheduler_id": job_id,
                    "resource_id": job_id,
                    "payload": payload,
                },
            )
        elif index_type_str == FullDatasourceTypes.XWIKI.value:
            payload = XWikiReindexTask(
                project_name=project_name,
                resource_id=job_id,
                resource_name=resource_name,
                user=user,
                index_info=index_info,
                xwiki_index_info=index_info.xwiki,
            )
            return self.scheduler.add_job(
                _tracked_run_sync,
                trigger=cron_trigger,
                id=job_id,
                replace_existing=True,
                kwargs={"actor_fn": reindex_xwiki, "scheduler_id": job_id, "resource_id": job_id, "payload": payload},
            )
        elif index_type_str == "knowledge_base_xray":
            payload = XrayReindexTask(
                project_name=project_name,
                resource_id=job_id,
                resource_name=resource_name,
                user=user,
                index_info=index_info,
            )
            return self.scheduler.add_job(
                _tracked_run_sync,
                trigger=cron_trigger,
                id=job_id,
                replace_existing=True,
                kwargs={"actor_fn": reindex_xray, "scheduler_id": job_id, "resource_id": job_id, "payload": payload},
            )
        elif index_type_str == "knowledge_base_sharepoint":
            payload = SharePointReindexTask(
                project_name=project_name,
                resource_id=job_id,
                resource_name=resource_name,
                user=user,
                index_info=index_info,
            )
            return self.scheduler.add_job(
                _tracked_run_sync,
                trigger=cron_trigger,
                id=job_id,
                replace_existing=True,
                kwargs={
                    "actor_fn": reindex_sharepoint,
                    "scheduler_id": job_id,
                    "resource_id": job_id,
                    "payload": payload,
                },
            )
        else:
            logger.error("Datasource index type not supported: %s", index_type)
            return None

    @staticmethod
    def __get_cred_value(setting, key):
        """Get credential value"""
        cred = next((cred for cred in setting.credential_values if cred.key == key), None)
        if not cred:
            return None

        return cred.value

    @staticmethod
    def __valid_schedule(schedule):
        """Validate schedule"""
        if schedule:
            try:
                croniter(str(schedule))
            except Exception as e:
                logger.error("Invalid cron expression: %s", e)
                return False
        return True

    @staticmethod
    def __get_settings(
        credential_type: CredentialTypes = CredentialTypes.SCHEDULER,
    ):
        """Get setting by credential type and setting type"""

        search_fields = {
            SearchFields.CREDENTIAL_TYPE: credential_type,
        }

        settings = Settings.get_all_by_fields(fields=search_fields)

        return settings
