@checkout @regression
Feature: Customer checkout

  @smoke
  Scenario: Signed-in customer reaches the dashboard
    Given the customer is logged in
    When they navigate to the Dashboard page
    Then the page shows "Orders today"

  Scenario: Customer cannot open admin settings
    Given the customer is logged in
    When they navigate to the Users page
    Then the page shows "Access denied"
    And the URL contains /users
