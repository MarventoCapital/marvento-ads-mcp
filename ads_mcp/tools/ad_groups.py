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

"""Write tools for ad groups, keywords and ad-group-level negatives."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

import ads_mcp.mutate as m
import ads_mcp.utils as utils

ad_groups_mcp = FastMCP("ad_groups")

_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False)
_DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False)

MatchType = Literal["EXACT", "PHRASE", "BROAD"]
Status = Literal["ENABLED", "PAUSED", "REMOVED"]


@ad_groups_mcp.tool(annotations=_WRITE)
def create_ad_group(
    customer_id: str,
    campaign_id: str,
    name: str,
    default_cpc_bid: Optional[float] = None,
    status: Literal["ENABLED", "PAUSED"] = "ENABLED",
    validate_only: bool = False,
    login_customer_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Creates a Search ad group inside a campaign.

    Ad groups default to ENABLED because the campaign itself is created PAUSED;
    nothing serves until the campaign is enabled with confirm=true.

    Args:
        customer_id: Google Ads customer id.
        campaign_id: The parent campaign id.
        name: Ad group name, unique within the campaign. One theme per ad group.
        default_cpc_bid: Default max CPC in account currency. Only used by MANUAL_CPC
            campaigns; ignored by automated bidding.
        status: ENABLED or PAUSED.
        validate_only: Dry-run only.
        login_customer_id: Manager account id if applicable.
    """
    client = utils.get_googleads_client(login_customer_id=login_customer_id)
    op = client.get_type("AdGroupOperation")
    ag = op.create
    ag.name = name
    ag.campaign = m.campaign_rn(customer_id, campaign_id)
    ag.status = m.enum_value(client, "AdGroupStatusEnum", status)
    ag.type_ = client.enums.AdGroupTypeEnum.SEARCH_STANDARD
    if default_cpc_bid is not None:
        ag.cpc_bid_micros = m.to_micros(default_cpc_bid)

    rns = m.run_mutate(
        client,
        "AdGroupService",
        "mutate_ad_groups",
        "MutateAdGroupsRequest",
        customer_id,
        [op],
        validate_only=validate_only,
        login_customer_id=login_customer_id,
    )
    outcome = m.Guard.VALIDATED if validate_only else m.Guard.APPLIED
    return m.result(
        outcome, f"Create ad group '{name}' in campaign {campaign_id}", rns
    )


