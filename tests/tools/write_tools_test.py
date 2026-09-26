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

"""Tests for the write tools: protos built correctly, safety rails hold.

A real GoogleAdsClient is used for types (no network), and the service
layer is replaced with a recorder so every test can inspect the exact
request that would have been sent.
"""

import os
import unittest
from unittest.mock import MagicMock, patch

from fastmcp.exceptions import ToolError
from google.ads.googleads.client import GoogleAdsClient
from google.oauth2.credentials import Credentials

import ads_mcp.mutate as m
from ads_mcp.tools import ad_groups, ads, assets, campaigns


def _client():
    return GoogleAdsClient(
        credentials=Credentials(token="test-token"),
        developer_token="test-dev-token",
        use_proto_plus=True,
    )


class _Recorder:
    """Stands in for any Ads service; records the request and returns fake results."""

    def __init__(self, resource_names_per_call=None):
        self.requests = []
        self._rns = list(resource_names_per_call or [])

    def __getattr__(self, name):
        if name.startswith("mutate_"):

            def _mutate(request=None, **kwargs):
                self.requests.append(request)
                response = MagicMock()
                if request.validate_only:
                    response.results = []
                else:
                    rns = self._rns.pop(0) if self._rns else [
                        f"customers/{request.customer_id}/fake/{i}"
                        for i in range(len(request.operations))
                    ]
                    response.results = [MagicMock(resource_name=rn) for rn in rns]
                return response

            return _mutate
        raise AttributeError(name)


class WriteToolTestCase(unittest.TestCase):
    def setUp(self):
        self.client = _client()
        self.recorder = _Recorder()
        p1 = patch("ads_mcp.utils.get_googleads_client", return_value=self.client)
        p2 = patch("ads_mcp.utils.get_googleads_service", return_value=self.recorder)
        p1.start()
        p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)
        os.environ.pop("ADS_MCP_MAX_DAILY_BUDGET", None)

    @property
    def last_request(self):
        return self.recorder.requests[-1]


class TestMoney(unittest.TestCase):
    def test_to_micros_rounds_to_cents(self):
        self.assertEqual(m.to_micros(150), 150_000_000)
        self.assertEqual(m.to_micros(0.015), 20_000)
        self.assertEqual(m.to_micros(12.34), 12_340_000)

    def test_negative_rejected(self):
        with self.assertRaises(ToolError):
            m.to_micros(-1)

    def test_budget_cap_env(self):
        os.environ["ADS_MCP_MAX_DAILY_BUDGET"] = "100"
        try:
            with self.assertRaises(ToolError):
                m.check_budget_cap(100.01)
            m.check_budget_cap(100)
        finally:
            os.environ.pop("ADS_MCP_MAX_DAILY_BUDGET")
        m.check_budget_cap(500)
        with self.assertRaises(ToolError):
            m.check_budget_cap(500.5)


class TestBudgets(WriteToolTestCase):
    def test_create_budget_builds_request(self):
        res = campaigns.create_campaign_budget(
            customer_id="123-456-7890", name="B1", daily_amount=150
        )
        req = self.last_request
        self.assertEqual(req.customer_id, "1234567890")
        self.assertFalse(req.validate_only)
        b = req.operations[0].create
        self.assertEqual(b.name, "B1")
        self.assertEqual(b.amount_micros, 150_000_000)
        self.assertEqual(b.delivery_method.name, "STANDARD")
        self.assertFalse(b.explicitly_shared)
        self.assertEqual(res["outcome"], "applied")
        self.assertEqual(res["ids"], ["0"])

    def test_create_budget_over_cap_never_calls_api(self):
        with self.assertRaises(ToolError):
            campaigns.create_campaign_budget(
                customer_id="1", name="B", daily_amount=5000
            )
        self.assertEqual(self.recorder.requests, [])

    def test_update_budget_needs_confirm(self):
        res = campaigns.update_campaign_budget(
            customer_id="1", budget_id="55", daily_amount=200
        )
        self.assertEqual(res["outcome"], "preview")
        self.assertTrue(self.last_request.validate_only)
        op = self.last_request.operations[0]
        self.assertEqual(op.update.resource_name, "customers/1/campaignBudgets/55")
        self.assertIn("amount_micros", op.update_mask.paths)

        res = campaigns.update_campaign_budget(
            customer_id="1", budget_id="55", daily_amount=200, confirm=True
        )
        self.assertEqual(res["outcome"], "applied")
        self.assertFalse(self.last_request.validate_only)


