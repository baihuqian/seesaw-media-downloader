"""Client and planner tests against redacted fixtures of real Seesaw payloads."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from seesaw_dl.api import BASE_URL, CHILD_CLASSES, CLASS_FEED, DASHBOARD, ITEM, SeesawClient
from seesaw_dl.auth import Session
from seesaw_dl.errors import ApiContractError, AuthError
from seesaw_dl.logging import Reporter
from seesaw_dl.planner import build_plan

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def session() -> Session:
    return Session(
        storage_state={
            "cookies": [{"name": "authToken", "value": "tok", "domain": "app.seesaw.me"}]
        },
        xsrf="xsrf-token",
        email="family@example.com",
        release="prod_2026-08-25.3",
    )


@pytest.fixture
def reporter() -> Reporter:
    return Reporter()


def mock_account() -> None:
    respx.get(f"{BASE_URL}{DASHBOARD}").mock(
        return_value=httpx.Response(200, json=fixture("dashboard.json"))
    )
    respx.get(f"{BASE_URL}{CHILD_CLASSES}").mock(
        return_value=httpx.Response(200, json=fixture("child_classes.json"))
    )
    respx.get(f"{BASE_URL}{CLASS_FEED}").mock(
        return_value=httpx.Response(200, json=fixture("class_feed.json"))
    )
    respx.get(f"{BASE_URL}{ITEM}").mock(
        return_value=httpx.Response(200, json=fixture("item_v2.json"))
    )


@respx.mock
def test_children_and_classes(session: Session, reporter: Reporter) -> None:
    mock_account()
    with SeesawClient(session, reporter) as client:
        children = client.children()
        assert [c.display_name for c in children] == ["Alex Rivera"]
        classes = client.classes(children[0])
        assert [c.name for c in classes] == ["SY26-27 - HOMEROOM - Nguyen - B101XY"]


@respx.mock
def test_request_carries_xsrf_and_release(session: Session, reporter: Reporter) -> None:
    route = respx.get(f"{BASE_URL}{DASHBOARD}").mock(
        return_value=httpx.Response(200, json=fixture("dashboard.json"))
    )
    with SeesawClient(session, reporter) as client:
        client.children()
    params = route.calls.last.request.url.params
    assert params["_xsrf"] == "xsrf-token"
    assert params["_release"] == "prod_2026-08-25.3"
    assert params["_bundle"] == "me.see-saw.web_magiccam"


@respx.mock
def test_multipage_item_is_refetched(session: Session, reporter: Reporter) -> None:
    """The feed sends one page for a two-page item; the plan must still get both."""
    mock_account()
    with SeesawClient(session, reporter) as client:
        plan = build_plan(client, reporter)
    assert len(plan) == 2
    assert [entry.asset.page_index for entry in plan.assets] == [0, 1]
    assert respx.calls.call_count >= 4  # dashboard, classes, feed, item


@respx.mock
def test_plan_paths_use_child_year_and_date(session: Session, reporter: Reporter) -> None:
    mock_account()
    with SeesawClient(session, reporter) as client:
        plan = build_plan(client, reporter)
    path = plan.assets[0].asset.relative_path()
    assert path.parts[0] == "Alex Rivera"
    assert path.parts[1] == path.parts[2][:4]  # year folder matches the date folder
    assert path.name.endswith("_p1.jpg")


@respx.mock
def test_asset_id_ignores_the_url_signature(session: Session, reporter: Reporter) -> None:
    """Signed URLs are re-issued constantly; the manifest key must survive that."""
    mock_account()
    with SeesawClient(session, reporter) as client:
        plan = build_plan(client, reporter)
    asset_id = plan.assets[0].asset.asset_id
    assert ":::" not in asset_id
    assert "SIGNATURE" not in asset_id
    assert asset_id.startswith("us-2/")


@respx.mock
def test_presence_check_marks_existing_assets(session: Session, reporter: Reporter) -> None:
    mock_account()
    with SeesawClient(session, reporter) as client:
        plan = build_plan(client, reporter, is_present=lambda asset: asset.page_index == 0)
    assert plan.present_count == 1
    assert len(plan.to_download) == 1


@respx.mock
def test_rejected_session_is_reported_clearly(session: Session, reporter: Reporter) -> None:
    respx.get(f"{BASE_URL}{DASHBOARD}").mock(return_value=httpx.Response(403))
    with SeesawClient(session, reporter) as client, pytest.raises(AuthError) as excinfo:
        client.children()
    assert "seesaw-dl login" in str(excinfo.value)


@respx.mock
def test_changed_payload_raises_contract_error(session: Session, reporter: Reporter) -> None:
    respx.get(f"{BASE_URL}{DASHBOARD}").mock(
        return_value=httpx.Response(200, json={"status": "OK", "response": {"nope": 1}})
    )
    with SeesawClient(session, reporter) as client, pytest.raises(ApiContractError) as excinfo:
        client.children()
    assert "parent" in str(excinfo.value)


@respx.mock
def test_never_marks_items_as_seen(session: Session, reporter: Reporter) -> None:
    """Browsing in the web app marks posts read; downloading must not."""
    mock_account()
    with SeesawClient(session, reporter) as client:
        build_plan(client, reporter)
    assert all(
        "update_seen_state" not in str(call.request.url) for call in respx.calls
    )
    assert all(call.request.method == "GET" for call in respx.calls)
