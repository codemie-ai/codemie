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

from fastapi import APIRouter
from fastapi.encoders import jsonable_encoder
from codemie.configs.customer_config import customer_config, Component
from typing import List
from codemie.rest_api.models.application import Application

router = APIRouter(
    tags=["customer_config"],
    prefix="/v1",
    dependencies=[],
)


@router.get("/config", response_model=List[Component], response_model_exclude_none=True)
async def get_config():
    return jsonable_encoder(customer_config.get_enabled_components())


@router.get("/applications", response_model=List[Application])
async def get_applications():
    components = customer_config.get_enabled_components()

    # Filter for components with IDs starting with 'applications:'
    application_components = [component for component in components if component.id.startswith('applications:')]

    applications = []
    for app in application_components:
        slug = app.id.replace('applications:', '', 1)

        applications.append(
            Application(
                name=app.settings.name,
                description=app.settings.description or '',
                slug=slug,
                entry=app.settings.url,
                type=app.settings.type,
                created_by=app.settings.created_by,
                icon_url=app.settings.icon_url,
                arguments=getattr(app.settings, "arguments", None),
            )
        )

    return jsonable_encoder(applications)