class TestCampaigns(WriteToolTestCase):
    def test_create_campaign_is_paused_search_with_defaults(self):
        res = campaigns.create_campaign(
            customer_id="1", name="C1", budget_id="9"
        )
        c = self.last_request.operations[0].create
        self.assertEqual(c.status.name, "PAUSED")
        self.assertEqual(c.advertising_channel_type.name, "SEARCH")
        self.assertEqual(c.campaign_budget, "customers/1/campaignBudgets/9")
        self.assertTrue(c.network_settings.target_google_search)
        self.assertFalse(c.network_settings.target_search_network)
        self.assertFalse(c.network_settings.target_content_network)
        self.assertEqual(c.geo_target_type_setting.positive_geo_target_type.name, "PRESENCE")
        self.assertEqual(
            c.contains_eu_political_advertising.name,
            "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING",
        )
        self.assertEqual(c._pb.WhichOneof("campaign_bidding_strategy"), "target_spend")
        self.assertEqual(res["status"], "PAUSED")

    def test_bidding_strategies_set_oneof(self):
        campaigns.create_campaign(
            customer_id="1", name="C", budget_id="9",
            bidding="MAXIMIZE_CONVERSIONS", target_cpa=40,
        )
        c = self.last_request.operations[0].create
        self.assertEqual(c._pb.WhichOneof("campaign_bidding_strategy"), "maximize_conversions")
        self.assertEqual(c.maximize_conversions.target_cpa_micros, 40_000_000)

        campaigns.create_campaign(
            customer_id="1", name="C", budget_id="9", bidding="MAXIMIZE_CONVERSIONS"
        )
        c = self.last_request.operations[0].create
        self.assertEqual(c._pb.WhichOneof("campaign_bidding_strategy"), "maximize_conversions")

        campaigns.create_campaign(
            customer_id="1", name="C", budget_id="9",
            bidding="MAXIMIZE_CONVERSION_VALUE", target_roas=3.5,
        )
        c = self.last_request.operations[0].create
        self.assertEqual(c._pb.WhichOneof("campaign_bidding_strategy"), "maximize_conversion_value")
        self.assertAlmostEqual(c.maximize_conversion_value.target_roas, 3.5)

        campaigns.create_campaign(
            customer_id="1", name="C", budget_id="9", bidding="MANUAL_CPC"
        )
        c = self.last_request.operations[0].create
        self.assertEqual(c._pb.WhichOneof("campaign_bidding_strategy"), "manual_cpc")

    def test_dates(self):
        campaigns.create_campaign(
            customer_id="1", name="C", budget_id="9",
            start_date="2026-10-01", end_date="20261231",
        )
        c = self.last_request.operations[0].create
        self.assertEqual(c.start_date_time, "2026-10-01 00:00:00")
        self.assertEqual(c.end_date_time, "2026-12-31 23:59:59")
        with self.assertRaises(ToolError):
            campaigns.create_campaign(
                customer_id="1", name="C", budget_id="9", start_date="01/10/2026"
            )

    def test_validate_only_passthrough(self):
        res = campaigns.create_campaign(
            customer_id="1", name="C", budget_id="9", validate_only=True
        )
        self.assertTrue(self.last_request.validate_only)
        self.assertEqual(res["outcome"], "validated")
        self.assertEqual(res["resource_names"], [])

    def test_status_gate(self):
        # PAUSED applies without confirm.
        res = campaigns.set_campaign_status(customer_id="1", campaign_id="7", status="PAUSED")
        self.assertEqual(res["outcome"], "applied")
        self.assertFalse(self.last_request.validate_only)
        # ENABLED needs confirm.
        res = campaigns.set_campaign_status(customer_id="1", campaign_id="7", status="ENABLED")
        self.assertEqual(res["outcome"], "preview")
        self.assertTrue(self.last_request.validate_only)
        res = campaigns.set_campaign_status(
            customer_id="1", campaign_id="7", status="ENABLED", confirm=True
        )
        self.assertEqual(res["outcome"], "applied")
        op = self.last_request.operations[0]
        self.assertEqual(op.update.resource_name, "customers/1/campaigns/7")
        self.assertEqual(op.update.status.name, "ENABLED")
        self.assertIn("status", op.update_mask.paths)
        # REMOVED needs confirm.
        res = campaigns.set_campaign_status(customer_id="1", campaign_id="7", status="REMOVED")
        self.assertEqual(res["outcome"], "preview")

    def test_update_campaign_requires_a_field(self):
        with self.assertRaises(ToolError):
            campaigns.update_campaign(customer_id="1", campaign_id="7")
        campaigns.update_campaign(customer_id="1", campaign_id="7", name="New", budget_id="3")
        op = self.last_request.operations[0]
        self.assertEqual(op.update.name, "New")
        self.assertEqual(op.update.campaign_budget, "customers/1/campaignBudgets/3")
        self.assertIn("name", op.update_mask.paths)
        self.assertIn("campaign_budget", op.update_mask.paths)

    @patch("ads_mcp.mutate.resolve_language_ids", return_value={"en": 1000, "ar": 1019})
    def test_targeting(self, _langs):
        res = campaigns.add_campaign_targeting(
            customer_id="1",
            campaign_id="7",
            locations=[
                campaigns.LocationTarget(geo_target_id=2784),
                campaigns.LocationTarget(geo_target_id=1000, negative=True),
            ],
            language_codes=["en", "ar"],
            negative_keywords=["free", "jobs"],
        )
        ops = self.last_request.operations
        self.assertEqual(len(ops), 6)
        loc = ops[0].create
        self.assertEqual(loc.campaign, "customers/1/campaigns/7")
        self.assertEqual(loc.location.geo_target_constant, "geoTargetConstants/2784")
        self.assertFalse(loc.negative)
        self.assertTrue(ops[1].create.negative)
        self.assertEqual(ops[2].create.language.language_constant, "languageConstants/1000")
        neg = ops[4].create
        self.assertTrue(neg.negative)
        self.assertEqual(neg.keyword.text, "free")
        self.assertEqual(neg.keyword.match_type.name, "PHRASE")
        self.assertEqual(res["languages"], {"en": 1000, "ar": 1019})

    def test_targeting_requires_something(self):
        with self.assertRaises(ToolError):
            campaigns.add_campaign_targeting(customer_id="1", campaign_id="7")

    def test_remove_criteria_gate(self):
        res = campaigns.remove_campaign_criteria(
            customer_id="1", campaign_id="7", criterion_ids=["11", "12"]
        )
        self.assertEqual(res["outcome"], "preview")
        self.assertEqual(
            self.last_request.operations[1].remove, "customers/1/campaignCriteria/7~12"
        )


