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

from typing import Annotated

from fastapi import APIRouter, Depends, Header, status
from codemie.configs import logger
from codemie.core.constants import HEADER_CODEMIE_CLI, HEADER_CODEMIE_CLIENT
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.metrics import MetricsRequest, MetricsResponse
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.monitoring.base_monitoring_service import BaseMonitoringService
from codemie.service.monitoring.metrics_constants import MetricsAttributes
from codemie.core.utils import calculate_cli_metric_cost

router = APIRouter(tags=["Metrics"], prefix="/v1", dependencies=[Depends(authenticate)])


@router.post(
    "/metrics",
    status_code=status.HTTP_200_OK,
    response_model=MetricsResponse,
    response_model_by_alias=True,
)
def send_metric(
    request: MetricsRequest,
    user: User = Depends(authenticate),
    x_codemie_cli: Annotated[str | None, Header(alias=HEADER_CODEMIE_CLI)] = None,
    x_codemie_client: Annotated[str | None, Header(alias=HEADER_CODEMIE_CLIENT)] = None,
):
    """
    Send a custom count metric using the base monitoring service.

    This endpoint provides a generic interface to the send_count_metric method
    from BaseMonitoringService with the following parameters:
    - name: metric name (will be prefixed with 'frontend_' if X-CodeMie-Client header is not present)
    - attributes: custom attributes dictionary (optional)

    The endpoint automatically adds user context and environment information
    to the metric attributes, including CodeMie client information from headers.

    Headers:
    - X-CodeMie-CLI: CodeMie CLI version information
    - X-CodeMie-Client: CodeMie client type (when present, disables metric name transformation)
    """
    try:
        # Handle metric name - add frontend_ prefix only if X-CodeMie-Client header is not present
        if x_codemie_client:
            # For CodeMie client requests, use metric name as-is (backward compatibility)
            metric_name = request.name
        else:
            # For other clients, add frontend_ prefix if not already present
            metric_name = f"frontend_{request.name}" if not request.name.startswith("frontend_") else request.name

        # Add user information to attributes if available
        attributes = request.attributes or {}
        if user:
            attributes.update(
                {
                    MetricsAttributes.USER_ID: user.id,
                    MetricsAttributes.USER_NAME: user.name,
                    MetricsAttributes.USER_EMAIL: user.username,
                }
            )

        # Add CodeMie client information to attributes if available
        if x_codemie_cli:
            attributes[MetricsAttributes.CODEMIE_CLI] = x_codemie_cli
        if x_codemie_client:
            attributes[MetricsAttributes.CODEMIE_CLIENT] = x_codemie_client

        if x_codemie_cli and request.name == "codemie_cli_usage_total" and 'money_spent' not in attributes:
            # Calculate cost for CLI metrics if not already present
            money_spent, cached_cost, cache_creation_cost = calculate_cli_metric_cost(attributes)
            attributes[MetricsAttributes.MONEY_SPENT] = money_spent
            attributes[MetricsAttributes.CACHED_TOKENS_MONEY_SPENT] = cached_cost
            attributes[MetricsAttributes.CACHE_CREATION_TOKENS_MONEY_SPENT] = cache_creation_cost

            logger.debug(
                f"Calculated CLI cost: model={attributes.get('llm_model')}, "
                f"input={attributes.get('total_input_tokens')}, "
                f"cache_creation={attributes.get('total_cache_creation_tokens')}, "
                f"cache_read={attributes.get('total_cache_read_input_tokens')}, "
                f"output={attributes.get('total_output_tokens')}, "
                f"total=${money_spent:.6f}, cached=${cached_cost:.6f}, creation=${cache_creation_cost:.6f}"
            )

        BaseMonitoringService.send_count_metric(name=metric_name, attributes=attributes)

        return MetricsResponse(success=True, message=f"Metric '{metric_name}' sent successfully")

    except Exception as e:
        logger.error(f"Failed to send metric '{request.name}': {str(e)}", exc_info=True)
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Failed to send metric",
            details=f"An error occurred while sending the metric '{request.name}': {str(e)}",
            help="Please check the metric data and try again. If the issue persists, contact support.",
        ) from e
