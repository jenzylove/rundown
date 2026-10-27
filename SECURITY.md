# Security

Please report a vulnerability privately through GitHub's
[security advisory form](https://github.com/jenzylove/rundown/security/advisories/new), not in a public issue.

What the live app already does:

- Only the browser that started a match can skip it, restart the second half or switch agents off (an owner token per match).
- New matches and heavy endpoints are rate limited per address, and each address can have at most six matches in play.
- The MCP server checks the Host header against the app's own hostname.
- Keys live in Azure Container Apps secrets and GitHub Actions secrets, never in the repo.

The data is synthetic: fictional clubs and players, no personal data is stored.
