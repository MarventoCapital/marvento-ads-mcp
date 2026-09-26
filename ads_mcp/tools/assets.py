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

"""Write tools for campaign assets (sitelinks, callouts) and conversion actions."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

import ads_mcp.mutate as m
import ads_mcp.utils as utils

assets_mcp = FastMCP("assets")

_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False)


class Sitelink(BaseModel):
    link_text: str = Field(description="Max 25 characters.")
    final_url: str = Field(description="https landing page for this sitelink.")
    description1: Optional[str] = Field(default=None, description="Optional, max 35 characters.")
    description2: Optional[str] = Field(default=None, description="Optional, max 35 characters. Requires description1.")


def _link_assets_to_campaign(
    client,
    customer_id: str,
    campaign_id: str,
    asset_rns: List[str],
    field_type: str,
    validate_only: bool,
    login_customer_id: Optional[str],
) -> List[str]:
    ops = []
    for arn in asset_rns:
        op = client.get_type("CampaignAssetOperation")
        ca = op.create
        ca.campaign = m.campaign_rn(customer_id, campaign_id)
        ca.asset = arn
        ca.field_type = m.enum_value(client, "AssetFieldTypeEnum", field_type)
        ops.append(op)
    return m.run_mutate(
        client,
        "CampaignAssetService",
        "mutate_campaign_assets",
        "MutateCampaignAssetsRequest",
        customer_id,
        ops,
        validate_only=validate_only,
        login_customer_id=login_customer_id,
    )


@assets_mcp.tool(annotations=_WRITE)
def add_sitelinks(
    customer_id: str,
    campaign_id: str,
    sitelinks: List[Sitelink],
    validate_only: bool = False,
    login_customer_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Creates sitelink assets and attaches them to a campaign. Add at least 4 for full effect.

    Args:
        customer_id: Google Ads customer id.
        campaign_id: The campaign id.
        sitelinks: 2-20 sitelinks. Link text max 25 chars, descriptions max 35 chars each.
        validate_only: Dry-run. Validates the assets only; linking is skipped in dry-run.
        login_customer_id: Manager account id if applicable.
    """
    if len(sitelinks) < 2:
        raise ToolError("Google requires at least 2 sitelinks per campaign.")
    problems = []
    for i, s in enumerate(sitelinks, 1):
        if len(s.link_text.strip()) > 25:
            problems.append(f"Sitelink {i} link_text over 25 chars: '{s.link_text}'")
        for label, d in (("description1", s.description1), ("description2", s.description2)):
            if d and len(d.strip()) > 35:
                problems.append(f"Sitelink {i} {label} over 35 chars: '{d}'")
        if s.description2 and not s.description1:
            problems.append(f"Sitelink {i} description2 requires description1.")
        if not s.final_url.startswith(("http://", "https://")):
            problems.append(f"Sitelink {i} final_url must start with http(s)://")
    if problems:
        raise ToolError("Sitelink problems:\n- " + "\n- ".join(problems))

    client = utils.get_googleads_client(login_customer_id=login_customer_id)
    ops = []
    for s in sitelinks:
        op = client.get_type("AssetOperation")
        asset = op.create
        asset.final_urls.append(s.final_url)
        asset.sitelink_asset.link_text = s.link_text.strip()
        if s.description1:
            asset.sitelink_asset.description1 = s.description1.strip()
        if s.description2:
            asset.sitelink_asset.description2 = s.description2.strip()
        ops.append(op)

    asset_rns = m.run_mutate(
        client,
        "AssetService",
        "mutate_assets",
        "MutateAssetsRequest",
        customer_id,
        ops,
        validate_only=validate_only,
        login_customer_id=login_customer_id,
    )
    if validate_only:
        return m.result(m.Guard.VALIDATED, f"Create {len(ops)} sitelinks", [])

    link_rns = _link_assets_to_campaign(
        client, customer_id, campaign_id, asset_rns, "SITELINK", False, login_customer_id
    )
    return m.result(
        m.Guard.APPLIED,
        f"Add {len(asset_rns)} sitelinks to campaign {campaign_id}",
        asset_rns,
        campaign_asset_resource_names=link_rns,
    )