class TestAdGroups(WriteToolTestCase):
    def test_create_ad_group(self):
        ad_groups.create_ad_group(
            customer_id="1", campaign_id="7", name="AG", default_cpc_bid=2.5
        )
        ag = self.last_request.operations[0].create
        self.assertEqual(ag.campaign, "customers/1/campaigns/7")
        self.assertEqual(ag.type_.name, "SEARCH_STANDARD")
        self.assertEqual(ag.status.name, "ENABLED")
        self.assertEqual(ag.cpc_bid_micros, 2_500_000)

    def test_add_keywords(self):
        ad_groups.add_keywords(
            customer_id="1",
            ad_group_id="33",
            keywords=[
                ad_groups.KeywordInput(text="villa maintenance dubai"),
                ad_groups.KeywordInput(text="ac repair", match_type="EXACT", cpc_bid=3),
                ad_groups.KeywordInput(text="  ", match_type="BROAD"),
            ],
        )
        ops = self.last_request.operations
        self.assertEqual(len(ops), 2)  # blank keyword skipped
        k0 = ops[0].create
        self.assertEqual(k0.ad_group, "customers/1/adGroups/33")
        self.assertEqual(k0.keyword.match_type.name, "PHRASE")
        self.assertEqual(ops[1].create.keyword.match_type.name, "EXACT")
        self.assertEqual(ops[1].create.cpc_bid_micros, 3_000_000)

    def test_add_keywords_empty(self):
        with self.assertRaises(ToolError):
            ad_groups.add_keywords(customer_id="1", ad_group_id="33", keywords=[])

    def test_negatives(self):
        ad_groups.add_ad_group_negative_keywords(
            customer_id="1", ad_group_id="33", negative_keywords=["cheap"], match_type="EXACT"
        )
        crit = self.last_request.operations[0].create
        self.assertTrue(crit.negative)
        self.assertEqual(crit.keyword.match_type.name, "EXACT")

    def test_keyword_status_and_remove_gate(self):
        res = ad_groups.set_keyword_status(
            customer_id="1", ad_group_id="33", criterion_ids=["5"], status="PAUSED"
        )
        self.assertEqual(res["outcome"], "applied")
        op = self.last_request.operations[0]
        self.assertEqual(op.update.resource_name, "customers/1/adGroupCriteria/33~5")
        res = ad_groups.set_keyword_status(
            customer_id="1", ad_group_id="33", criterion_ids=["5"], status="REMOVED"
        )
        self.assertEqual(res["outcome"], "preview")
        self.assertEqual(self.last_request.operations[0].remove, "customers/1/adGroupCriteria/33~5")

    def test_cpc_bid_gate(self):
        res = ad_groups.update_ad_group_cpc_bid(
            customer_id="1", ad_group_id="33", default_cpc_bid=4
        )
        self.assertEqual(res["outcome"], "preview")
        res = ad_groups.update_ad_group_cpc_bid(
            customer_id="1", ad_group_id="33", default_cpc_bid=4, confirm=True
        )
        self.assertEqual(res["outcome"], "applied")
        self.assertEqual(self.last_request.operations[0].update.cpc_bid_micros, 4_000_000)


