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

"""Write tools for responsive search ads."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

import ads_mcp.mutate as m
import ads_mcp.utils as utils

ads_mcp_server = FastMCP("ads")

_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False)
_DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False)

HEADLINE_MAX = 30
DESCRIPTION_MAX = 90
PATH_MAX = 15
HEADLINES_MIN, HEADLINES_MAX = 3, 15
DESCRIPTIONS_MIN, DESCRIPTIONS_MAX = 2, 4

HeadlinePin = Literal["HEADLINE_1", "HEADLINE_2", "HEADLINE_3"]
DescriptionPin = Literal["DESCRIPTION_1", "DESCRIPTION_2"]


class Headline(BaseModel):
    text: str = Field(description="Headline text, max 30 characters.")
    pin: Optional[HeadlinePin] = Field(
        default=None,
        description="Pin to HEADLINE_1/2/3. Pin sparingly; it limits Google's combinations.",
    )


class Description(BaseModel):
    text: str = Field(description="Description text, max 90 characters.")
    pin: Optional[DescriptionPin] = Field(default=None, description="Pin to DESCRIPTION_1/2.")


def _norm_headlines(items: List[Union[str, Headline]]) -> List[Headline]:
    out = []
    for it in items:
        out.append(Headline(text=it) if isinstance(it, str) else it)
    return out


def _norm_descriptions(items: List[Union[str, Description]]) -> List[Description]:
    out = []
    for it in items:
        out.append(Description(text=it) if isinstance(it, str) else it)
    return out


def validate_rsa_copy(
    headlines: List[Headline],
    descriptions: List[Description],
    path1: Optional[str],
    path2: Optional[str],
    final_urls: List[str],
) -> List[str]:
    """Returns a list of problems (empty when the copy is valid)."""
    problems: List[str] = []
    if not (HEADLINES_MIN <= len(headlines) <= HEADLINES_MAX):
        problems.append(
            f"{len(headlines)} headlines given; need between {HEADLINES_MIN} and {HEADLINES_MAX}."
        )
    if not (DESCRIPTIONS_MIN <= len(descriptions) <= DESCRIPTIONS_MAX):
        problems.append(
            f"{len(descriptions)} descriptions given; need between {DESCRIPTIONS_MIN} and {DESCRIPTIONS_MAX}."
        )
    seen = set()
    for i, h in enumerate(headlines, 1):
        t = h.text.strip()
        if not t:
            problems.append(f"Headline {i} is empty.")
        elif len(t) > HEADLINE_MAX:
            problems.append(
                f"Headline {i} is {len(t)} chars (max {HEADLINE_MAX}): '{t}'"
            )
        if t.lower() in seen:
            problems.append(f"Headline {i} duplicates another headline: '{t}'")
        seen.add(t.lower())
        if t.isupper() and len(t) > 4:
            problems.append(f"Headline {i} is all caps, which Google's policy rejects: '{t}'")
        if "!" in t:
            problems.append(f"Headline {i} contains '!', which is not allowed in headlines: '{t}'")
    seen = set()
    for i, d in enumerate(descriptions, 1):
        t = d.text.strip()
        if not t:
            problems.append(f"Description {i} is empty.")
        elif len(t) > DESCRIPTION_MAX:
            problems.append(
                f"Description {i} is {len(t)} chars (max {DESCRIPTION_MAX}): '{t}'"
            )
        if t.lower() in seen:
            problems.append(f"Description {i} duplicates another description: '{t}'")
        seen.add(t.lower())
        if t.count("!") > 1:
            problems.append(f"Description {i} has more than one '!': '{t}'")
    for label, p in (("path1", path1), ("path2", path2)):
        if p and len(p) > PATH_MAX:
            problems.append(f"{label} is {len(p)} chars (max {PATH_MAX}): '{p}'")
        if p and ("/" in p or " " in p):
            problems.append(f"{label} cannot contain slashes or spaces: '{p}'")
    if path2 and not path1:
        problems.append("path2 requires path1.")
    if not final_urls:
        problems.append("At least one final_url is required.")
    for u in final_urls:
        if not (u.startswith("http://") or u.startswith("https://")):
            problems.append(f"final_url must start with http:// or https://: '{u}'")
    return problems


def _fill_rsa(client, ad, headlines, descriptions, path1, path2) -> None:
    rsa = ad.responsive_search_ad
    for h in headlines:
        asset = client.get_type("AdTextAsset")
        asset.text = h.text.strip()
        if h.pin:
            asset.pinned_field = m.enum_value(client, "ServedAssetFieldTypeEnum", h.pin)
        rsa.headlines.append(asset)
    for d in descriptions:
        asset = client.get_type("AdTextAsset")
        asset.text = d.text.strip()
        if d.pin:
            asset.pinned_field = m.enum_value(client, "ServedAssetFieldTypeEnum", d.pin)
        rsa.descriptions.append(asset)
    if path1:
        rsa.path1 = path1.strip()
    if path2:
        rsa.path2 = path2.strip()


@ads_mcp_server.tool(annotations=_WRITE)
def create_responsive_search_ad(
    customer_id: str,
    ad_group_id: str,
    headlines: List[Union[str, Headline]],
    descriptions: List[Union[str, Description]],
    final_urls: List[str],
    path1: Optional[str] = None,
    path2: Optional[str] = None,
    status: Literal["ENABLED", "PAUSED"] = "ENABLED",
    validate_only: bool = False,
    login_customer_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Creates a responsive search ad in an ad group.

    Copy is checked locally against Google's limits before the API call:
    3-15 headlines of max 30 chars, 2-4 descriptions of max 90 chars, display
    paths of max 15 chars, no duplicate lines, no all-caps, no '!' in headlines.
    Aim for 8-15 distinct headlines and 4 descriptions; Google rates ad strength on variety.

    The ad defaults to ENABLED because the campaign is PAUSED until confirmed.

    Args:
        customer_id: Google Ads customer id.
        ad_group_id: The ad group id.
        headlines: Plain strings, or objects {text, pin} to pin a headline to position 1-3.
        descriptions: Plain strings, or objects {text, pin} to pin to DESCRIPTION_1/2.
        final_urls: Landing page URL(s), https.
        path1: Optional display path segment (max 15 chars), e.g. "services".
        path2: Optional second display path segment (requires path1).
        status: ENABLED or PAUSED.
        validate_only: Dry-run. Also runs Google's policy checks without saving.
        login_customer_id: Manager account id if applicable.
    """
    hs = _norm_headlines(headlines)
    ds = _norm_descriptions(descriptions)
    problems = validate_rsa_copy(hs, ds, path1, path2, final_urls)
    if problems:
        raise ToolError("Ad copy problems:\n- " + "\n- ".join(problems))

    client = utils.get_googleads_client(login_customer_id=login_customer_id)
    op = client.get_type("AdGroupAdOperation")
    aga = op.create
    aga.ad_group = m.ad_group_rn(customer_id, ad_group_id)
    aga.status = m.enum_value(client, "AdGroupAdStatusEnum", status)
    aga.ad.final_urls.extend(final_urls)
    _fill_rsa(client, aga.ad, hs, ds, path1, path2)

    rns = m.run_mutate(
        client,
        "AdGroupAdService",
        "mutate_ad_group_ads",
        "MutateAdGroupAdsRequest",
        customer_id,
        [op],
        validate_only=validate_only,
        login_customer_id=login_customer_id,
    )
    outcome = m.Guard.VALIDATED if validate_only else m.Guard.APPLIED
    ad_ids = [rn.rsplit("~", 1)[-1] for rn in rns]
    return m.result(
        outcome,
        f"Create responsive search ad in ad group {ad_group_id}",
        rns,
        ad_ids=ad_ids,
        headline_count=len(hs),
        description_count=len(ds),
        note="Ads go through Google policy review after creation; check ad_group_ad.policy_summary via search.",
    )


