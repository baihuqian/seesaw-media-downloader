"""Client and planner tests against redacted fixtures of real Seesaw payloads."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import httpx
import pytest
import respx

from seesaw_dl.api import BASE_URL, CHILD_CLASSES, CLASS_FEED, DASHBOARD, ITEM, SeesawClient
from seesaw_dl.auth import Session
from seesaw_dl.errors import ApiContractError, AuthError, ConfigError
from seesaw_dl.logging import Reporter
from seesaw_dl.planner import Plan, build_plan, resolve_child

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


def plan_for(client: SeesawClient, reporter: Reporter, **kwargs: object) -> Plan:
    """Resolve the fixture's only child, then plan -- what the CLI does."""
    child = resolve_child(client, reporter, None)
    return build_plan(client, reporter, child, **kwargs)  # type: ignore[arg-type]


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
        plan = plan_for(client, reporter)
    assert len(plan) == 2
    assert [entry.asset.page_index for entry in plan.assets] == [0, 1]
    assert respx.calls.call_count >= 4  # dashboard, classes, feed, item


@respx.mock
def test_plan_paths_use_year_and_date(session: Session, reporter: Reporter) -> None:
    """The child's name is not in the path -- a run covers exactly one child."""
    mock_account()
    with SeesawClient(session, reporter) as client:
        plan = plan_for(client, reporter)
    path = plan.assets[0].asset.relative_path()
    assert len(path.parts) == 3  # <year>/<date>/<file>, no child folder
    assert path.parts[0] == path.parts[1][:4]  # year folder matches the date folder
    assert "Alex Rivera" not in str(path)
    assert path.name.endswith("_p1.jpg")


def mock_two_children() -> None:
    respx.get(f"{BASE_URL}{DASHBOARD}").mock(
        return_value=httpx.Response(200, json=fixture("dashboard_two_children.json"))
    )


@respx.mock
def test_single_child_is_selected_without_a_flag(session: Session, reporter: Reporter) -> None:
    mock_account()
    with SeesawClient(session, reporter) as client:
        assert resolve_child(client, reporter, None).display_name == "Alex Rivera"


@respx.mock
def test_multiple_children_without_a_flag_stops_and_lists_them(
    session: Session, reporter: Reporter
) -> None:
    """The whole point of the flag: never guess which child to download."""
    mock_two_children()
    with SeesawClient(session, reporter) as client, pytest.raises(ConfigError) as excinfo:
        resolve_child(client, reporter, None)
    message = str(excinfo.value)
    assert "--child" in message
    assert "Alex Rivera" in message and "Robin Rivera" in message


@respx.mock
@pytest.mark.parametrize(
    "token, expected",
    [
        ("Robin Rivera", "Robin Rivera"),
        ("robin rivera", "Robin Rivera"),  # case-insensitive
        ("Alex", "Alex Rivera"),  # unique substring
        ("person.aa11bb22-0000-4000-8000-000000000000", "Robin Rivera"),  # person id
    ],
)
def test_child_flag_selects_by_name_case_or_id(
    session: Session, reporter: Reporter, token: str, expected: str
) -> None:
    mock_two_children()
    with SeesawClient(session, reporter) as client:
        assert resolve_child(client, reporter, token).display_name == expected


@respx.mock
def test_unknown_child_lists_the_available_ones(session: Session, reporter: Reporter) -> None:
    mock_two_children()
    with SeesawClient(session, reporter) as client, pytest.raises(ConfigError) as excinfo:
        resolve_child(client, reporter, "Jordan")
    assert "Alex Rivera" in str(excinfo.value)


@respx.mock
def test_ambiguous_child_asks_for_a_full_name(session: Session, reporter: Reporter) -> None:
    """"Rivera" is both children's surname; guessing either would be wrong."""
    mock_two_children()
    with SeesawClient(session, reporter) as client, pytest.raises(ConfigError) as excinfo:
        resolve_child(client, reporter, "Rivera")
    assert "matches 2 children" in str(excinfo.value)


@respx.mock
def test_no_children_on_the_account_is_an_error(session: Session, reporter: Reporter) -> None:
    respx.get(f"{BASE_URL}{DASHBOARD}").mock(
        return_value=httpx.Response(
            200, json={"status": "OK", "response": {"parent": {"children": {"objects": []}}}}
        )
    )
    with SeesawClient(session, reporter) as client, pytest.raises(ConfigError):
        resolve_child(client, reporter, None)


@respx.mock
def test_plan_covers_only_the_selected_child(session: Session, reporter: Reporter) -> None:
    """Two children on the account, one child's classes fetched."""
    mock_account()
    mock_two_children()
    with SeesawClient(session, reporter) as client:
        child = resolve_child(client, reporter, "Alex")
        build_plan(client, reporter, child)
    class_calls = [c for c in respx.calls if CHILD_CLASSES in str(c.request.url)]
    assert len(class_calls) == 1
    assert child.person_id in str(class_calls[0].request.url)


