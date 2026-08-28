# TC-1001: Admin can view the user management page

Description: Confirm an administrator can reach user management after signing in.

Preconditions:
- The admin user account exists and is enabled.
- The user is logged in as an administrator.

Steps:
1. Navigate to the Users page
2. Verify the heading "User Management" is displayed

Expected results:
- The page shows "User Management"

Tags: smoke, authz
Priority: High

# TC-1002: Admin can disable an active user

Description: An administrator disables an active account and the status updates.

Preconditions:
- Logged in as an administrator.
- An existing active user is present in the list.

Steps:
1. Navigate to the Users page
2. Click the "Disable" button for an active user
3. Verify the status shows "disabled"

Expected results:
- The user's status is "disabled"

Tags: users, destructive
Priority: Medium

# TC-1003: Standard user cannot manage users

Preconditions:
- Logged in as a standard user.

Steps:
1. Navigate to the Users page
2. Verify the heading "Access denied" is displayed

Expected results:
- Access is refused
- The URL contains /users

Tags: authz, negative
