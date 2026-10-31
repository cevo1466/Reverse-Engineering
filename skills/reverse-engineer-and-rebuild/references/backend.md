# Rebuild backend behavior from observable cases

A page that calls an API needs a working backend, not hard-coded success messages. Build an evidence-to-implementation contract for every requested flow. Capture **request, response, and state after the operation**. A response that says “created” does not prove persistence; read the entity or list afterward.

## Collect a comparable case set

Use an authorized, resettable target environment and known seed data. For a stateful route, capture the relevant cases: initial read; valid write; read after write; invalid write and unchanged state; repeated request or idempotency behavior; update; delete and read after delete. Add pagination, ordering, filtering, permissions, concurrency, time behavior, and error states when they are part of the requested feature. Keep one stable case ID per observation.

For each case, record method, path, query, JSON request body where applicable, response status, selected headers, JSON response body, and the resulting state or visible side effect. Use the same sequence and seed state on the original and rebuild. Save the exact Evidence location or HTTP transcript path. Do not infer an unseen rule from a single successful call.

REA browser scenarios can record selected network metadata and bodies, or issue explicit same-origin `request` steps that use the page's session. Its [request-step contract](https://github.com/morluto/rea/blob/main/docs/browser-scenario-contract.md#request-steps) supports saved response values for later requests. Choose network capture only for the transactions needed; inspect capture completeness and redaction. REA's [environment views](https://github.com/morluto/rea/blob/main/docs/web-network-captures.md) can help inventory routes and confirmed writes, but they do not prove unseen auth rules or response computation.

Without REA, use a browser's Network panel/HAR or an ordinary HTTP client against the authorized test instance. Record the same cases and provenance. Do not send write requests to a production account merely to fill a test matrix. Remove credentials, cookies, personal data, and secret headers from saved transcripts. Avoid committing captured production data.

## Build and verify

Implement routing, validation, persistence, error semantics, and state changes in the existing server stack. Keep the user-facing flow connected to this implementation. A hard-coded response or in-memory fake is insufficient when the target persists data across requests.

The bundled comparator accepts two saved JSON transcripts. It makes no network requests:

```json
{
  "schema_version": 1,
  "cases": [
    {
      "id": "create-valid",
      "request": {"method": "POST", "path": "/api/items", "body": {"name": "Seed"}},
      "response": {"status": 201, "headers": {"content-type": "application/json"}, "body": {"id": 1, "name": "Seed"}},
      "state_after": {"items": [{"id": 1, "name": "Seed"}]}
    }
  ]
}
```

```bash
python3 <this-skill-directory>/scripts/compare_http.py reference.json rebuild.json --output NEW_COMPARISON_DIR
```

Capture only response headers relevant to the contract. The comparator matches case IDs and request identities, compares JSON values independent of object key order, and compares selected headers without regard to header-name case. Missing observations remain unknown. It writes a machine-readable difference file and Markdown report. Use deterministic seeds for generated IDs/timestamps where possible; otherwise record the proven volatile rule and compare the resulting relationships explicitly. Do not silently ignore changing fields.

After every fix, replay the affected original and rebuild cases from the same initial state. A passing case means only those recorded inputs and resulting outputs/state matched. Authentication, distributed transactions, scheduling, and hidden business rules stay `unknown` until comparable observations cover them.