@assets_mcp.tool(annotations=_WRITE)
def add_callouts(
    customer_id: str,
    campaign_id: str,
    callouts: List[str],
    validate_only: bool = False,
    login_customer_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Creates callout assets ("Free Quote", "24/7 Support") and attaches them to a campaign.

    Args:
        customer_id: Google Ads customer id.
        campaign_id: The campaign id.
        callouts: 2-20 short phrases, max 25 characters each, no punctuation at the end.
        validate_only: Dry-run. Validates the assets only; linking is skipped in dry-run.
        login_customer_id: Manager account id if applicable.
    """
    texts = [c.strip() for c in callouts if c.strip()]
    if len(texts) < 2:
        raise ToolError("Google requires at least 2 callouts.")
    too_long = [t for t in texts if len(t) > 25]
    if too_long:
        raise ToolError("Callouts over 25 chars: " + "; ".join(too_long))

    client = utils.get_googleads_client(login_customer_id=login_customer_id)
    ops = []
    for t in texts:
        op = client.get_type("AssetOperation")
        op.create.callout_asset.callout_text = t
        ops.append(op)

    asset_rns = m.run_mutate(
        client,
        "AssetService",
        "mutate_assets",
        "MutateAssetsRequest",
        customer_id,
        ops,
        validate_only=validate_only,
        login_customer_id=login_customer_id,
    )
    if validate_only:
        return m.result(m.Guard.VALIDATED, f"Create {len(ops)} callouts", [])

    link_rns = _link_assets_to_campaign(
        client, customer_id, campaign_id, asset_rns, "CALLOUT", False, login_customer_id
    )
    return m.result(
        m.Guard.APPLIED,
        f"Add {len(asset_rns)} callouts to campaign {campaign_id}",
        asset_rns,
        campaign_asset_resource_names=link_rns,
    )


ConversionCategory = Literal[
    "DEFAULT",
    "PAGE_VIEW",
    "PURCHASE",
    "SIGNUP",
    "LEAD",
    "DOWNLOAD",
    "ADD_TO_CART",
    "BEGIN_CHECKOUT",
    "SUBSCRIBE_PAID",
    "PHONE_CALL_LEAD",
    "IMPORTED_LEAD",
    "SUBMIT_LEAD_FORM",
    "BOOK_APPOINTMENT",
    "REQUEST_QUOTE",
    "GET_DIRECTIONS",
    "OUTBOUND_CLICK",
    "CONTACT",
    "ENGAGEMENT",
    "STORE_VISIT",
    "STORE_SALE",
    "QUALIFIED_LEAD",
    "CONVERTED_LEAD",
]


@assets_mcp.tool(annotations=_WRITE)
def create_conversion_action(
    customer_id: str,
    name: str,
    category: ConversionCategory = "SUBMIT_LEAD_FORM",
    default_value: Optional[float] = None,
    always_use_default_value: bool = True,
    counting_type: Literal["ONE_PER_CLICK", "MANY_PER_CLICK"] = "ONE_PER_CLICK",
    click_through_lookback_window_days: int = 30,
    primary_for_goal: bool = True,
    validate_only: bool = False,
    login_customer_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Creates a website conversion action (the thing bidding optimises for).

    After creating it, fetch the tag with search on conversion_action selecting
    conversion_action.tag_snippets and install the event snippet on the site,
    or fire it through Google Tag Manager.

    Args:
        customer_id: Google Ads customer id.
        name: e.g. "Contact form submit".
        category: What the action represents. Leads: SUBMIT_LEAD_FORM, REQUEST_QUOTE,
            BOOK_APPOINTMENT, PHONE_CALL_LEAD, CONTACT. Sales: PURCHASE.
        default_value: Value per conversion in account currency (helps value-based bidding).
        always_use_default_value: True to ignore values sent by the tag.
        counting_type: ONE_PER_CLICK for leads, MANY_PER_CLICK for purchases.
        click_through_lookback_window_days: 1-90.
        primary_for_goal: Whether campaigns bid on this action by default.
        validate_only: Dry-run only.
        login_customer_id: Manager account id if applicable.
    """
    client = utils.get_googleads_client(login_customer_id=login_customer_id)
    op = client.get_type("ConversionActionOperation")
    ca = op.create
    ca.name = name
    ca.type_ = client.enums.ConversionActionTypeEnum.WEBPAGE
    ca.category = m.enum_value(client, "ConversionActionCategoryEnum", category)
    ca.status = client.enums.ConversionActionStatusEnum.ENABLED
    ca.counting_type = m.enum_value(
        client, "ConversionActionCountingTypeEnum", counting_type
    )
    ca.click_through_lookback_window_days = int(click_through_lookback_window_days)
    ca.primary_for_goal = primary_for_goal
    if default_value is not None:
        ca.value_settings.default_value = float(default_value)
        ca.value_settings.always_use_default_value = always_use_default_value

    rns = m.run_mutate(
        client,
        "ConversionActionService",
        "mutate_conversion_actions",
        "MutateConversionActionsRequest",
        customer_id,
        [op],
        validate_only=validate_only,
        login_customer_id=login_customer_id,
    )
    outcome = m.Guard.VALIDATED if validate_only else m.Guard.APPLIED
    return m.result(
        outcome,
        f"Create conversion action '{name}' ({category})",
        rns,
        next_step="search conversion_action.tag_snippets for the install snippet",
    )