class TestAds(WriteToolTestCase):
    GOOD_H = ["Villa Maintenance Dubai", "Same Day AC Repair", "Licensed Technicians"]
    GOOD_D = [
        "Fast, insured maintenance teams across Dubai. Book online in two minutes.",
        "Transparent pricing and a 30 day workmanship guarantee on every job.",
    ]

    def test_copy_validation(self):
        problems = ads.validate_rsa_copy(
            ads._norm_headlines(["A" * 31, "ok", "ok", "SHOUTING HEADLINE", "wow!"]),
            ads._norm_descriptions(["d" * 91]),
            "a/b",
            "toolongpathsegmentx",
            ["ftp://x"],
        )
        joined = "\n".join(problems)
        self.assertIn("Headline 1 is 31 chars", joined)
        self.assertIn("duplicates", joined)
        self.assertIn("all caps", joined)
        self.assertIn("contains '!'", joined)
        self.assertIn("descriptions given", joined)
        self.assertIn("Description 1 is 91 chars", joined)
        self.assertIn("path1 cannot contain", joined)
        self.assertIn("path2 is", joined)
        self.assertIn("final_url must start", joined)

    def test_good_copy_passes(self):
        self.assertEqual(
            ads.validate_rsa_copy(
                ads._norm_headlines(self.GOOD_H),
                ads._norm_descriptions(self.GOOD_D),
                "services", "dubai", ["https://mlabs.ae/"],
            ),
            [],
        )

    def test_create_rsa_builds_request(self):
        res = ads.create_responsive_search_ad(
            customer_id="1",
            ad_group_id="33",
            headlines=[ads.Headline(text=self.GOOD_H[0], pin="HEADLINE_1")] + self.GOOD_H[1:],
            descriptions=self.GOOD_D,
            final_urls=["https://mlabs.ae/"],
            path1="services",
        )
        aga = self.last_request.operations[0].create
        self.assertEqual(aga.ad_group, "customers/1/adGroups/33")
        self.assertEqual(aga.status.name, "ENABLED")
        self.assertEqual(list(aga.ad.final_urls), ["https://mlabs.ae/"])
        rsa = aga.ad.responsive_search_ad
        self.assertEqual(len(rsa.headlines), 3)
        self.assertEqual(rsa.headlines[0].pinned_field.name, "HEADLINE_1")
        self.assertEqual(rsa.headlines[1].pinned_field.name, "UNSPECIFIED")
        self.assertEqual(len(rsa.descriptions), 2)
        self.assertEqual(rsa.path1, "services")
        self.assertEqual(res["headline_count"], 3)

    def test_create_rsa_rejects_bad_copy_before_api(self):
        with self.assertRaises(ToolError):
            ads.create_responsive_search_ad(
                customer_id="1", ad_group_id="33",
                headlines=["only one"], descriptions=self.GOOD_D,
                final_urls=["https://mlabs.ae/"],
            )
        self.assertEqual(self.recorder.requests, [])

    def test_update_rsa(self):
        ads.update_responsive_search_ad(
            customer_id="1", ad_id="99",
            headlines=self.GOOD_H, descriptions=self.GOOD_D, path1="new",
        )
        op = self.last_request.operations[0]
        self.assertEqual(op.update.resource_name, "customers/1/ads/99")
        self.assertEqual(len(op.update.responsive_search_ad.headlines), 3)
        self.assertIn("responsive_search_ad.headlines", op.update_mask.paths)
        self.assertIn("responsive_search_ad.path1", op.update_mask.paths)
        with self.assertRaises(ToolError):
            ads.update_responsive_search_ad(customer_id="1", ad_id="99", headlines=self.GOOD_H)

    def test_ad_status_gate(self):
        res = ads.set_ad_status(customer_id="1", ad_group_id="33", ad_id="99", status="PAUSED")
        self.assertEqual(res["outcome"], "applied")
        self.assertEqual(
            self.last_request.operations[0].update.resource_name, "customers/1/adGroupAds/33~99"
        )
        res = ads.set_ad_status(customer_id="1", ad_group_id="33", ad_id="99", status="REMOVED")
        self.assertEqual(res["outcome"], "preview")


