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

"""Write tools for campaigns, budgets and campaign-level targeting.

Safety model (applies to every tool here):
  * New campaigns are created PAUSED. Enabling is a separate, confirmed step.
  * Budgets are capped by the server (ADS_MCP_MAX_DAILY_BUDGET).
  * Changing status to ENABLED/REMOVED and changing a budget need confirm=true.
    Without it the call is a validated preview and nothing changes.
  * validate_only=true dry-runs any tool against the API.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

import ads_mcp.mutate as m
import ads_mcp.utils as utils

campaigns_mcp = FastMCP("campaigns")

_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False)
_DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False)
_READ = ToolAnnotations(readOnlyHint=True)


# --------------------------------------------------------------------------- #
# Budgets
# --------------------------------------------------------------------------- #


@campaigns_mcp.tool(annotations=_WRITE)
def create_campaign_budget(
    customer_id: str,
    name: str,
    daily_amount: float,
    delivery_method: Literal["STANDARD", "ACCELERATED"] = "STANDARD",
    explicitly_shared: bool = False,
    validate_only: bool = False,
    login_customer_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Creates a campaign budget. Do this before create_campaign.

    Args:
        customer_id: Google Ads customer id (digits only).
        name: Budget name, unique within the account.
        daily_amount: Average daily budget in the account's currency (e.g. 150 for AED 150/day).
            Capped by the server setting ADS_MCP_MAX_DAILY_BUDGET.
        delivery_method: STANDARD spreads spend across the day. Leave as STANDARD.
        explicitly_shared: True to allow several campaigns to share this budget.
        validate_only: Dry-run against the API without creating anything.
        login_customer_id: Manager account id if the customer is accessed through a manager.

    Returns:
        outcome, resource_names, ids and the amount in micros.
    """
    m.check_budget_cap(daily_amount)
    client = utils.get_googleads_client(login_customer_id=login_customer_id)
    op = client.get_type("CampaignBudgetOperation")
    budget = op.create
    budget.name = name
    budget.amount_micros = m.to_micros(daily_amount)
    budget.delivery_method = m.enum_value(
        client, "BudgetDeliveryMethodEnum", delivery_method
    )
    budget.explicitly_shared = explicitly_shared

    rns = m.run_mutate(
        client,
        "CampaignBudgetService",
        "mutate_campaign_budgets",
        "MutateCampaignBudgetsRequest",
        customer_id,
        [op],
        validate_only=validate_only,
        login_customer_id=login_customer_id,
    )
    outcome = m.Guard.VALIDATED if validate_only else m.Guard.APPLIED
    return m.result(
        outcome,
        f"Create budget '{name}' at {daily_amount:.2f}/day",
        rns,
        amount_micros=budget.amount_micros,
    )


