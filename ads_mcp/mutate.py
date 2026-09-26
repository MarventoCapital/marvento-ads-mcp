# Copyright 2026 Marvento Labs.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Shared helpers for the write (mutate) tools.

Every write tool in this server goes through this module so that the same
safety rails apply everywhere:

* ``validate_only`` dry-runs are supported on every mutate.
* Anything that can start or increase spend requires ``confirm=True``.
  Without it the request is validated against the API and a preview is
  returned, but nothing changes.
* Daily budgets are capped by ``ADS_MCP_MAX_DAILY_BUDGET`` (account currency
  units, default 500). Raising the cap is a server-side decision, not a tool
  parameter, so a misfired tool call cannot lift it.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, Iterable, List

from fastmcp.exceptions import ToolError
from google.ads.googleads.errors import GoogleAdsException
from google.api_core import protobuf_helpers

import ads_mcp.utils as utils

MICROS = 1_000_000

DEFAULT_MAX_DAILY_BUDGET = 500.0


class Guard:
    """Sentinel results for tools that stopped before mutating anything."""

    PREVIEW = "preview"
    APPLIED = "applied"
    VALIDATED = "validated"


# --------------------------------------------------------------------------- #
# Money
# --------------------------------------------------------------------------- #


def to_micros(amount: float | int) -> int:
    """Converts an amount in account currency units to micros.

    Rounds to the nearest 10,000 micros (one cent) so that the value is
    accepted for every currency the API supports.
    """
    if amount is None:
        raise ToolError("An amount is required.")
    if amount < 0:
        raise ToolError("Amounts cannot be negative.")
    micros = int(round(float(amount) * MICROS))
    return int(round(micros / 10_000)) * 10_000


def from_micros(micros: int | None) -> float | None:
    if micros is None:
        return None
    return round(micros / MICROS, 2)


def max_daily_budget() -> float:
    raw = os.environ.get("ADS_MCP_MAX_DAILY_BUDGET")
    if not raw:
        return DEFAULT_MAX_DAILY_BUDGET
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_MAX_DAILY_BUDGET


def check_budget_cap(daily_amount: float) -> None:
    cap = max_daily_budget()
    if daily_amount > cap:
        raise ToolError(
            f"Daily budget {daily_amount:.2f} exceeds the server cap of "
            f"{cap:.2f} (ADS_MCP_MAX_DAILY_BUDGET). The cap is a server "
            "setting; ask the operator to raise it if this amount is intended."
        )


# --------------------------------------------------------------------------- #
# Resource names
# --------------------------------------------------------------------------- #


def id_from_resource_name(resource_name: str) -> str:
    """Returns the trailing id of a resource name, e.g. 'customers/1/campaigns/2' -> '2'."""
    return resource_name.rsplit("/", 1)[-1]


def clean_id(value: str | int) -> str:
    return re.sub(r"\D", "", str(value))


def campaign_rn(customer_id: str, campaign_id: str | int) -> str:
    return f"customers/{clean_id(customer_id)}/campaigns/{clean_id(campaign_id)}"


def budget_rn(customer_id: str, budget_id: str | int) -> str:
    return f"customers/{clean_id(customer_id)}/campaignBudgets/{clean_id(budget_id)}"


def ad_group_rn(customer_id: str, ad_group_id: str | int) -> str:
    return f"customers/{clean_id(customer_id)}/adGroups/{clean_id(ad_group_id)}"


def ad_group_ad_rn(
    customer_id: str, ad_group_id: str | int, ad_id: str | int
) -> str:
    return (
        f"customers/{clean_id(customer_id)}/adGroupAds/"
        f"{clean_id(ad_group_id)}~{clean_id(ad_id)}"
    )


def ad_rn(customer_id: str, ad_id: str | int) -> str:
    return f"customers/{clean_id(customer_id)}/ads/{clean_id(ad_id)}"


def ad_group_criterion_rn(
    customer_id: str, ad_group_id: str | int, criterion_id: str | int
) -> str:
    return (
        f"customers/{clean_id(customer_id)}/adGroupCriteria/"
        f"{clean_id(ad_group_id)}~{clean_id(criterion_id)}"
    )


def campaign_criterion_rn(
    customer_id: str, campaign_id: str | int, criterion_id: str | int
) -> str:
    return (
        f"customers/{clean_id(customer_id)}/campaignCriteria/"
        f"{clean_id(campaign_id)}~{clean_id(criterion_id)}"
    )


def asset_rn(customer_id: str, asset_id: str | int) -> str:
    return f"customers/{clean_id(customer_id)}/assets/{clean_id(asset_id)}"


# --------------------------------------------------------------------------- #
# Enums and masks
# --------------------------------------------------------------------------- #


def enum_value(client, enum_name: str, member: str):
    """Resolves ``member`` on ``client.enums.<enum_name>`` or raises a ToolError."""
    enum_cls = getattr(client.enums, enum_name)
    key = str(member).strip().upper()
    try:
        return getattr(enum_cls, key)
    except AttributeError:
        valid = [
            e.name
            for e in enum_cls
            if e.name not in ("UNSPECIFIED", "UNKNOWN")
        ]
        raise ToolError(
            f"'{member}' is not a valid {enum_name}. Valid values: {', '.join(valid)}"
        )


