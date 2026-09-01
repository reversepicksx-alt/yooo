---
name: GitHub push authentication
description: How GitHub connector authorization differs from credentials used by direct Git pushes in this workspace.
---

The GitHub API connector does not automatically authenticate HTTPS Git transport. A direct push may still fail even when connector API calls succeed.

**Why:** Reconnecting the GitHub provider left both Git and GitHub CLI unauthenticated, while the connector remained functional. Existing token aliases were stale, and a fine-grained token without effective Contents write permission authenticated but could not create Git objects.

**How to apply:** For direct pushes, use a workspace secret containing a valid repository-scoped token through a temporary Git askpass helper, never in the remote URL or logs. For this public repository, a classic token with `public_repo` permission successfully pushed the full history.