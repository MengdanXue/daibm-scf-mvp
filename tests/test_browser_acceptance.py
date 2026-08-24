from __future__ import annotations

from scripts.browser_acceptance import (
    _select_application,
    _wait_for_created_application,
)


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
