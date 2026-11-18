# Trusted HTTPS provider transport

V1-02E adds an inert Python transport module for the generic
`chat_completions` and `responses` request formats. It is intended to run inside
the trusted Linux-host Controller process after the separately approved runtime
wiring work. This guide describes a library boundary only; it does not activate
a Controller, service, container, or provider call.

## Call contract

The trusted host first loads an operator-approved V1-02C `RoleProfiles` value
and an approved task context, then calls V1-02D `encode_request`. The resulting
`ProtocolRequest` is passed to `integrations.provider_http.post_json` with:

- the selected provider's exact approved HTTPS base URL;
- the request's fixed `/chat/completions` or `/responses` path and JSON body;
- the corresponding `secret://` reference;
- a secret resolver callback supplied by the trusted host; and
- the trusted host's V1-02C approved endpoint set.

The transport rechecks exact endpoint membership, HTTPS scheme, host, port,
absence of URL credentials/query/fragment, protocol/path agreement, request
JSON, and secret-reference shape before I/O. It resolves the key only at
invocation. It uses Python's certificate-verifying HTTPS connection directly,
so it does not discover or inherit environment proxy settings. Redirects are
never followed, POST retries are not performed, and connect/read timeouts and
request/response size limits are explicit. Responses must be successful UTF-8
JSON; transport errors return a small category without unsafe provider text.

The bounded response text is passed to V1-02D `normalize_response` with the
trusted schema and tool allowlist. Normalized tool calls remain data. This
transport never dispatches or executes them.

## Trust boundaries

The trusted host owns endpoint approval, role/task selection, the endpoint
allowlist, and the injected secret resolver. Untrusted task instructions,
model output, and provider response fields cannot select a URL, HTTP method,
path, secret reference, or credential. Do not pass model-generated endpoint
data to this module. Do not log authorization headers or resolved key values.

`approved_endpoints` is a trusted input, not a user- or model-controlled list.
Callers must supply the V1-02C-validated allowlist and selected provider config;
the transport's URL checks are defense in depth, not a replacement for role
preflight.

## Limits and deployment expectations

The implementation and its tests use injected fake connections and fake
secrets. Tests prove request construction, validation, one-attempt behavior,
response bounds, and sanitized error handling. They do not prove reachability,
DNS/IP egress restrictions, certificate behavior against a live server, or
production secret-store behavior. No real API key, live provider request,
Controller/Worker wiring, Docker/systemd deployment, or runtime activation is
part of V1-02E.

Future trusted-host wiring must preserve the injected resolver and operator
allowlist, apply host-level network egress controls, and separately obtain
approval for real credentials and provider conformance testing. Those steps
are not enabled by this module.