@campaigns_mcp.tool(annotations=_DESTRUCTIVE)
def update_campaign_budget(
    customer_id: str,
    budget_id: str,
    daily_amount: float,
    confirm: bool = False,
    validate_only: bool = False,
    login_customer_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Changes the daily amount of an existing budget. Needs confirm=true to apply.

    Args:
        customer_id: Google Ads customer id.
        budget_id: The campaign budget id (campaign_budget.id in GAQL, or from campaign.campaign_budget).
        daily_amount: New average daily budget in account currency. Capped by ADS_MCP_MAX_DAILY_BUDGET.
        confirm: Must be true to apply. False returns a validated preview.
        validate_only: Dry-run only.
        login_customer_id: Manager account id if applicable.
    """
    m.check_budget_cap(daily_amount)
    client = utils.get_googleads_client(login_customer_id=login_customer_id)
    op = client.get_type("CampaignBudgetOperation")
    budget = op.update
    budget.resource_name = m.budget_rn(customer_id, budget_id)
    budget.amount_micros = m.to_micros(daily_amount)
    m.set_update_mask(client, op)

    vo, outcome = m.confirm_gate(confirm, validate_only, "update budget")
    rns = m.run_mutate(
        client,
        "CampaignBudgetService",
        "mutate_campaign_budgets",
        "MutateCampaignBudgetsRequest",
        customer_id,
        [op],
        validate_only=vo,
        login_customer_id=login_customer_id,
    )
    return m.result(
        outcome,
        f"Set budget {budget_id} to {daily_amount:.2f}/day",
        rns if rns else [budget.resource_name],
        amount_micros=budget.amount_micros,
    )


# --------------------------------------------------------------------------- #
# Campaigns
# --------------------------------------------------------------------------- #

Bidding = Literal[
    "MAXIMIZE_CONVERSIONS",
    "MAXIMIZE_CONVERSION_VALUE",
    "MAXIMIZE_CLICKS",
    "MANUAL_CPC",
]


@campaigns_mcp.tool(annotations=_WRITE)
def create_campaign(
    customer_id: str,
    name: str,
    budget_id: str,
    bidding: Bidding = "MAXIMIZE_CLICKS",
    target_cpa: Optional[float] = None,
    target_roas: Optional[float] = None,
    max_cpc_bid_ceiling: Optional[float] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    include_search_partners: bool = False,
    include_display_network: bool = False,
    location_targeting: Literal["PRESENCE", "PRESENCE_OR_INTEREST"] = "PRESENCE",
    contains_eu_political_advertising: bool = False,
    validate_only: bool = False,
    login_customer_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Creates a Search campaign, always PAUSED. Enable it later with set_campaign_status.

    Typical order: create_campaign_budget -> create_campaign -> add_campaign_targeting
    -> ad_groups.create_ad_group -> ad_groups.add_keywords -> ads.create_responsive_search_ad
    -> review -> set_campaign_status(ENABLED, confirm=true).

    Args:
        customer_id: Google Ads customer id.
        name: Campaign name, unique within the account.
        budget_id: Id of a budget created with create_campaign_budget.
        bidding: MAXIMIZE_CLICKS (good default for a new account without conversion history),
            MAXIMIZE_CONVERSIONS (optionally with target_cpa), MAXIMIZE_CONVERSION_VALUE
            (optionally with target_roas), or MANUAL_CPC.
        target_cpa: Optional target cost per acquisition in account currency (MAXIMIZE_CONVERSIONS only).
        target_roas: Optional target return on ad spend as a ratio, e.g. 4.0 = 400% (MAXIMIZE_CONVERSION_VALUE only).
        max_cpc_bid_ceiling: Optional max CPC ceiling in account currency (MAXIMIZE_CLICKS / MAXIMIZE_CONVERSIONS).
        start_date: YYYY-MM-DD. Defaults to today.
        end_date: YYYY-MM-DD. Defaults to no end date.
        include_search_partners: Show on Google search partner sites. Default off.
        include_display_network: Display expansion. Default off; keep off for Search.
        location_targeting: PRESENCE targets people in the location. PRESENCE_OR_INTEREST also
            targets people interested in it. PRESENCE is the safer default for local businesses.
        contains_eu_political_advertising: Declare EU political advertising (required declaration; default false).
        validate_only: Dry-run against the API without creating anything.
        login_customer_id: Manager account id if applicable.
    """
    client = utils.get_googleads_client(login_customer_id=login_customer_id)
    op = client.get_type("CampaignOperation")
    campaign = op.create
    campaign.name = name
    campaign.status = client.enums.CampaignStatusEnum.PAUSED
    campaign.advertising_channel_type = (
        client.enums.AdvertisingChannelTypeEnum.SEARCH
    )
    campaign.campaign_budget = m.budget_rn(customer_id, budget_id)

    ns = campaign.network_settings
    ns.target_google_search = True
    ns.target_search_network = include_search_partners
    ns.target_content_network = include_display_network
    ns.target_partner_search_network = False

    campaign.geo_target_type_setting.positive_geo_target_type = m.enum_value(
        client, "PositiveGeoTargetTypeEnum", location_targeting
    )
    campaign.contains_eu_political_advertising = (
        client.enums.EuPoliticalAdvertisingStatusEnum.CONTAINS_EU_POLITICAL_ADVERTISING
        if contains_eu_political_advertising
        else client.enums.EuPoliticalAdvertisingStatusEnum.DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING
    )

    if bidding == "MAXIMIZE_CLICKS":
        # target_spend_micros is deprecated; an empty TargetSpend = maximize clicks.
        client.copy_from(campaign.target_spend, client.get_type("TargetSpend"))
        if max_cpc_bid_ceiling is not None:
            campaign.target_spend.cpc_bid_ceiling_micros = m.to_micros(
                max_cpc_bid_ceiling
            )
    elif bidding == "MAXIMIZE_CONVERSIONS":
        # copy_from marks the oneof as set even when no target is given.
        client.copy_from(
            campaign.maximize_conversions, client.get_type("MaximizeConversions")
        )
        if target_cpa is not None:
            campaign.maximize_conversions.target_cpa_micros = m.to_micros(
                target_cpa
            )
        if max_cpc_bid_ceiling is not None:
            campaign.maximize_conversions.cpc_bid_ceiling_micros = m.to_micros(
                max_cpc_bid_ceiling
            )
    elif bidding == "MAXIMIZE_CONVERSION_VALUE":
        client.copy_from(
            campaign.maximize_conversion_value,
            client.get_type("MaximizeConversionValue"),
        )
        if target_roas is not None:
            campaign.maximize_conversion_value.target_roas = float(target_roas)
    elif bidding == "MANUAL_CPC":
        client.copy_from(campaign.manual_cpc, client.get_type("ManualCpc"))
    else:
        raise ToolError(f"Unsupported bidding strategy '{bidding}'.")

    if start_date:
        campaign.start_date_time = _clean_date(start_date) + " 00:00:00"
    if end_date:
        campaign.end_date_time = _clean_date(end_date) + " 23:59:59"

    rns = m.run_mutate(
        client,
        "CampaignService",
        "mutate_campaigns",
        "MutateCampaignsRequest",
        customer_id,
        [op],
        validate_only=validate_only,
        login_customer_id=login_customer_id,
    )
    outcome = m.Guard.VALIDATED if validate_only else m.Guard.APPLIED
    return m.result(
        outcome,
        f"Create Search campaign '{name}' (PAUSED, {bidding})",
        rns,
        status="PAUSED",
        next_steps=[
            "add_campaign_targeting for locations and languages",
            "ad_groups.create_ad_group, ad_groups.add_keywords",
            "ads.create_responsive_search_ad",
            "set_campaign_status ENABLED with confirm=true when ready",
        ],
    )


def _clean_date(value: str) -> str:
    v = value.strip()
    if len(v) == 10 and v[4] == "-" and v[7] == "-":
        return v
    if len(v) == 8 and v.isdigit():
        return f"{v[:4]}-{v[4:6]}-{v[6:]}"
    raise ToolError(f"Dates must be YYYY-MM-DD, got '{value}'.")


@campaigns_mcp.tool(annotations=_DESTRUCTIVE)
def set_campaign_status(
    customer_id: str,
    campaign_id: str,
    status: Literal["ENABLED", "PAUSED", "REMOVED"],
    confirm: bool = False,
    validate_only: bool = False,
    login_customer_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Enables, pauses or removes a campaign.

    PAUSED applies immediately (it only stops spend). ENABLED starts spend and
    REMOVED is permanent, so both need confirm=true; without it you get a
    validated preview.

    Args:
        customer_id: Google Ads customer id.
        campaign_id: The campaign id.
        status: ENABLED, PAUSED or REMOVED.
        confirm: Required true for ENABLED and REMOVED.
        validate_only: Dry-run only.
        login_customer_id: Manager account id if applicable.
    """
    client = utils.get_googleads_client(login_customer_id=login_customer_id)
    op = client.get_type("CampaignOperation")
    campaign = op.update
    campaign.resource_name = m.campaign_rn(customer_id, campaign_id)
    campaign.status = m.enum_value(client, "CampaignStatusEnum", status)
    m.set_update_mask(client, op)

    needs_confirm = status in ("ENABLED", "REMOVED")
    vo, outcome = m.confirm_gate(
        confirm or not needs_confirm, validate_only, "set status"
    )
    rns = m.run_mutate(
        client,
        "CampaignService",
        "mutate_campaigns",
        "MutateCampaignsRequest",
        customer_id,
        [op],
        validate_only=vo,
        login_customer_id=login_customer_id,
    )
    return m.result(
        outcome,
        f"Set campaign {campaign_id} to {status}",
        rns if rns else [campaign.resource_name],
        status=status,
    )


@campaigns_mcp.tool(annotations=_WRITE)
def update_campaign(
    customer_id: str,
    campaign_id: str,
    name: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    budget_id: Optional[str] = None,
    validate_only: bool = False,
    login_customer_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Renames a campaign, changes its dates, or points it at another budget.

    Bidding strategy changes are deliberately not exposed here; create a new
    campaign instead so the learning history is not disturbed by accident.

    Args:
        customer_id: Google Ads customer id.
        campaign_id: The campaign id.
        name: New name.
        start_date: New start date YYYY-MM-DD (only before the campaign has started).
        end_date: New end date YYYY-MM-DD.
        budget_id: Id of another existing budget to attach.
        validate_only: Dry-run only.
        login_customer_id: Manager account id if applicable.
    """
    if not any([name, start_date, end_date, budget_id]):
        raise ToolError("Nothing to update: pass at least one field.")
    client = utils.get_googleads_client(login_customer_id=login_customer_id)
    op = client.get_type("CampaignOperation")
    campaign = op.update
    campaign.resource_name = m.campaign_rn(customer_id, campaign_id)
    if name:
        campaign.name = name
    if start_date:
        campaign.start_date_time = _clean_date(start_date) + " 00:00:00"
    if end_date:
        campaign.end_date_time = _clean_date(end_date) + " 23:59:59"
    if budget_id:
        campaign.campaign_budget = m.budget_rn(customer_id, budget_id)
    m.set_update_mask(client, op)

    rns = m.run_mutate(
        client,
        "CampaignService",
        "mutate_campaigns",
        "MutateCampaignsRequest",
        customer_id,
        [op],
        validate_only=validate_only,
        login_customer_id=login_customer_id,
    )
    outcome = m.Guard.VALIDATED if validate_only else m.Guard.APPLIED
    return m.result(
        outcome,
        f"Update campaign {campaign_id}",
        rns if rns else [campaign.resource_name],
    )


# --------------------------------------------------------------------------- #
# Targeting
# --------------------------------------------------------------------------- #


@campaigns_mcp.tool(annotations=_READ)
def suggest_geo_targets(
    location_names: List[str],
    country_code: str = "AE",
    locale: str = "en",
    login_customer_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Looks up geo target ids for place names. Use before add_campaign_targeting.

    Args:
        location_names: Place names, e.g. ["Dubai", "Abu Dhabi", "United Arab Emirates"].
        country_code: Two-letter country to search within, e.g. AE, PT, GB, US.
        locale: Language of the names, e.g. en.
        login_customer_id: Manager account id if applicable.

    Returns:
        Candidates with id, name, canonical_name, target_type and reach. Pick the
        canonical_name that matches what the user means.
    """
    client = utils.get_googleads_client(login_customer_id=login_customer_id)
    service = utils.get_googleads_service(
        "GeoTargetConstantService", login_customer_id=login_customer_id
    )
    request = client.get_type("SuggestGeoTargetConstantsRequest")
    request.locale = locale
    request.country_code = country_code.upper()
    request.location_names.names.extend([n.strip() for n in location_names if n.strip()])
    try:
        response = service.suggest_geo_target_constants(request=request)
    except Exception as ex:  # GoogleAdsException or transport error
        raise ToolError(f"Geo target lookup failed: {ex}")

    out: List[Dict[str, Any]] = []
    for s in response.geo_target_constant_suggestions:
        gtc = s.geo_target_constant
        out.append(
            {
                "searched": s.search_term,
                "id": int(gtc.id),
                "name": gtc.name,
                "canonical_name": gtc.canonical_name,
                "country_code": gtc.country_code,
                "target_type": gtc.target_type,
                "reach": int(s.reach) if s.reach else None,
            }
        )
    return out


class LocationTarget(BaseModel):
    geo_target_id: int = Field(description="Geo target constant id from suggest_geo_targets.")
    negative: bool = Field(default=False, description="True to exclude this location.")


@campaigns_mcp.tool(annotations=_WRITE)
def add_campaign_targeting(
    customer_id: str,
    campaign_id: str,
    locations: List[LocationTarget] = [],
    language_codes: List[str] = [],
    negative_keywords: List[str] = [],
    negative_keyword_match_type: Literal["EXACT", "PHRASE", "BROAD"] = "PHRASE",
    validate_only: bool = False,
    login_customer_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Adds locations, languages and campaign-level negative keywords to a campaign.

    Args:
        customer_id: Google Ads customer id.
        campaign_id: The campaign id.
        locations: Geo targets to include or exclude (ids from suggest_geo_targets).
        language_codes: ISO codes, e.g. ["en", "ar"]. Resolved to language constants.
        negative_keywords: Search terms the campaign must never show for.
        negative_keyword_match_type: Match type for the negatives (PHRASE is the usual choice).
        validate_only: Dry-run only.
        login_customer_id: Manager account id if applicable.
    """
    if not (locations or language_codes or negative_keywords):
        raise ToolError("Nothing to add: pass locations, language_codes or negative_keywords.")

    client = utils.get_googleads_client(login_customer_id=login_customer_id)
    campaign_rn = m.campaign_rn(customer_id, campaign_id)
    gt_service = client.get_service("GeoTargetConstantService")
    lang_service = client.get_service("GoogleAdsService")
    ops = []

    for loc in locations:
        op = client.get_type("CampaignCriterionOperation")
        crit = op.create
        crit.campaign = campaign_rn
        crit.location.geo_target_constant = gt_service.geo_target_constant_path(
            str(loc.geo_target_id)
        )
        crit.negative = bool(loc.negative)
        ops.append(op)

    lang_ids = m.resolve_language_ids(
        customer_id, language_codes, login_customer_id
    )
    for code, lang_id in lang_ids.items():
        op = client.get_type("CampaignCriterionOperation")
        crit = op.create
        crit.campaign = campaign_rn
        crit.language.language_constant = lang_service.language_constant_path(
            str(lang_id)
        )
        ops.append(op)

    match = m.enum_value(client, "KeywordMatchTypeEnum", negative_keyword_match_type)
    for text in negative_keywords:
        text = text.strip()
        if not text:
            continue
        op = client.get_type("CampaignCriterionOperation")
        crit = op.create
        crit.campaign = campaign_rn
        crit.negative = True
        crit.keyword.text = text
        crit.keyword.match_type = match
        ops.append(op)

    rns = m.run_mutate(
        client,
        "CampaignCriterionService",
        "mutate_campaign_criteria",
        "MutateCampaignCriteriaRequest",
        customer_id,
        ops,
        validate_only=validate_only,
        login_customer_id=login_customer_id,
    )
    outcome = m.Guard.VALIDATED if validate_only else m.Guard.APPLIED
    return m.result(
        outcome,
        f"Add {len(ops)} targeting criteria to campaign {campaign_id}",
        rns,
        locations=[loc.model_dump() for loc in locations],
        languages=lang_ids,
        negative_keywords=negative_keywords,
    )


@campaigns_mcp.tool(annotations=_DESTRUCTIVE)
def remove_campaign_criteria(
    customer_id: str,
    campaign_id: str,
    criterion_ids: List[str],
    confirm: bool = False,
    validate_only: bool = False,
    login_customer_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Removes campaign criteria (locations, languages, negatives) by criterion id. Needs confirm=true.

    Find criterion ids with search on campaign_criterion (campaign_criterion.criterion_id).
    """
    if not criterion_ids:
        raise ToolError("criterion_ids is empty.")
    client = utils.get_googleads_client(login_customer_id=login_customer_id)
    ops = []
    for cid in criterion_ids:
        op = client.get_type("CampaignCriterionOperation")
        op.remove = m.campaign_criterion_rn(customer_id, campaign_id, cid)
        ops.append(op)
    vo, outcome = m.confirm_gate(confirm, validate_only, "remove criteria")
    rns = m.run_mutate(
        client,
        "CampaignCriterionService",
        "mutate_campaign_criteria",
        "MutateCampaignCriteriaRequest",
        customer_id,
        ops,
        validate_only=vo,
        login_customer_id=login_customer_id,
    )
    return m.result(
        outcome,
        f"Remove {len(ops)} criteria from campaign {campaign_id}",
        rns if rns else [op.remove for op in ops],
    )