@ad_groups_mcp.tool(annotations=_DESTRUCTIVE)
def set_ad_group_status(
    customer_id: str,
    ad_group_id: str,
    status: Status,
    confirm: bool = False,
    validate_only: bool = False,
    login_customer_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Enables, pauses or removes an ad group. REMOVED needs confirm=true.

    Args:
        customer_id: Google Ads customer id.
        ad_group_id: The ad group id.
        status: ENABLED, PAUSED or REMOVED.
        confirm: Required true for REMOVED.
        validate_only: Dry-run only.
        login_customer_id: Manager account id if applicable.
    """
    client = utils.get_googleads_client(login_customer_id=login_customer_id)
    op = client.get_type("AdGroupOperation")
    ag = op.update
    ag.resource_name = m.ad_group_rn(customer_id, ad_group_id)
    ag.status = m.enum_value(client, "AdGroupStatusEnum", status)
    m.set_update_mask(client, op)

    vo, outcome = m.confirm_gate(
        confirm or status != "REMOVED", validate_only, "set status"
    )
    rns = m.run_mutate(
        client,
        "AdGroupService",
        "mutate_ad_groups",
        "MutateAdGroupsRequest",
        customer_id,
        [op],
        validate_only=vo,
        login_customer_id=login_customer_id,
    )
    return m.result(
        outcome,
        f"Set ad group {ad_group_id} to {status}",
        rns if rns else [ag.resource_name],
        status=status,
    )


@ad_groups_mcp.tool(annotations=_DESTRUCTIVE)
def update_ad_group_cpc_bid(
    customer_id: str,
    ad_group_id: str,
    default_cpc_bid: float,
    confirm: bool = False,
    validate_only: bool = False,
    login_customer_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Changes an ad group's default max CPC (MANUAL_CPC campaigns). Needs confirm=true.

    Args:
        customer_id: Google Ads customer id.
        ad_group_id: The ad group id.
        default_cpc_bid: New default max CPC in account currency.
        confirm: Must be true to apply.
        validate_only: Dry-run only.
        login_customer_id: Manager account id if applicable.
    """
    client = utils.get_googleads_client(login_customer_id=login_customer_id)
    op = client.get_type("AdGroupOperation")
    ag = op.update
    ag.resource_name = m.ad_group_rn(customer_id, ad_group_id)
    ag.cpc_bid_micros = m.to_micros(default_cpc_bid)
    m.set_update_mask(client, op)

    vo, outcome = m.confirm_gate(confirm, validate_only, "update bid")
    rns = m.run_mutate(
        client,
        "AdGroupService",
        "mutate_ad_groups",
        "MutateAdGroupsRequest",
        customer_id,
        [op],
        validate_only=vo,
        login_customer_id=login_customer_id,
    )
    return m.result(
        outcome,
        f"Set ad group {ad_group_id} default CPC to {default_cpc_bid:.2f}",
        rns if rns else [ag.resource_name],
        cpc_bid_micros=ag.cpc_bid_micros,
    )


class KeywordInput(BaseModel):
    text: str = Field(description="Keyword text, e.g. 'villa maintenance dubai'.")
    match_type: MatchType = Field(
        default="PHRASE",
        description="EXACT, PHRASE or BROAD. PHRASE is the balanced default; BROAD needs conversion data to be safe.",
    )
    cpc_bid: Optional[float] = Field(
        default=None,
        description="Optional keyword-level max CPC in account currency (MANUAL_CPC only).",
    )
    final_url: Optional[str] = Field(
        default=None,
        description="Optional keyword-level landing page override.",
    )


@ad_groups_mcp.tool(annotations=_WRITE)
def add_keywords(
    customer_id: str,
    ad_group_id: str,
    keywords: List[KeywordInput],
    status: Literal["ENABLED", "PAUSED"] = "ENABLED",
    validate_only: bool = False,
    login_customer_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Adds keywords to an ad group.

    Args:
        customer_id: Google Ads customer id.
        ad_group_id: The ad group id.
        keywords: Keywords with match type. Keep an ad group to one tight theme (5-20 keywords).
        status: ENABLED or PAUSED for the new keywords.
        validate_only: Dry-run only. Useful to catch policy or format errors first.
        login_customer_id: Manager account id if applicable.
    """
    if not keywords:
        raise ToolError("keywords is empty.")
    client = utils.get_googleads_client(login_customer_id=login_customer_id)
    ag_rn = m.ad_group_rn(customer_id, ad_group_id)
    st = m.enum_value(client, "AdGroupCriterionStatusEnum", status)
    ops = []
    for kw in keywords:
        text = kw.text.strip()
        if not text:
            continue
        if len(text) > 80:
            raise ToolError(f"Keyword too long (max 80 chars): '{text}'")
        op = client.get_type("AdGroupCriterionOperation")
        crit = op.create
        crit.ad_group = ag_rn
        crit.status = st
        crit.keyword.text = text
        crit.keyword.match_type = m.enum_value(
            client, "KeywordMatchTypeEnum", kw.match_type
        )
        if kw.cpc_bid is not None:
            crit.cpc_bid_micros = m.to_micros(kw.cpc_bid)
        if kw.final_url:
            crit.final_urls.append(kw.final_url)
        ops.append(op)

    rns = m.run_mutate(
        client,
        "AdGroupCriterionService",
        "mutate_ad_group_criteria",
        "MutateAdGroupCriteriaRequest",
        customer_id,
        ops,
        validate_only=validate_only,
        login_customer_id=login_customer_id,
    )
    outcome = m.Guard.VALIDATED if validate_only else m.Guard.APPLIED
    return m.result(
        outcome,
        f"Add {len(ops)} keywords to ad group {ad_group_id}",
        rns,
        keywords=[{"text": k.text, "match_type": k.match_type} for k in keywords],
    )


@ad_groups_mcp.tool(annotations=_WRITE)
def add_ad_group_negative_keywords(
    customer_id: str,
    ad_group_id: str,
    negative_keywords: List[str],
    match_type: MatchType = "PHRASE",
    validate_only: bool = False,
    login_customer_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Adds negative keywords to one ad group. For campaign-wide negatives use campaigns.add_campaign_targeting.

    Args:
        customer_id: Google Ads customer id.
        ad_group_id: The ad group id.
        negative_keywords: Terms this ad group must never show for.
        match_type: EXACT, PHRASE or BROAD.
        validate_only: Dry-run only.
        login_customer_id: Manager account id if applicable.
    """
    if not negative_keywords:
        raise ToolError("negative_keywords is empty.")
    client = utils.get_googleads_client(login_customer_id=login_customer_id)
    ag_rn = m.ad_group_rn(customer_id, ad_group_id)
    mt = m.enum_value(client, "KeywordMatchTypeEnum", match_type)
    ops = []
    for text in negative_keywords:
        text = text.strip()
        if not text:
            continue
        op = client.get_type("AdGroupCriterionOperation")
        crit = op.create
        crit.ad_group = ag_rn
        crit.negative = True
        crit.keyword.text = text
        crit.keyword.match_type = mt
        ops.append(op)

    rns = m.run_mutate(
        client,
        "AdGroupCriterionService",
        "mutate_ad_group_criteria",
        "MutateAdGroupCriteriaRequest",
        customer_id,
        ops,
        validate_only=validate_only,
        login_customer_id=login_customer_id,
    )
    outcome = m.Guard.VALIDATED if validate_only else m.Guard.APPLIED
    return m.result(
        outcome,
        f"Add {len(ops)} negative keywords to ad group {ad_group_id}",
        rns,
    )


@ad_groups_mcp.tool(annotations=_DESTRUCTIVE)
def set_keyword_status(
    customer_id: str,
    ad_group_id: str,
    criterion_ids: List[str],
    status: Status,
    confirm: bool = False,
    validate_only: bool = False,
    login_customer_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Pauses, enables or removes keywords (ad group criteria). REMOVED needs confirm=true.

    Find criterion ids with search on ad_group_criterion (ad_group_criterion.criterion_id).

    Args:
        customer_id: Google Ads customer id.
        ad_group_id: The ad group the criteria belong to.
        criterion_ids: One or more criterion ids.
        status: ENABLED, PAUSED or REMOVED.
        confirm: Required true for REMOVED.
        validate_only: Dry-run only.
        login_customer_id: Manager account id if applicable.
    """
    if not criterion_ids:
        raise ToolError("criterion_ids is empty.")
    client = utils.get_googleads_client(login_customer_id=login_customer_id)
    ops = []
    for cid in criterion_ids:
        op = client.get_type("AdGroupCriterionOperation")
        rn = m.ad_group_criterion_rn(customer_id, ad_group_id, cid)
        if status == "REMOVED":
            op.remove = rn
        else:
            crit = op.update
            crit.resource_name = rn
            crit.status = m.enum_value(client, "AdGroupCriterionStatusEnum", status)
            m.set_update_mask(client, op)
        ops.append(op)

    vo, outcome = m.confirm_gate(
        confirm or status != "REMOVED", validate_only, "set keyword status"
    )
    rns = m.run_mutate(
        client,
        "AdGroupCriterionService",
        "mutate_ad_group_criteria",
        "MutateAdGroupCriteriaRequest",
        customer_id,
        ops,
        validate_only=vo,
        login_customer_id=login_customer_id,
    )
    return m.result(
        outcome,
        f"Set {len(ops)} keywords in ad group {ad_group_id} to {status}",
        rns
        if rns
        else [m.ad_group_criterion_rn(customer_id, ad_group_id, c) for c in criterion_ids],
        status=status,
    )