@respx.mock
def test_asset_id_ignores_the_url_signature(session: Session, reporter: Reporter) -> None:
    """Signed URLs are re-issued constantly; the manifest key must survive that."""
    mock_account()
    with SeesawClient(session, reporter) as client:
        plan = plan_for(client, reporter)
    asset_id = plan.assets[0].asset.asset_id
    assert ":::" not in asset_id
    assert "SIGNATURE" not in asset_id
    assert asset_id.startswith("us-2/")


@respx.mock
def test_presence_check_marks_existing_assets(session: Session, reporter: Reporter) -> None:
    mock_account()
    with SeesawClient(session, reporter) as client:
        plan = plan_for(client, reporter, is_present=lambda asset: asset.page_index == 0)
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
        plan_for(client, reporter)
    assert all(
        "update_seen_state" not in str(call.request.url) for call in respx.calls
    )
    assert all(call.request.method == "GET" for call in respx.calls)


def _feed_page(items: list[dict], last_key: str | None) -> dict:
    return {"status": "OK", "response": {"items": {"objects": items, "last_key": last_key}}}


def _item(day: int) -> dict:
    """A single-page item posted at noon on 2026-08-<day>, local time.

    Each day gets its own asset *path*: the planner keys on the storage path, so reusing
    one path across items would (correctly) dedupe them down to a single asset.
    """
    when = datetime(2026, 8, day, 12, 0).astimezone().timestamp()
    return {
        "item": {
            "item_id": f"item.{day:08d}-0000-4000-8000-000000000000",
            "create_date": when,
            "num_pages": 1,
            "class_name": "Class",
            "pages": {
                "objects": [
                    {"composite_image_url": f"https://assets.seesaw.me/us-2/day{day}.jpg"}
                ]
            },
        }
    }


def _mock_account_with_feed(pages: list[dict]) -> None:
    respx.get(f"{BASE_URL}{DASHBOARD}").mock(
        return_value=httpx.Response(200, json=fixture("dashboard.json"))
    )
    respx.get(f"{BASE_URL}{CHILD_CLASSES}").mock(
        return_value=httpx.Response(200, json=fixture("child_classes.json"))
    )
    respx.get(f"{BASE_URL}{CLASS_FEED}").mock(
        side_effect=[httpx.Response(200, json=page) for page in pages]
    )


@respx.mock
def test_since_filters_out_older_items(session: Session, reporter: Reporter) -> None:
    _mock_account_with_feed([_feed_page([_item(25), _item(20), _item(10)], None)])
    cutoff = datetime(2026, 8, 18).astimezone()
    with SeesawClient(session, reporter) as client:
        plan = plan_for(client, reporter, since=cutoff)
    assert len(plan) == 2
    assert all(entry.asset.created_at >= cutoff for entry in plan.assets)


@respx.mock
def test_since_boundary_is_inclusive(session: Session, reporter: Reporter) -> None:
    """A post exactly at the cutoff instant belongs in the window."""
    _mock_account_with_feed([_feed_page([_item(20)], None)])
    cutoff = datetime(2026, 8, 20, 12, 0).astimezone()
    with SeesawClient(session, reporter) as client:
        plan = plan_for(client, reporter, since=cutoff)
    assert len(plan) == 1


@respx.mock
def test_pagination_stops_once_a_whole_page_predates_the_cutoff(
    session: Session, reporter: Reporter
) -> None:
    """The point of --since: don't walk years of history to find nothing."""
    _mock_account_with_feed(
        [
            _feed_page([_item(25), _item(24)], "cursor-1"),
            _feed_page([_item(5), _item(4)], "cursor-2"),
            _feed_page([_item(3)], "cursor-3"),  # must never be requested
        ]
    )
    cutoff = datetime(2026, 8, 20).astimezone()
    with SeesawClient(session, reporter) as client:
        plan = plan_for(client, reporter, since=cutoff)
    assert len(plan) == 2
    feed_calls = [c for c in respx.calls if str(CLASS_FEED) in str(c.request.url)]
    assert len(feed_calls) == 2  # stopped after the first fully-old page


@respx.mock
def test_out_of_order_item_does_not_truncate_the_run(
    session: Session, reporter: Reporter
) -> None:
    """A back-dated or pinned post must not end pagination early."""
    _mock_account_with_feed(
        [
            _feed_page([_item(25), _item(1), _item(24)], "cursor-1"),
            _feed_page([_item(23)], None),
        ]
    )
    cutoff = datetime(2026, 8, 20).astimezone()
    with SeesawClient(session, reporter) as client:
        plan = plan_for(client, reporter, since=cutoff)
    days = sorted(entry.asset.created_at.day for entry in plan.assets)
    assert days == [23, 24, 25]  # the old item skipped, the run continued


@respx.mock
def test_no_since_walks_the_whole_feed(session: Session, reporter: Reporter) -> None:
    _mock_account_with_feed(
        [_feed_page([_item(25)], "cursor-1"), _feed_page([_item(1)], None)]
    )
    with SeesawClient(session, reporter) as client:
        plan = plan_for(client, reporter)
    assert len(plan) == 2
