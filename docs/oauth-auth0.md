# Historical OAuth design notes (superseded)

The implementation and active operator instructions are now consolidated in
[Auth0 OAuth for isolated WFC MCP](auth0-oauth.md).

**Do not follow old DCR instructions.** The POC Auth0 tenant now uses ChatGPT
Client ID Metadata Document (CIMD) registration with DCR OFF. Both the user
ID (`WFC_AUTH0_ALLOWED_SUB`) and the exact CIMD client ID
(`WFC_AUTH0_CLIENT_ID`) are required by the verifier. The legacy Bearer MCP
remains running until operator tests and an explicit tunnel cutover.

No actual Auth0 login, Docker acceptance test or full pytest run has been
verified for the current branch in this chat.