@ads_mcp_server.tool(annotations=_WRITE)
def update_responsive_search_ad(
    customer_id: str,
    ad_id: str,
    headlines: Optional[List[Union[str, Headline]]] = None,
    descriptions: Optional[List[Union[str, Description]]] = None,
    final_urls: Optional[List[str]] = None,
    path1: Optional[str] = None,
    path2: Optional[str] = None,
    validate_only: bool = False,
    login_customer_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Replaces the copy of an existing responsive search ad.

    Headlines and descriptions are replaced as whole sets (pass the full list
    you want to end up with). Editing an ad resets its performance history in
    the UI; for A/B tests prefer creating a second ad in the same ad group.

    Args:
        customer_id: Google Ads customer id.
        ad_id: The ad id (ad_group_ad.ad.id).
        headlines: Full replacement list, or omit to keep current.
        descriptions: Full replacement list, or omit to keep current.
        final_urls: Replacement landing page(s), or omit to keep current.
        path1: New display path 1 (pass "" to clear).
        path2: New display path 2 (pass "" to clear).
        validate_only: Dry-run only.
        login_customer_id: Manager account id if applicable.
    """
    if headlines is None and descriptions is None and final_urls is None and path1 is None and path2 is None:
        raise ToolError("Nothing to update.")
    if (headlines is None) != (descriptions is None):
        raise ToolError("Pass headlines and descriptions together (the ad's copy is replaced as a set).")

    hs = _norm_headlines(headlines) if headlines is not None else []
    ds = _norm_descriptions(descriptions) if descriptions is not None else []
    if headlines is not None:
        problems = validate_rsa_copy(
            hs, ds, path1 or None, path2 or None, final_urls or ["https://placeholder.invalid"]
        )
        problems = [p for p in problems if "final_url" not in p]
        if problems:
            raise ToolError("Ad copy problems:\n- " + "\n- ".join(problems))

    client = utils.get_googleads_client(login_customer_id=login_customer_id)
    op = client.get_type("AdOperation")
    ad = op.update
    ad.resource_name = m.ad_rn(customer_id, ad_id)
    if final_urls is not None:
        if not final_urls:
            raise ToolError("final_urls cannot be emptied.")
        ad.final_urls.extend(final_urls)
    if headlines is not None:
        _fill_rsa(client, ad, hs, ds, None, None)
    if path1 is not None:
        ad.responsive_search_ad.path1 = path1.strip()
    if path2 is not None:
        ad.responsive_search_ad.path2 = path2.strip()
    m.set_update_mask(client, op)

    rns = m.run_mutate(
        client,
        "AdService",
        "mutate_ads",
        "MutateAdsRequest",
        customer_id,
        [op],
        validate_only=validate_only,
        login_customer_id=login_customer_id,
    )
    outcome = m.Guard.VALIDATED if validate_only else m.Guard.APPLIED
    return m.result(
        outcome,
        f"Update responsive search ad {ad_id}",
        rns if rns else [ad.resource_name],
    )


@ads_mcp_server.tool(annotations=_DESTRUCTIVE)
def set_ad_status(
    customer_id: str,
    ad_group_id: str,
    ad_id: str,
    status: Literal["ENABLED", "PAUSED", "REMOVED"],
    confirm: bool = False,
    validate_only: bool = False,
    login_customer_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Enables, pauses or removes an ad. REMOVED needs confirm=true.

    Args:
        customer_id: Google Ads customer id.
        ad_group_id: The ad group the ad belongs to.
        ad_id: The ad id.
        status: ENABLED, PAUSED or REMOVED.
        confirm: Required true for REMOVED.
        validate_only: Dry-run only.
        login_customer_id: Manager account id if applicable.
    """
    client = utils.get_googleads_client(login_customer_id=login_customer_id)
    op = client.get_type("AdGroupAdOperation")
    rn = m.ad_group_ad_rn(customer_id, ad_group_id, ad_id)
    if status == "REMOVED":
        op.remove = rn
    else:
        aga = op.update
        aga.resource_name = rn
        aga.status = m.enum_value(client, "AdGroupAdStatusEnum", status)
        m.set_update_mask(client, op)

    vo, outcome = m.confirm_gate(
        confirm or status != "REMOVED", validate_only, "set ad status"
    )
    rns = m.run_mutate(
        client,
        "AdGroupAdService",
        "mutate_ad_group_ads",
        "MutateAdGroupAdsRequest",
        customer_id,
        [op],
        validate_only=vo,
        login_customer_id=login_customer_id,
    )
    return m.result(
        outcome, f"Set ad {ad_id} to {status}", rns if rns else [rn], status=status
    )