def set_update_mask(client, operation) -> None:
    """Fills ``operation.update_mask`` from the fields set on ``operation.update``."""
    client.copy_from(
        operation.update_mask,
        protobuf_helpers.field_mask(None, operation.update._pb),
    )


# --------------------------------------------------------------------------- #
# Executing mutates
# --------------------------------------------------------------------------- #


def _format_google_ads_exception(ex: GoogleAdsException) -> str:
    lines = [f"Request ID: {ex.request_id}"]
    for error in ex.failure.errors:
        location = ""
        if error.location and error.location.field_path_elements:
            location = " (" + ".".join(
                f"{e.field_name}[{e.index}]" if "index" in e else e.field_name
                for e in error.location.field_path_elements
            ) + ")"
        lines.append(f"Google Ads API Error: {error.message}{location}")
    return "\n".join(lines)


def run_mutate(
    client,
    service_name: str,
    method_name: str,
    request_type: str,
    customer_id: str,
    operations: Iterable[Any],
    *,
    validate_only: bool = False,
    login_customer_id: str | int | None = None,
) -> List[str]:
    """Executes a mutate request and returns the resource names it produced.

    Args:
        client: A GoogleAdsClient (already bound to the caller's credentials).
        service_name: e.g. "CampaignService".
        method_name: e.g. "mutate_campaigns".
        request_type: e.g. "MutateCampaignsRequest".
        customer_id: The customer the mutate runs against.
        operations: The operation protos.
        validate_only: When True the API validates without applying.
        login_customer_id: Optional manager account id for the login header.

    Returns:
        The list of resource names created/updated. Empty for validate_only.
    """
    service = utils.get_googleads_service(
        service_name, login_customer_id=login_customer_id
    )
    request = client.get_type(request_type)
    request.customer_id = clean_id(customer_id)
    request.operations.extend(list(operations))
    request.validate_only = bool(validate_only)
    request.partial_failure = False

    try:
        response = getattr(service, method_name)(request=request)
    except GoogleAdsException as ex:
        raise ToolError(_format_google_ads_exception(ex))

    results = getattr(response, "results", [])
    return [r.resource_name for r in results if r.resource_name]


def confirm_gate(
    confirm: bool, validate_only: bool, action: str
) -> tuple[bool, str]:
    """Decides how a spend-affecting tool should run.

    Returns:
        (effective_validate_only, outcome) where outcome is one of the
        ``Guard`` constants. When ``confirm`` is False the mutate is forced
        into validate_only mode and the outcome is ``Guard.PREVIEW``.
    """
    if validate_only:
        return True, Guard.VALIDATED
    if not confirm:
        return True, Guard.PREVIEW
    return False, Guard.APPLIED


def outcome_message(outcome: str, action: str) -> str:
    if outcome == Guard.APPLIED:
        return f"{action}: applied."
    if outcome == Guard.VALIDATED:
        return f"{action}: validated with Google, nothing changed (validate_only=true)."
    return (
        f"{action}: validated with Google, nothing changed. This action affects "
        "spend or removes something, so it needs confirm=true to apply. "
        "Show the user this preview and call again with confirm=true once they agree."
    )


def result(
    outcome: str,
    action: str,
    resource_names: List[str] | None = None,
    **extra: Any,
) -> Dict[str, Any]:
    """Uniform tool result."""
    out: Dict[str, Any] = {
        "outcome": outcome,
        "message": outcome_message(outcome, action),
    }
    if resource_names is not None:
        out["resource_names"] = resource_names
        out["ids"] = [id_from_resource_name(rn) for rn in resource_names]
    out.update(extra)
    return out


# --------------------------------------------------------------------------- #
# Lookups that several tools share
# --------------------------------------------------------------------------- #


def gaql(
    customer_id: str,
    query: str,
    login_customer_id: str | int | None = None,
):
    """Runs a GAQL query and yields rows."""
    service = utils.get_googleads_service(
        "GoogleAdsService", login_customer_id=login_customer_id
    )
    try:
        for batch in service.search_stream(
            customer_id=clean_id(customer_id), query=query
        ):
            for row in batch.results:
                yield row
    except GoogleAdsException as ex:
        raise ToolError(_format_google_ads_exception(ex))


def resolve_language_ids(
    customer_id: str,
    language_codes: List[str],
    login_customer_id: str | int | None = None,
) -> Dict[str, int]:
    """Maps ISO language codes ('en', 'ar', 'pt') to language constant ids."""
    if not language_codes:
        return {}
    codes = sorted({c.strip().lower() for c in language_codes if c.strip()})
    quoted = ",".join(f"'{c}'" for c in codes)
    query = (
        "SELECT language_constant.id, language_constant.code "
        f"FROM language_constant WHERE language_constant.code IN ({quoted})"
    )
    found: Dict[str, int] = {}
    for row in gaql(customer_id, query, login_customer_id):
        found[row.language_constant.code.lower()] = int(row.language_constant.id)
    missing = [c for c in codes if c not in found]
    if missing:
        raise ToolError(
            f"Unknown language code(s): {', '.join(missing)}. Use ISO codes such as "
            "'en', 'ar', 'pt', 'fr', 'de', 'es', 'hi', 'ur', 'ru', 'zh_CN'."
        )
    return found