class TestAssets(WriteToolTestCase):
    def test_sitelinks_two_step(self):
        self.recorder._rns = [
            ["customers/1/assets/100", "customers/1/assets/101"],
            ["customers/1/campaignAssets/7~100~SITELINK", "customers/1/campaignAssets/7~101~SITELINK"],
        ]
        res = assets.add_sitelinks(
            customer_id="1",
            campaign_id="7",
            sitelinks=[
                assets.Sitelink(link_text="Pricing", final_url="https://mlabs.ae/pricing", description1="See plans"),
                assets.Sitelink(link_text="Contact", final_url="https://mlabs.ae/contact"),
            ],
        )
        self.assertEqual(len(self.recorder.requests), 2)
        asset_req, link_req = self.recorder.requests
        self.assertEqual(asset_req.operations[0].create.sitelink_asset.link_text, "Pricing")
        self.assertEqual(asset_req.operations[0].create.sitelink_asset.description1, "See plans")
        self.assertEqual(link_req.operations[0].create.asset, "customers/1/assets/100")
        self.assertEqual(link_req.operations[0].create.field_type.name, "SITELINK")
        self.assertEqual(link_req.operations[0].create.campaign, "customers/1/campaigns/7")
        self.assertEqual(res["ids"], ["100", "101"])

    def test_sitelinks_validation(self):
        with self.assertRaises(ToolError):
            assets.add_sitelinks(
                customer_id="1", campaign_id="7",
                sitelinks=[assets.Sitelink(link_text="x" * 26, final_url="https://a.b"),
                           assets.Sitelink(link_text="ok", final_url="https://a.b")],
            )
        self.assertEqual(self.recorder.requests, [])

    def test_sitelinks_validate_only_skips_linking(self):
        res = assets.add_sitelinks(
            customer_id="1", campaign_id="7",
            sitelinks=[assets.Sitelink(link_text="A", final_url="https://a.b"),
                       assets.Sitelink(link_text="B", final_url="https://a.b")],
            validate_only=True,
        )
        self.assertEqual(len(self.recorder.requests), 1)
        self.assertEqual(res["outcome"], "validated")

    def test_callouts(self):
        assets.add_callouts(customer_id="1", campaign_id="7", callouts=["Free Quote", "24/7 Support"])
        asset_req, link_req = self.recorder.requests
        self.assertEqual(asset_req.operations[1].create.callout_asset.callout_text, "24/7 Support")
        self.assertEqual(link_req.operations[0].create.field_type.name, "CALLOUT")
        with self.assertRaises(ToolError):
            assets.add_callouts(customer_id="1", campaign_id="7", callouts=["x" * 26, "ok"])

    def test_conversion_action(self):
        assets.create_conversion_action(
            customer_id="1", name="Lead form", category="SUBMIT_LEAD_FORM", default_value=50
        )
        ca = self.last_request.operations[0].create
        self.assertEqual(ca.type_.name, "WEBPAGE")
        self.assertEqual(ca.category.name, "SUBMIT_LEAD_FORM")
        self.assertEqual(ca.counting_type.name, "ONE_PER_CLICK")
        self.assertEqual(ca.click_through_lookback_window_days, 30)
        self.assertAlmostEqual(ca.value_settings.default_value, 50.0)
        self.assertTrue(ca.value_settings.always_use_default_value)


class TestErrorFormatting(unittest.TestCase):
    def test_google_ads_exception_becomes_tool_error(self):
        from google.ads.googleads.errors import GoogleAdsException

        client = _client()
        failure = client.get_type("GoogleAdsFailure")
        err = client.get_type("GoogleAdsError")
        err.message = "Budget too low"
        el = client.get_type("ErrorLocation").FieldPathElement()
        el.field_name = "operations"
        el.index = 0
        el2 = client.get_type("ErrorLocation").FieldPathElement()
        el2.field_name = "create"
        err.location.field_path_elements.append(el)
        err.location.field_path_elements.append(el2)
        failure.errors.append(err)
        ex = GoogleAdsException(None, None, failure, "req-1")

        service = MagicMock()
        service.mutate_campaign_budgets.side_effect = ex
        with patch("ads_mcp.utils.get_googleads_service", return_value=service):
            with self.assertRaises(ToolError) as ctx:
                m.run_mutate(
                    client, "CampaignBudgetService", "mutate_campaign_budgets",
                    "MutateCampaignBudgetsRequest", "1", [client.get_type("CampaignBudgetOperation")],
                )
        self.assertIn("Budget too low", str(ctx.exception))
        self.assertIn("req-1", str(ctx.exception))
        self.assertIn("operations[0].create", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
