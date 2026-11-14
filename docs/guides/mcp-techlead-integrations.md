
# Tech Lead MCP integrations

The existing FastMCP server registers six additional tools in Auth0 mode. Its
protected-resource metadata advertises the configured workflow scope and
`wfc:techlead`. Keep `WFC_AUTH0_SCOPE` set to a single workflow permission,
normally `wfc:workflow`. A workflow-only access token still works for the nine
legacy tools. The Tech Lead tools require both scopes in the signed access
token's `scope` claim. Bearer and anonymous modes expose the original nine tools.

Grant both permissions to the authorized subject and existing ChatGPT client
for the same Auth0 MCP audience. After deploying this change, reconnect the
ChatGPT MCP connection to start a fresh OAuth authorization flow. A cached
workflow-only grant may still serve legacy calls. During the separately
authorized E2E check, verify that the fresh access token includes both scopes
without copying or logging the token. A permission grant in the Auth0 console
alone does not verify what the client requested or what Auth0 issued.

Each integration loads its own settings after the Auth0 check. Unset or invalid
settings disable calls for that integration. Store each credential in a
server-readable, read-only secret mount outside every Git checkout. Limit
filesystem access to the MCP service account.

## GitHub App

Use a new dedicated Tech Lead App and installation for
`builacviet/agent-workflow`. Grant repository **Metadata: read** and **Pull
requests: write** only. Install it only on this repository. Keep the App and its
key separate from the Controller, Coder Worker, and Owner connections.

| Setting | Value |
| --- | --- |
| `TECHLEAD_GITHUB_APP_ID` | Numeric GitHub App ID |
| `TECHLEAD_GITHUB_INSTALLATION_ID` | Numeric installation ID for this repository |
| `TECHLEAD_GITHUB_APP_SLUG` | App slug without the `[bot]` suffix |
| `TECHLEAD_GITHUB_BOT_ID` | Numeric ID of the App's bot user |
| `TECHLEAD_GITHUB_KEY_FILE` | Absolute path to the mounted App PEM |
| `TECHLEAD_GITHUB_TEST_PRS` | Comma-separated IDs of explicitly approved test PRs. Empty disables review writes. |

`tl_github_identity` checks the App, installation, scoped token and associated
bot account. `tl_github_get_pr(pr_number)` returns minimal PR metadata and
HEAD. `tl_github_comment_review(pr_number, expected_head_sha, body)` re-reads
the open test PR and submits a `COMMENT` review pinned to that commit. A
concurrent push after the HEAD read can make the review outdated. The review
remains pinned to the requested commit.

## Notion integration

Use a dedicated internal workspace integration. Grant **Read content** and
**Insert comments** capabilities, and share only the authorized test/task pages.
Use a token mount unique to this bot.

| Setting | Value |
| --- | --- |
| `TECHLEAD_NOTION_BOT_ID` | Exact bot user UUID reported by `/v1/users/me` |
| `TECHLEAD_NOTION_TOKEN_FILE` | Absolute path to the mounted integration token |
| `TECHLEAD_NOTION_TASK_PAGES` | Comma-separated UUIDs of shared task pages |
| `TECHLEAD_NOTION_TEST_PAGES` | Comma-separated subset of task UUIDs that allow comments. Empty disables comment writes. |

`tl_notion_identity` checks the workspace bot against the configured UUID.
`tl_notion_get_task(page_id)` returns a title and text from up to 20 top-level
blocks and marks clipped or omitted content. `tl_notion_comment(page_id, body)`
adds a page-level comment on an allowlisted test page. Both write tools accept
1–2,000 characters.

## Separately authorized end-to-end verification

After the Owner merges and separately authorizes a test under
[OPS-TL-ID-01](https://app.notion.com/p/418f92014abd815c961de5bda67b7536):

1. Verify both identity tools and read an approved test PR and shared task page.
   Confirm that a workflow-only OAuth token fails the Tech Lead scope check.
2. Use an Owner-designated open test PR and Notion test page. Submit a single
   review pinned to the just-read HEAD and a single page comment with known
   test text.
3. Re-read the two artifacts independently. Confirm the actual GitHub App
   bot login, numeric ID, review state and commit, and the Notion comment's
   recorded creator bot ID. Record links and actor evidence in the parent task.
4. If a write returns `BLOCKED`, inspect the destination for the intended
   artifact and verify its author before considering another attempt. The
   server sends each POST at most once per call.

Provider references: [GitHub App tokens](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app),
[GitHub PR reviews](https://docs.github.com/en/rest/pulls/reviews?apiVersion=2022-11-28#create-a-review-for-a-pull-request),
[Notion bot identity](https://developers.notion.com/reference/get-self), and
[Notion comments](https://developers.notion.com/reference/create-a-comment).
