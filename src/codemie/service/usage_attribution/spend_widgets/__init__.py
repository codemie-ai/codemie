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

"""Usage Attribution spend widgets: figures the hourly spend ledger produces on its own, one class per entity.

**One engine per figure.** Every read is clipped to the registered coverage, ``[max(start, F), end)``, before the port
is asked, so the part of a window below the floor contributes nothing: the port's legacy Elasticsearch leg is never
reached, and no figure here is ever topped up from Elasticsearch. A window wholly below the floor, or a ledger that is
not activated, gives no figure for every figure - never a zero.

**Plain values.** Each answer is reduced to its bare figures; the partial and unavailable qualifications the port
attaches are dropped, because the Usage Attribution responses carry plain numbers only.

**One widget, one public method.** Each widget in ``widgets`` (``PlatformSpend``, ``PeopleSpend``,
``ProjectSpend``, ``CliUsageSpend``, ``WhoPaysSpend``) subclasses ``SpendWidget``, names its cache view and output type,
and has one ``read``, cached by ``@cached_read``.
A caller's ``WidgetContext`` holds the user, the shared cache and the access boundary, derived once. The widgets
hand out ready data - figures in cents - and the caller lays them into its own response.

**A new widget** is one more ``SpendWidget`` subclass in ``widgets``, with a ``@cached_read`` ``read``, and an output
dataclass in ``base`` when ``SpendFigures`` and ``SpendTable`` do not fit.
"""

from codemie.service.usage_attribution.spend_widgets.base import (
    CliUsageFigures,
    SpendFigures,
    SpendTable,
    SpendWidget,
    WhoPaysFigures,
    WhoPaysSplit,
    WidgetCache,
    WidgetContext,
)
from codemie.service.usage_attribution.spend_widgets.caching import LEDGER_TTL_S, cached_read
from codemie.service.usage_attribution.spend_widgets.widgets import (
    CliUsageSpend,
    PeopleSpend,
    PlatformSpend,
    ProjectSpend,
    WhoPaysSpend,
)

__all__ = [
    "LEDGER_TTL_S",
    "CliUsageFigures",
    "CliUsageSpend",
    "PeopleSpend",
    "PlatformSpend",
    "ProjectSpend",
    "SpendFigures",
    "SpendTable",
    "SpendWidget",
    "WhoPaysFigures",
    "WhoPaysSpend",
    "WhoPaysSplit",
    "WidgetCache",
    "WidgetContext",
    "cached_read",
]
