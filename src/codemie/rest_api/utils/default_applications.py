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

from codemie.configs import logger
from codemie.core.models import Application

DEMO_PROJECT_NAME = "demo"
CODEMIE_PROJECT_NAME = "codemie"


def ensure_application_exists(project_name: str) -> None:
    """
    Ensures an Application exists for the given project_name.
    Creates it if it doesn't exist.

    Args:
        project_name: The name of the project/application to ensure exists
    """
    from codemie.service.user.application_service import application_service

    application_service.ensure_application_exists(project_name)


def create_default_applications():
    demo_filter_apps = Application.get_all_by_fields({"name": DEMO_PROJECT_NAME})
    codemie_filter_apps = Application.get_all_by_fields({"name": CODEMIE_PROJECT_NAME})

    if len(demo_filter_apps) == 0:
        application = Application(name=DEMO_PROJECT_NAME)
        application.save()
        logger.info(f"Created default application: {DEMO_PROJECT_NAME}")

    if len(codemie_filter_apps) == 0:
        application = Application(name=CODEMIE_PROJECT_NAME)
        application.save()
        logger.info(f"Created default application: {CODEMIE_PROJECT_NAME}")
