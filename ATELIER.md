# Atelier's carry on authentik

`atelier/main` is authentik `version/2026.2.1` with the fixes below merged
in, one branch each. The image Atelier runs copies in the Python files
`atelier/main` changes against the release tag
(`infrastructure/k8s/tools/authentik` in Atelier's repository). Nothing
here goes upstream.

## Branches

Each branch is cut from `version/2026.2.1`, carries one fix and its test in
authentik's own layout, and is merged into `atelier/main` with `--no-ff`.

| Branch | Why it exists | Test |
| --- | --- | --- |
| `fix/scim-source-namespaces` | A SCIM source resolved users and groups by global name, so one source could take over, deactivate or delete users and groups it did not create. A source now resolves through its own connections only, names what it creates `<slug>.<name>`, keeps the client's names in responses, and a migration holds each user and group to one source. | `sources/scim/tests/test_namespaces.py` |
| `fix/scim-user-conflict-message` | The user endpoint's 409 said "Group with ID exists already." | `sources/scim/tests/test_user_conflict_message.py` |
| `fix/scim-source-member-scoping` | Group members resolved among every user of the instance, so a source could seat users it did not create. | `sources/scim/tests/test_member_scoping.py` |
| `fix/scim-patch-deep-copy` | A PATCH to a nested or multi-valued attribute answered 200 and changed nothing. | `sources/scim/tests/test_patch_nested.py` |
| `fix/scim-group-member-filter-remove` | Removing a member by `members[value eq "<id>"]` removed nobody. | `sources/scim/tests/test_group_member_filter_remove.py` |
| `fix/scim-filters` | `externalId eq` and unparsable filters answered 500, and an unknown attribute widened an `and`. | `sources/scim/tests/test_filters.py` |
| `fix/scim-never-returned` | A password sent by the client was stored and returned. | `sources/scim/tests/test_never_returned.py` |
| `fix/scim-error-responses` | Errors under a source rendered the HTML error page. | `sources/scim/tests/test_error_responses.py` |
| `fix/scim-provider-static-scope` | The SCIM provider ran the policy engine once per user of the instance on every page of a full sync. The query is held to the engine's answer user by user over generated binding sets; like the engine, an application with no bindings passes everyone, so a provider meant for one directory sets `group_scope_root`. | `providers/scim/tests/test_static_scope.py` |
| `fix/scim-source-token-scope` | A SCIM source's token authenticated every endpoint of the API as the source's service account, and let a client set the IP authentik records for it and pass as an outpost. | `sources/scim/tests/test_token_scope.py` |
| `fix/scim-provider-full-sync-queue` | A change to one user or group waited behind a full sync's queued pages, for most of the sync. | `providers/scim/tests/test_sync_queue.py` |
| `fix/outgoing-sync-rerun` | A full sync requested while another ran, as a provider save starts one, was dropped. | `providers/scim/tests/test_sync_rerun.py` |
| `fix/scim-group-members-replace` | A PATCH replacing a group's members, a pathless add or replace naming members, and a remove naming no member were dropped, so a member a directory removed kept the group; a PUT naming no member left every member seated. | `sources/scim/tests/test_group_member_writes.py` |
| `fix/scim-source-group-seating` | Nothing seated a SCIM source's users in a group, and a provider's group filters could not reach the groups a source creates. A source can name a `group` to seat its users in and nest its groups under, never one that is a superuser group, holds a role, or sits below either; a provider can name a `group_scope_root` to sync its descendants and their members. | `sources/scim/tests/test_group_seating.py`, `providers/scim/tests/test_group_scope_root.py` |
| `fix/blueprint-apply-once` | The worker applied every blueprint at once, twice or more on a fresh install (a discovery per process start), and a meta apply applied its target once more beside the target's own task, so applications writing the same rows deadlocked and ended in error until the next hourly discovery. Applications in a tenant now take turns on an advisory lock; an apply a file's change requested, and a meta apply, skip a blueprint applied at its current content, and a meta apply records the one it applies. | `blueprints/tests/test_apply_once.py` |
| `fix/outgoing-sync-no-wait` | A full sync held its worker thread while it waited for its pages, which need threads of their own, so as many full syncs as the worker has threads stopped every task, deprovisioning included, for 45 minutes or more. A full sync now takes a lease in the cache, dispatches its users' pages and ends. Each page settles in a `finally`, counted once under the provider's transaction-level lock; the last users' page dispatches the groups', the last of those releases the lease, and the lease expires at the full sync's time limit. On `atelier/main` a full sync that finds it held asks for a rerun (`fix/outgoing-sync-rerun`), which the release dispatches. | `providers/scim/tests/test_sync_no_wait.py` |
| `fix/log-capture-per-context` | `capture_logs` swapped the process's log processors for the length of a capture, so on a worker's threads a capture took what other threads logged and a capture closing first emptied a later one: a blueprint apply that failed validation beside another task recorded none of its warnings, and a capture closing last could drop the process's logs from then on. A capture is now a context variable, and one processor hands an entry to the innermost capture of the context that logged it. | `events/tests/test_capture_logs.py` |
| `fix/oauth-source-partial-update` | A PATCH of an OAuth source naming no `provider_type` raised a KeyError in the serializer's validate, and one naming no URL its type needs was refused although the source had it. What a source took from its discovery document now stands while the document does: an update keeping the document fills only what neither it nor the source sets, from the source's own well-known and JWKS URLs, and an update moving a source to another document takes each field and the keys from that document unless it names them. | `sources/oauth/tests/test_partial_update.py` |
| `fix/saml-source-issuer-default` | A SAML source made without an issuer, which is optional and defaults to the metadata URL, failed on save: the column takes no null and the default was None. | `sources/saml/tests/test_issuer_default.py` |
| `fix/oauth-source-pkce-discovery` | OIDC discovery copied the IdP's list of PKCE methods into the source's one method, so the code exchange failed without a word. The source takes S256, else plain, else none, as one of the document's fields, filled by the rule the endpoints are. | `sources/oauth/tests/test_discovery_pkce.py` |

## Moving to the next release

1. Rebase each `fix/*` branch onto the new release tag, resolve, and run
   its test on the branch: it fails on the tag and passes on the branch.
2. **The migrations:** each branch's migration depends on the newest migration
   of its app the release ships; point it at the new release's newest one.
   They are `sources/scim/migrations/fork_0001_one_source_per_object.py`,
   `sources/scim/migrations/fork_0002_scimsource_group.py` and
   `providers/scim/migrations/fork_0001_scimprovider_group_scope_root.py`.
   `atelier/main` joins the two source migrations in
   `sources/scim/migrations/fork_0003_merge.py`, so each app keeps one leaf.
3. Merge the new tag into `atelier/main`, then each rebased branch with
   `--no-ff`, each conflict resolved to the branch. `atelier/main` is never
   rebuilt or force-pushed: the image records the commit it was built from.
4. Regenerate the image's patches from the new `atelier/main` and run its
   suite in Atelier's repository (`infrastructure/k8s/tools/authentik`,
   `regen.py` then `tests/run.py matrix`) before the image moves.
