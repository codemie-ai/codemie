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

import platform
from datetime import datetime
from typing import Dict
from croniter import croniter
from apscheduler.triggers.cron import CronTrigger
from apscheduler.schedulers.asyncio import AsyncIOScheduler


from codemie.configs import logger, config
from codemie.core.constants import CodeIndexType
from codemie.core.models import GitRepo
from codemie.rest_api.models.index import IndexInfo
from codemie_tools.base.models import CredentialTypes
from codemie.rest_api.models.settings import Settings
from codemie.rest_api.security.user import User
from codemie.service.constants import FullDatasourceTypes
from codemie.service.settings.base_settings import SearchFields
from codemie.triggers.actors.assistant import invoke_assistant
from codemie.triggers.actors.datasource import (
    reindex_azure_devops_wiki,
    reindex_azure_devops_work_item,
    reindex_code,
    reindex_confluence,
    reindex_google,
    reindex_jira,
)
from codemie.triggers.actors.workflow import invoke_workflow
from codemie.triggers.bindings.cache_manager import CacheManager
from codemie.triggers.bindings.utils import validate_assistant, validate_datasource
from codemie.triggers.trigger_models import (
    AzureDevOpsWikiReindexTask,
    AzureDevOpsWorkItemReindexTask,
    CodeReindexTask,
    ConfluenceReindexTask,
    GoogleReindexTask,
    JiraReindexTask,
)

