from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts.browser_acceptance import (
    _select_application,
    _wait_for_created_application,
    _is_expected_empty_calibration_error,
)


@pytest.mark.parametrize(
    "path,status,code,kind,text,expected",
    [
        ("calibration-deployments/active", 404, "outcome_not_found", "error", "404", True),
        ("calibration-deployments/active", 500, "outcome_not_found", "error", "404", False),
        ("calibration-deployments/active", 404, "unexpected", "error", "404", False),
        ("applications", 404, "outcome_not_found", "error", "404", False),
        ("calibration-deployments/active", 404, "outcome_not_found", "pageerror", "404", False),
        ("calibration-deployments/active", 404, "outcome_not_found", "error", "500", False),
    ],
)
def test_only_confirmed_optional_calibration_empty_state_is_expected(
    path, status, code, kind, text, expected
):
    url = f"http://127.0.0.1:8010/api/v1/{path}"
    message = (kind, f"Failed to load resource: the server responded with a status of {text} (Not Found)", url)
    response = SimpleNamespace(
        url=url, status=status, json=lambda: {"detail": {"code": code}}
    )
    assert _is_expected_empty_calibration_error(message, [response]) is expected
    assert not _is_expected_empty_calibration_error(message, [])


class _WorkflowPage:
    def __init__(self) -> None:
        self.selected_id = "old-returned-id"
        self.created_id = "new-created-id"
        self.created_contract = "SCF-ACCEPTANCE-NEW"
        self.events: list[str] = []

    def locator(self, selector: str, *, has_text: str | None = None):
        return _WorkflowLocator(self, selector, has_text)


class _WorkflowLocator:
    def __init__(
        self,
        page: _WorkflowPage,
        selector: str,
        has_text: str | None = None,
    ) -> None:
        self.page = page
        self.selector = selector
        self.has_text = has_text

    def filter(self, *, has_text: str):
        return _WorkflowLocator(self.page, self.selector, has_text)

    def wait_for(self) -> None:
        self.page.events.append(f"wait:{self.selector}:{self.has_text}")
        if self.selector == "#workflowDetail .detail-top h2":
            assert self.has_text == self.page.created_contract
            self.page.selected_id = self.page.created_id
        if self.selector == "#workflowDetail .detail-top p":
            assert self.has_text == self.page.selected_id

    def inner_text(self) -> str:
        self.page.events.append(f"text:{self.selector}")
        assert self.selector == "#workflowDetail .detail-top p"
        return self.page.selected_id

    def click(self) -> None:
        self.page.events.append(f"click:{self.selector}")
        prefix = '[data-application-id="'
        assert self.selector.startswith(prefix) and self.selector.endswith('"]')
        self.page.selected_id = self.selector[len(prefix) : -2]


def test_created_application_waits_for_the_new_contract_before_capturing_id():
    page = _WorkflowPage()

    application_id = _wait_for_created_application(
        page,
        page.created_contract,
    )

    assert application_id == page.created_id
    assert page.events == [
        "wait:#workflowDetail .detail-top h2:SCF-ACCEPTANCE-NEW",
        "text:#workflowDetail .detail-top p",
    ]


def test_cross_role_selection_clicks_the_exact_application_before_asserting():
    page = _WorkflowPage()

    _select_application(page, page.created_id)

    assert page.selected_id == page.created_id
    assert page.events == [
        'wait:[data-application-id="new-created-id"]:None',
        'click:[data-application-id="new-created-id"]',
        "wait:#workflowDetail .detail-top p:new-created-id",
    ]