# Constants
DEFAULT_TASK_PROMPT = "Do it"


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

    async def start_async(self):
        """Start the trigger engine asynchronously"""
        if self.scheduler is not None:
            logger.warning("Cron scheduler already running, skipping start")
            return

        self.scheduler = AsyncIOScheduler()
        self.scheduler.start()
        logger.info("Trigger Engine Cron binding started on %s", platform.uname().node)
        self.scheduler.add_job(self.__watch_settings, "interval", seconds=10)

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

        # Shutdown scheduler
        try:
            self.scheduler.shutdown(wait=False)
            logger.info("Scheduler shutdown completed")
        except Exception as e:
            logger.error("Error during scheduler shutdown: %s", e, exc_info=True)
        finally:
            self.scheduler = None

    def __watch_settings(self):
        """Watch for changes in the settings"""
        # Clean expired cache entries once per watcher cycle (not per validation)
        self.cache.clean_expired()
        user_settings = self.__get_settings()
        self.remove_jobs_for_deleted_settings(user_settings)
        self.__actualize_jobs(settings=user_settings)

    def remove_jobs_for_deleted_settings(self, settings):
        """Remove jobs for deleted settings"""
        # Optimized: Use set for O(1) lookup instead of O(n) nested loop
        setting_ids = {setting.id for setting in settings}

        for job_id in list(self.jobs.keys()):
            if job_id not in setting_ids:
                logger.info("Removed scheduled job since no settings found: %s", job_id)
                self.scheduler.remove_job(job_id)
                del self.jobs[job_id]

    def __actualize_jobs(self, settings):
        """Actualize triggers"""
        for setting in settings:
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
                )

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

    def __validate_resource(self, resource_type, resource_id, bad_resource_message):
        """Validate resource based on type and return resource details with caching"""
        cache_key = f"{resource_type}:{resource_id}"

        # Check cache first
        cached_result = self.cache.get(cache_key)
        if cached_result is not None:
            return cached_result

        # Cache miss - perform validation
        result: dict | None = None
        if resource_type == "assistant":
            assistant = validate_assistant(resource_id)
            if not assistant:
                logger.error(bad_resource_message)
            else:
                result = {"resource_name": assistant.name, "project_name": "", "index_type": "", "jql": ""}

        elif resource_type == "datasource":
            ds_meta = validate_datasource(resource_id)
            if not ds_meta:
                logger.error(bad_resource_message)
            else:
                result = {
                    "resource_name": ds_meta.repo_name,
                    "project_name": ds_meta.project_name,
                    "index_type": ds_meta.index_type,
                    "jql": ds_meta.jira.jql if ds_meta.jira else "",
                }
        else:
            result = {"resource_name": "", "project_name": "", "index_type": "", "jql": ""}

        # Cache the result (even if None, to avoid repeated failed validations)
        self.cache.set(cache_key, result)

        return result

    def __valid_setting(self, setting):
        """Validate setting"""
        if not self.__updated_setting(setting):
            return False

        is_enabled = self.__get_cred_value(setting, "is_enabled")
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

        return {
            "schedule": schedule,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "is_enabled": is_enabled,
            "prompt": prompt,
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
    ):
        """Parse cron expression"""
        if not is_enabled:
            self.__remove_disabled_job(job_id)
            return

        cron_trigger = self.__create_cron_trigger(cron_expression)
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
            self.scheduler.remove_job(job_id)
            del self.jobs[job_id]
            logger.info("Removed job: %s", job_id)

    @staticmethod
    def __create_cron_trigger(cron_expression):
        """Create cron trigger from expression"""
        minute, hour, day_of_month, month, day_of_week = cron_expression.split()
        return CronTrigger(
            minute=minute,
            hour=hour,
            day=day_of_month,
            month=month,
            day_of_week=day_of_week,
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
        if resource_type == "assistant":
            return self.__schedule_assistant_job(cron_trigger, job_id, resource_id, resource_name, user_id, prompt)
        elif resource_type == "workflow":
            return self.__schedule_workflow_job(cron_trigger, job_id, resource_id, user_id, prompt)
        elif resource_type == "datasource":
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
            invoke_assistant,
            trigger=cron_trigger,
            id=job_id,
            replace_existing=True,
            kwargs={
                "assistant_id": resource_id,
                "user_id": user_id,
                "job_id": job_id,
                "task": task_prompt,
            },
        )

    def __schedule_workflow_job(self, cron_trigger, job_id, resource_id, user_id, prompt):
        """Schedule workflow job"""
        task_prompt = prompt or DEFAULT_TASK_PROMPT
        logger.info("Scheduling workflow job %s with custom prompt: %s...", job_id, task_prompt[:50])
        return self.scheduler.add_job(
            invoke_workflow,
            trigger=cron_trigger,
            id=job_id,
            replace_existing=True,
            kwargs={
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
        user = User(id=user_id)
        if not user:
            logger.error("User not found: %s", user_id)
            return None

        index_info = self.__get_index_info_cached(resource_id)
        if not index_info:
            logger.error("IndexInfo not found for resource_id: %s", resource_id)
            return None

        # Build payload based on index type
        index_type_str = index_type.value if isinstance(index_type, CodeIndexType) else index_type

        if index_type_str == "code":
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
                reindex_code,
                trigger=cron_trigger,
                id=job_id,
                replace_existing=True,
                kwargs={"payload": payload},
            )
        elif index_type_str == "knowledge_base_jira":
            payload = JiraReindexTask(
                project_name=project_name,
                resource_id=job_id,
                resource_name=resource_name,
                user=user,
                index_info=index_info,
                jql=jql,
            )
            return self.scheduler.add_job(
                reindex_jira,
                trigger=cron_trigger,
                id=job_id,
                replace_existing=True,
                kwargs={"payload": payload},
            )
        elif index_type_str == "knowledge_base_confluence":
            payload = ConfluenceReindexTask(
                project_name=project_name,
                resource_id=job_id,
                resource_name=resource_name,
                user=user,
                index_info=index_info,
                confluence_index_info=index_info.confluence,
            )
            return self.scheduler.add_job(
                reindex_confluence,
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
                reindex_google,
                trigger=cron_trigger,
                id=job_id,
                replace_existing=True,
                kwargs={"payload": payload},
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
                reindex_azure_devops_wiki,
                trigger=cron_trigger,
                id=job_id,
                replace_existing=True,
                kwargs={"payload": payload},
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
                reindex_azure_devops_work_item,
                trigger=cron_trigger,
                id=job_id,
                replace_existing=True,
                kwargs={"payload": payload},
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
